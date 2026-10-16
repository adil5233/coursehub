import asyncio
import time
from types import SimpleNamespace

import pytest
import requests

from app.stream_proxy import StreamProxy
from app.telegram.client import AHEAD, CHUNK, IDLE, MAX_READ_AHEAD, MIN_READ_AHEAD, URGENT, TelegramService

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


def slow_downloads(service, seconds=0.3):
    """Replace the network with a fixed delay and log (event, index, time)."""
    service.log = []

    async def download(msg, index):
        service.log.append(("start", index, time.monotonic()))
        await asyncio.sleep(seconds)
        service.log.append(("end", index, time.monotonic()))
        return DATA[index * CHUNK:(index + 1) * CHUNK]

    service._download = download


def started(service, index):
    return next(t for event, i, t in service.log if event == "start" and i == index)


def ended(service, index):
    return next(t for event, i, t in service.log if event == "end" and i == index)


def test_idle_work_waits_while_player_waits(tg):
    slow_downloads(tg)
    msg = tg.get_message(1, 2)
    urgent = tg._chunk(1, 2, msg, 0, URGENT)
    time.sleep(0.05)
    idle = tg._chunk(1, 2, msg, 3, IDLE)
    urgent.result(5)
    idle.result(5)
    assert started(tg, 3) >= ended(tg, 0)


def test_read_ahead_runs_alongside_urgent(tg):
    """VLC reads a file's start chunk by chunk; read-ahead must overlap or every chunk pays full latency."""
    slow_downloads(tg)
    msg = tg.get_message(1, 2)
    urgent = tg._chunk(1, 2, msg, 0, URGENT)
    time.sleep(0.05)
    ahead = tg._chunk(1, 2, msg, 1, AHEAD)
    urgent.result(5)
    ahead.result(5)
    assert started(tg, 1) < ended(tg, 0)


def test_closing_a_stream_cancels_its_read_ahead(tg, monkeypatch):
    import app.telegram.client as client

    monkeypatch.setattr(client, "MAX_PARALLEL", 1)  # read-ahead queues up behind one download
    slow_downloads(tg, seconds=0.3)
    stream = tg.iter_range(1, 2, 0, len(DATA) - 1)
    for _ in range(3):
        next(stream)  # ramps read-ahead up to chunks 3..4
    stream.close()  # what happens when VLC seeks away
    time.sleep(1.5)
    downloaded = {i for event, i, _ in tg.log if event == "start"}
    assert 4 not in downloaded


def test_new_stream_on_same_file_cancels_old_read_ahead(tg, monkeypatch):
    import app.telegram.client as client

    monkeypatch.setattr(client, "MAX_PARALLEL", 1)
    slow_downloads(tg, seconds=0.3)
    old = tg.iter_range(1, 2, 0, len(DATA) - 1)
    for _ in range(3):
        next(old)  # old stream now has read-ahead queued for chunks 3..4
    new = tg.iter_range(1, 2, 5 * CHUNK, len(DATA) - 1)  # the player seeks near the end
    assert next(new) == DATA[5 * CHUNK:6 * CHUNK]
    time.sleep(1.0)
    assert 4 not in {i for event, i, _ in tg.log if event == "start"}
    old.close()
    new.close()


def test_urgent_request_promotes_queued_idle_chunk(tg):
    slow_downloads(tg, seconds=1.0)
    msg = tg.get_message(1, 2)
    blocker = tg._chunk(1, 2, msg, 0, URGENT)
    time.sleep(0.05)
    queued = tg._chunk(1, 2, msg, 4, IDLE)  # waits behind the blocker
    time.sleep(0.1)
    assert tg._chunk(1, 2, msg, 4, URGENT) is queued
    queued.result(5)
    blocker.result(5)
    assert started(tg, 4) < ended(tg, 0)


def test_prefetch_fetches_head_and_tail(tg):
    tg.prefetch(1, 2, 0.0, head_chunks=3, urgent=False)
    deadline = time.monotonic() + 5
    while len(tg.client.requests) < 4 and time.monotonic() < deadline:
        time.sleep(0.02)
    last = (len(DATA) - 1) // CHUNK
    assert sorted(tg.client.requests) == sorted({0, 1, 2, last})


@pytest.mark.parametrize("size,duration,expected", [
    (400 * 1024 ** 2, 9600, 8),  # low bitrate lecture: 90 s is ~3.8 MB
    (1500 * 1024 ** 2, 3600, MAX_READ_AHEAD),
    (10 * 1024 ** 2, 3600, MIN_READ_AHEAD),
    (100 * 1024 ** 2, None, 8),
])
def test_read_ahead_follows_bitrate(size, duration, expected):
    msg = SimpleNamespace(file=SimpleNamespace(size=size, duration=duration))
    assert TelegramService._read_ahead_chunks(msg) == expected
