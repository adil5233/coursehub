import time
from types import SimpleNamespace

import pytest
import requests

from app.stream_proxy import StreamProxy
from app.telegram.client import CHUNK, TelegramService

DATA = bytes(range(251)) * 12000  # ~3 MB, not a multiple of CHUNK


class FakeClient:
    def __init__(self):
        self.requests = []

    async def iter_download(self, media, offset, request_size, limit):
        assert offset % request_size == 0 and limit == 1
        self.requests.append(offset // request_size)
        yield DATA[offset:offset + request_size]


@pytest.fixture
def tg(tmp_path):
    service = TelegramService(cache_dir=tmp_path / "tg")
    service.client = FakeClient()
    service.is_authorized = True
    message = SimpleNamespace(media=None, file=SimpleNamespace(size=len(DATA), mime_type="video/x-matroska"))
    service.get_message = lambda chat_id, msg_id: message
    yield service
    service.shutdown()


@pytest.mark.parametrize("start,end", [
    (0, 99), (123_456, 2_000_000), (CHUNK - 1, CHUNK + 1), (len(DATA) - 10, len(DATA) - 1), (0, len(DATA) - 1),
])
def test_iter_range_exact_bytes(tg, start, end):
    assert b"".join(tg.iter_range(1, 2, start, end)) == DATA[start:end + 1]


def test_chunks_are_cached(tg):
    b"".join(tg.iter_range(1, 2, 0, CHUNK * 2))
    fetched = len(tg.client.requests)
    b"".join(tg.iter_range(1, 2, 10, CHUNK * 2))
    assert len(tg.client.requests) == fetched


def test_disk_cache_survives_restart(tg, tmp_path):
    b"".join(tg.iter_range(1, 2, 0, CHUNK + 5))
    deadline = time.monotonic() + 5  # disk writes happen in a future callback, just after the data is served
    while len(list((tmp_path / "tg").glob("*/*.bin"))) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    restarted = TelegramService(cache_dir=tmp_path / "tg")
    restarted.client = FakeClient()
    restarted.get_message = tg.get_message
    try:
        assert b"".join(restarted.iter_range(1, 2, 100, CHUNK + 5)) == DATA[100:CHUNK + 6]
        assert restarted.client.requests == []
    finally:
        restarted.shutdown()


def test_cached_chunks_reports_memory_and_disk(tg, tmp_path):
    b"".join(tg.iter_range(1, 2, 0, 10))
    (tmp_path / "tg" / "1_2").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tg" / "1_2" / "4.bin").write_bytes(b"x")
    (tmp_path / "tg" / "1_2" / "5.123.tmp").write_bytes(b"x")
    assert {0, 4} <= tg.cached_chunks(1, 2)
    assert 5 not in tg.cached_chunks(1, 2)
    assert tg.cached_chunks(1, 3) == set()


def test_proxy_marks_served_blocks():
    proxy = StreamProxy()
    try:
        proxy.mark_served("f", 0, 10)
        proxy.mark_served("f", 3 * 512 * 1024 - 1, 2)
        assert proxy.served_blocks("f") == {0, 2, 3}
        assert proxy.served_blocks("other") == set()
    finally:
        proxy.shutdown()


def test_proxy_ranges(tg):
    proxy = StreamProxy()
    proxy.telegram = tg
    url = proxy.telegram_url(-100123, 5)
    try:
        full = requests.get(url, timeout=30)
        assert full.status_code == 200 and full.content == DATA

        part = requests.get(url, headers={"Range": "bytes=1000-1999"}, timeout=30)
        assert part.status_code == 206
        assert part.headers["Content-Range"] == f"bytes 1000-1999/{len(DATA)}"
        assert part.content == DATA[1000:2000]

        assert requests.get(url, headers={"Range": "bytes=-100"}, timeout=30).content == DATA[-100:]
        assert requests.get(url, headers={"Range": f"bytes={len(DATA)}-"}, timeout=30).status_code == 416
        assert requests.get(url.replace(proxy.secret, "wrong"), timeout=30).status_code == 404
    finally:
        proxy.shutdown()
