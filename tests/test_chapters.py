import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import app.transcribe as transcribe  # noqa: E402
from app.transcribe import Segment, chapters  # noqa: E402


def lecture(minutes: int) -> list[Segment]:
    return [Segment(s, s + 9, f"line {s}") for s in range(0, minutes * 60, 10)]


def test_marked_parts_put_a_time_marker_every_30_seconds():
    parts = chapters.marked_parts(lecture(3), max_chars=10_000)
    assert len(parts) == 1
    start, end, text = parts[0]
    assert (start, end) == (0, 179_000)
    assert text.startswith("[0:00] line 0 line 10 line 20 [0:30] line 30") and "[2:30] line 150" in text
    many = chapters.marked_parts(lecture(30), max_chars=500)
    assert len(many) > 3 and all(t.startswith("[") for _s, _e, t in many)  # every part starts with a marker


def test_parse_and_tidy():
    reply = ('Here: [{"time": "0:05", "title": " Intro  "}, {"time": "1:00:30", "title": "Too late"}, '
             '{"time": "12:30", "title": "Neuron"}, {"time": "15:00", "title": "Too close"}, {"time": "x", "title": "Bad"}]')
    parsed = chapters.parse_chapters(reply, 0, 3_000_000)
    assert [(c["start_ms"], c["title"]) for c in parsed] == [(5_000, "Intro"), (750_000, "Neuron"), (900_000, "Too close")]
    assert chapters.parse_chapters(reply, 0, 600_000)[-1]["title"] == "Intro"  # outside the part: dropped
    tidy = chapters.tidy(parsed)
    assert [(c["start_ms"], c["title"]) for c in tidy] == [(0, "Intro"), (750_000, "Neuron")]
    assert chapters.parse_chapters("nope", 0, 1) == []


def test_make_chapters_and_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    replies = iter(['[{"time": "0:00", "title": "Basics"}]', '[{"time": "MM", "title": "x"}]',
                    '[{"time": "20:00", "title": "Training"}]'])
    monkeypatch.setattr(chapters, "marked_parts", lambda segments: [(0, 599_000, "a"), (600_000, 1_199_000, "b"),
                                                                    (1_200_000, 1_800_000, "c")])
    monkeypatch.setattr(chapters, "ask", lambda *a, **k: next(replies))
    result = chapters.make_chapters("key", lecture(1))
    assert result == [{"start_ms": 0, "title": "Basics"}, {"start_ms": 1_200_000, "title": "Training"}]
    chapters.save_chapters(5, result)
    assert chapters.load_chapters(5) == result and chapters.load_chapters(6) == []
    monkeypatch.setattr(chapters, "ask", lambda *a, **k: "[]")
    with pytest.raises(chapters.groq_client.GroqError):
        chapters.make_chapters("key", lecture(1))


def test_transcript_panel_chapter_list():
    from app.ui.transcript_panel import TranscriptPanel

    app = QApplication.instance() or QApplication([])
    panel = TranscriptPanel()
    panel.show_segments(lecture(1))
    panel.set_chapters([{"start_ms": 0, "title": "Basics"}, {"start_ms": 65_000, "title": "Weights"}])
    assert panel.chapter_list.count() == 2 and panel.chapters_button.text() == "Remake chapters"
    seeks = []
    panel.seek_requested.connect(seeks.append)
    panel.chapter_list.itemClicked.emit(panel.chapter_list.item(1))
    assert seeks == [65_000] and "1:05" in panel.chapter_list.item(1).text()
    panel.set_chapters_busy("Making chapters... part 1 of 2")
    assert not panel.chapters_button.isEnabled() and not panel.chapter_status.isHidden()
    panel.set_chapters_busy(None)
    assert panel.chapters_button.isEnabled() and panel.chapter_status.isHidden()
    app.processEvents()


def test_player_chapters_on_seek_bar():
    pytest.importorskip("vlc")
    from app.player.vlc_widget import VideoPlayer

    app = QApplication.instance() or QApplication([])
    player = VideoPlayer()
    player._length = 600_000
    player.set_chapters([{"start_ms": 300_000, "title": "Second half"}, {"start_ms": 0, "title": "Start"}])
    assert player.slider._chapters == [0.5]
    assert player.chapter_at(10_000) == "Start" and player.chapter_at(450_000) == "Second half"
    assert "Start" in player.time_label.text()
    player.stop()
    assert player.slider._chapters == [] and player.chapter_at(450_000) == ""
    player.release()
    app.processEvents()
