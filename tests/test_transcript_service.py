import functools
import os
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("av")

from PySide6.QtCore import QCoreApplication  # noqa: E402

import app.transcribe as transcribe  # noqa: E402
from app.transcribe import Segment, groq_client, load_parts, load_transcript, parts_path, save_parts  # noqa: E402
from app.transcribe import audio, service  # noqa: E402
from tests.test_transcribe import make_tone  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(groq_client, "GROQ_CONFIG", tmp_path / "groq.json")
    monkeypatch.setattr(service, "CHUNK_S", 10)
    monkeypatch.setattr(service, "iter_chunks", functools.partial(audio.iter_chunks, chunk_s=10, overlap_s=1))
    groq_client.save_settings({"api_key": "key", "auto": False})
    source = tmp_path / "lecture.wav"
    make_tone(source, 25)
    services = SimpleNamespace(media_for=lambda course, video: str(source))
    manager = service.TranscriptManager(services)
    events = []
    manager.finished.connect(lambda vid: events.append(("finished", vid)))
    manager.failed.connect(lambda vid, msg: events.append(("failed", vid, msg)))
    yield manager, events
    manager.shutdown()


def wait_for(events, count=1, timeout=30):
    deadline = time.monotonic() + timeout
    while len(events) < count and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    assert len(events) >= count, events


def video(video_id=1):
    return SimpleNamespace(id=video_id, title="Lecture", duration_ms=25_000)


def test_transcribes_all_chunks_and_saves_srt(env, monkeypatch):
    manager, events = env
    calls = []

    def fake_transcribe(api_key, data):
        calls.append(len(data))
        return [Segment(0.5, 1.5, f"part {len(calls)}")]

    monkeypatch.setattr(groq_client, "transcribe", fake_transcribe)
    manager.enqueue(None, video())
    wait_for(events)
    assert events == [("finished", 1)]
    assert len(calls) == 3 and all(size > 0 for size in calls)
    assert [(s.start, s.text) for s in load_transcript(1)] == [(0.5, "part 1"), (10.5, "part 2"), (20.5, "part 3")]
    assert transcribe.srt_path(1).read_text(encoding="utf-8").startswith("1\n00:00:00,500 --> 00:00:01,500\npart 1")
    assert not parts_path(1).exists()
    assert groq_client.audio_seconds_today(groq_client.load_settings()) == pytest.approx(27, abs=0.5)


def test_resumes_from_saved_parts(env, monkeypatch):
    manager, events = env
    save_parts(1, {0: (0.0, [Segment(1, 2, "done before")]), 1: (10.0, [])})
    calls = []
    monkeypatch.setattr(groq_client, "transcribe", lambda key, data: calls.append(1) or [Segment(0, 1, "last")])
    manager.enqueue(None, video())
    wait_for(events)
    assert len(calls) == 1
    assert [s.text for s in load_transcript(1)] == ["done before", "last"]


def test_invalid_key_fails_the_whole_queue(env, monkeypatch):
    manager, events = env

    def reject(api_key, data):
        time.sleep(0.2)  # let the second lecture land in the queue
        raise groq_client.InvalidKey("Groq rejected the API key.")

    monkeypatch.setattr(groq_client, "transcribe", reject)
    manager.enqueue(None, video(1))
    manager.enqueue(None, video(2))
    wait_for(events, 2)
    assert [(e[0], e[1]) for e in events] == [("failed", 1), ("failed", 2)]
    assert load_transcript(1) is None and load_parts(1) == {}
