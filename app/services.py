import os
import re
import shutil
import threading
from pathlib import Path

import requests

from .config import CACHE_DIR, SUBTITLE_EXTENSIONS
from .models import Course, Video
from .stream_proxy import DRIVE_MEDIA_URL, StreamProxy


def folder_size(folder: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(folder):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                pass
    return total


def empty_folder(folder: Path) -> None:
    """Delete everything inside folder but keep the folder itself."""
    if not folder.is_dir():
        return
    for child in folder.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


class Services:
    """Shared runtime objects: stream proxy plus (lazily) Drive and Telegram connections."""

    def __init__(self) -> None:
        self.proxy = StreamProxy()
        self.drive = None  # DriveSession
        self.telegram = None  # TelegramService

    def set_drive(self, session) -> None:
        self.drive = session
        self.proxy.drive_token = session.token

    def get_telegram(self):
        if self.telegram is None:
            from .telegram.client import TelegramService

            self.telegram = TelegramService()
            self.proxy.telegram = self.telegram
        return self.telegram

    def cache_size(self) -> int:
        return folder_size(CACHE_DIR)

    def clear_cache(self) -> None:
        if self.telegram is not None:
            self.telegram.clear_memory_cache()
        empty_folder(CACHE_DIR)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def media_for(self, course: Course, video: Video) -> str:
        if course.source == "local":
            path = Path(course.root) / video.ref
            if not path.is_file():
                raise FileNotFoundError(f"Video file not found:\n{path}")
            return str(path)
        if course.source == "drive":
            return self.proxy.drive_url(video.ref)
        return self.proxy.telegram_url(int(course.root), int(video.ref))

    def prefetch(self, course: Course, video: Video) -> None:
        """Start downloading the parts VLC needs first, without blocking the UI."""
        if course.source != "telegram" or self.telegram is None or not self.telegram.is_authorized:
            return
        fraction = video.last_position_ms / video.duration_ms if video.duration_ms else 0.0
        threading.Thread(
            target=self._safe_prefetch, args=(int(course.root), int(video.ref), fraction), daemon=True
        ).start()

    def _safe_prefetch(self, chat_id: int, msg_id: int, fraction: float) -> None:
        try:
            self.telegram.prefetch(chat_id, msg_id, fraction)
        except Exception:
            pass  # the real request will surface any error

    def subtitle_for(self, course: Course, video: Video) -> str | None:
        ref = video.subtitle_ref
        if not ref:
            return None
        if course.source == "local":
            path = Path(course.root) / ref
            return str(path) if path.is_file() else None

        file_id, ext = os.path.splitext(ref)
        if not re.fullmatch(r"[\w-]+", file_id) or ext.lower() not in SUBTITLE_EXTENSIONS:
            return None
        prefix = "drive" if course.source == "drive" else f"telegram_{abs(int(course.root))}"
        target = CACHE_DIR / f"{prefix}_{file_id}{ext}"
        if target.is_file():
            return str(target)
        try:
            if course.source == "drive" and self.drive is not None:
                resp = requests.get(
                    DRIVE_MEDIA_URL.format(file_id),
                    headers={"Authorization": f"Bearer {self.drive.token()}"},
                    timeout=30,
                )
                resp.raise_for_status()
                target.write_bytes(resp.content)
            elif course.source == "telegram" and self.telegram is not None:
                self.telegram.download_media(int(course.root), int(file_id), target)
            else:
                return None
        except Exception:
            return None
        return str(target) if target.is_file() else None

    def shutdown(self) -> None:
        self.proxy.shutdown()
        if self.telegram is not None:
            self.telegram.shutdown()
