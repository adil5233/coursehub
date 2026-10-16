import asyncio
import json
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from concurrent.futures import Future
from pathlib import Path

from ..config import CACHE_DIR, TELEGRAM_CONFIG, TELEGRAM_SESSION
from ..models import ScannedVideo
from ..scanner.telegram_scanner import scan_channel

# Telegram requires offsets divisible by the request size, and the size must divide 1 MB.
CHUNK = 512 * 1024
READ_AHEAD = 8
MAX_PARALLEL = 3  # read-ahead only; chunks VLC is waiting for skip this limit
CACHE_CHUNKS = 160  # ~80 MB in memory
DISK_CACHE_LIMIT = 2 * 1024 ** 3
MESSAGE_TTL_S = 30 * 60  # file references inside messages expire after a while


class TelegramService:
    """Runs a Telethon client on its own asyncio loop; all public methods are blocking and thread-safe."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="telegram", daemon=True)
        self._thread.start()
        self.client = None
        self.is_authorized = False
        self._messages: dict[tuple[int, int], tuple[float, object]] = {}
        self._lock = threading.RLock()  # Future callbacks may run synchronously while it is held
        self._chunks: OrderedDict[tuple[int, int, int], bytes] = OrderedDict()
        self._pending: dict[tuple[int, int, int], Future] = {}
        self._semaphore: asyncio.Semaphore | None = None
        self._disk = cache_dir or CACHE_DIR / "tg"
        self._disk.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._prune_disk, name="tg-cache-prune", daemon=True).start()

    def _run_loop(self) -> None:
        try:
            self._loop.run_forever()
        except (asyncio.InvalidStateError, OSError):
            pass  # Windows proactor races with socket teardown on shutdown; nothing left to clean up

    def _run(self, coro, timeout: float | None = 120):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    # ---- config ----
    @staticmethod
    def load_config() -> tuple[int, str] | None:
        try:
            data = json.loads(TELEGRAM_CONFIG.read_text(encoding="utf-8"))
            return int(data["api_id"]), str(data["api_hash"])
        except (OSError, ValueError, KeyError):
            return None

    @staticmethod
    def save_config(api_id: int, api_hash: str) -> None:
        TELEGRAM_CONFIG.write_text(json.dumps({"api_id": api_id, "api_hash": api_hash}), encoding="utf-8")

    @staticmethod
    def clear_config() -> None:
        TELEGRAM_CONFIG.unlink(missing_ok=True)

    # ---- login ----
    def connect(self) -> bool:
        if self.client is None:
            config = self.load_config()
            if config is None:
                raise RuntimeError("Telegram API ID / hash are not set.")
            self._run(self._create(*config))
        self.is_authorized = self._run(self._connect())
        return self.is_authorized

    async def _create(self, api_id: int, api_hash: str) -> None:
        from telethon import TelegramClient

        self.client = TelegramClient(str(TELEGRAM_SESSION), api_id, api_hash)

    async def _connect(self) -> bool:
        if not self.client.is_connected():
            await self.client.connect()
        return await self.client.is_user_authorized()

    def send_code(self, phone: str) -> None:
        self._run(self.client.send_code_request(phone))

    def sign_in(self, phone: str, code: str) -> bool:
        """Returns True when a two-step verification password is still required."""
        from telethon.errors import SessionPasswordNeededError

        try:
            self._run(self.client.sign_in(phone=phone, code=code))
        except SessionPasswordNeededError:
            return True
        self.is_authorized = True
        return False

    def sign_in_password(self, password: str) -> None:
        self._run(self.client.sign_in(password=password))
        self.is_authorized = True

    # ---- data ----
    def list_channels(self) -> list[tuple[int, str]]:
        return self._run(self._dialogs())

    async def _dialogs(self) -> list[tuple[int, str]]:
        result = []
        async for d in self.client.iter_dialogs():
            if d.is_channel or d.is_group:
                result.append((d.id, d.name))
            elif getattr(d.entity, "is_self", False):
                result.append((d.id, "Saved Messages"))
            elif getattr(d.entity, "bot", False):
                result.append((d.id, f"{d.name} (bot)"))
            elif d.is_user and not getattr(d.entity, "deleted", False):
                result.append((d.id, d.name or "Unknown user"))
        return result

    def scan(self, chat_id: int) -> list[ScannedVideo]:
        return self._run(scan_channel(self.client, chat_id), timeout=None)

    def get_message(self, chat_id: int, msg_id: int):
        key = (chat_id, msg_id)
        with self._lock:
            cached = self._messages.get(key)
            if cached and time.monotonic() - cached[0] < MESSAGE_TTL_S:
                return cached[1]
        msg = self._run(self.client.get_messages(chat_id, ids=msg_id))
        if msg is None or msg.file is None:
            raise FileNotFoundError(f"Telegram message {msg_id} has no file (deleted?).")
        with self._lock:
            self._messages[key] = (time.monotonic(), msg)
        return msg

    def download_media(self, chat_id: int, msg_id: int, target: Path) -> None:
        msg = self.get_message(chat_id, msg_id)
        self._run(self.client.download_media(msg, file=str(target)))

    def warm_messages(self, chat_id: int, msg_ids: list[int]) -> None:
        """Fetch many messages in batches of 100 instead of one request each."""
        now = time.monotonic()
        with self._lock:
            missing = [m for m in msg_ids if (chat_id, m) not in self._messages
                       or now - self._messages[(chat_id, m)][0] >= MESSAGE_TTL_S]
        for start in range(0, len(missing), 100):
            messages = self._run(self.client.get_messages(chat_id, ids=missing[start:start + 100]))
            fetched = time.monotonic()
            with self._lock:
                for msg in messages:
                    if msg is not None and msg.file is not None:
                        self._messages[(chat_id, msg.id)] = (fetched, msg)

    def download_thumb(self, chat_id: int, msg_id: int, target: Path) -> bool:
        msg = self.get_message(chat_id, msg_id)
        if not getattr(msg.document, "thumbs", None):
            return False
        saved = self._run(self.client.download_media(msg, file=str(target), thumb=-1))
        return bool(saved) and Path(saved).is_file()

    def iter_range(self, chat_id: int, msg_id: int, start: int, end: int) -> Iterator[bytes]:
        """Yield bytes start..end (inclusive) of the message's file, reading ahead in parallel."""
        msg = self.get_message(chat_id, msg_id)
        first, last = start // CHUNK, end // CHUNK
        served = 0
        for index in range(first, last + 1):
            needed = self._chunk(chat_id, msg_id, msg, index, urgent=True)
            # Read ahead only once VLC is clearly streaming sequentially, so header/seek probes stay cheap.
            for ahead in range(index + 1, min(index + min(served, READ_AHEAD), last) + 1):
                self._chunk(chat_id, msg_id, msg, ahead)
            data = needed.result(timeout=180)
            served += 1
            low = start - index * CHUNK if index == first else 0
            high = end - index * CHUNK + 1 if index == last else len(data)
            yield data[low:high]

    def prefetch(self, chat_id: int, msg_id: int, fraction: float = 0.0) -> None:
        """Warm up the file's DC connection and fetch header, tail and resume-point chunks."""
        msg = self.get_message(chat_id, msg_id)
        last = max(0, (msg.file.size - 1) // CHUNK)
        resume = min(last, int(msg.file.size * max(0.0, min(fraction, 1.0))) // CHUNK)
        for index in sorted({0, last, resume}):
            self._chunk(chat_id, msg_id, msg, index, urgent=True)

    def _chunk(self, chat_id: int, msg_id: int, msg, index: int, urgent: bool = False) -> Future:
        key = (chat_id, msg_id, index)
        with self._lock:
            data = self._chunks.get(key)
            if data is None:
                data = self._read_disk(key)
                if data is not None:
                    self._chunks[key] = data
            if data is not None:
                self._chunks.move_to_end(key)
                done: Future = Future()
                done.set_result(data)
                return done
            future = self._pending.get(key)
            if future is None:
                future = asyncio.run_coroutine_threadsafe(self._fetch(msg, index, urgent), self._loop)
                self._pending[key] = future
                future.add_done_callback(lambda f, key=key: self._store(key, f))
            return future

    def _store(self, key: tuple[int, int, int], future: Future) -> None:
        with self._lock:
            self._pending.pop(key, None)
            if future.cancelled() or future.exception() is not None:
                self._messages.pop(key[:2], None)  # force a fresh file reference on retry
                return
            data = future.result()
            self._chunks[key] = data
            while len(self._chunks) > CACHE_CHUNKS:
                self._chunks.popitem(last=False)
        if data:
            self._write_disk(key, data)

    # ---- disk cache ----
    def clear_memory_cache(self) -> None:
        with self._lock:
            self._chunks.clear()

    def _disk_path(self, key: tuple[int, int, int]) -> Path:
        chat_id, msg_id, index = key
        return self._disk / f"{abs(chat_id)}_{msg_id}" / f"{index}.bin"

    def _read_disk(self, key: tuple[int, int, int]) -> bytes | None:
        path = self._disk_path(key)
        try:
            data = path.read_bytes()
            os.utime(path)  # mtime doubles as LRU timestamp for pruning
        except OSError:
            return None
        return data or None

    def _write_disk(self, key: tuple[int, int, int], data: bytes) -> None:
        path = self._disk_path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(f".{threading.get_ident()}.tmp")
            temp.write_bytes(data)
            temp.replace(path)
        except OSError:
            pass  # cache is best-effort

    def _prune_disk(self) -> None:
        files = []
        for path in self._disk.glob("*/*"):
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((stat.st_mtime, stat.st_size, path))
        total = sum(size for _, size, _ in files)
        for _, size, path in sorted(files):
            if total <= DISK_CACHE_LIMIT * 0.8:
                break
            path.unlink(missing_ok=True)
            total -= size

    async def _fetch(self, msg, index: int, urgent: bool) -> bytes:
        if urgent:
            return await self._download(msg, index)
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(MAX_PARALLEL)
        async with self._semaphore:
            return await self._download(msg, index)

    async def _download(self, msg, index: int) -> bytes:
        async for chunk in self.client.iter_download(msg.media, offset=index * CHUNK, request_size=CHUNK, limit=1):
            return bytes(chunk)
        return b""

    def shutdown(self) -> None:
        if self.client is not None:
            try:
                self._run(self.client.disconnect(), timeout=10)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
