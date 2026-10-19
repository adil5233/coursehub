import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import app.transcribe as transcribe  # noqa: E402
from app.db import Database  # noqa: E402
from app.models import ScannedVideo  # noqa: E402
from app.search import TranscriptCache, search  # noqa: E402
from app.transcribe import Segment  # noqa: E402


def setup_library(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    db = Database(tmp_path / "data.db")
    course_id = db.add_course("Python", "local", str(tmp_path), [
        ScannedVideo("", "01 Intro.mp4", "a", 0), ScannedVideo("", "02 Decorators.mp4", "b", 0)])
    intro, decorators = db.get_videos(course_id)
    db.add_note(decorators.id, 90_000, "functools.wraps lagana mat bhoolna")
    db.add_note(intro.id, 5_000, "setup note")
    transcribe.save_transcript(decorators.id, [
        Segment(10, 14, "Decorator ek function ko wrap karta hai."),
        Segment(70, 74, "Isliye hum functools wraps use karte hain."),
    ])
    transcribe.save_transcript(999, [Segment(0, 2, "wraps from a deleted lecture")])  # not in the library
    return db, course_id, intro, decorators


def test_search_notes_and_transcripts(tmp_path, monkeypatch):
    db, course_id, _intro, decorators = setup_library(tmp_path, monkeypatch)
    hits = search(db, "Functools WRAPS")
    assert [(h.kind, h.video_id, h.time_ms) for h in hits] == [
        ("note", decorators.id, 90_000), ("transcript", decorators.id, 70_000)]
    assert hits[0].course_name == "Python" and hits[0].lecture_number == 2
    assert search(db, "decorator wrap")[0].time_ms == 10_000  # every word must match, in any order
    assert search(db, "x") == [] and search(db, "nothing here") == []

    cache = TranscriptCache()
    assert len(search(db, "wraps", cache)) == 2
    transcribe.save_transcript(decorators.id, [Segment(1, 2, "naya transcript me wraps")])
    os.utime(transcribe.segments_path(decorators.id), (1, 1))  # make sure the mtime changes
    assert [h.text for h in search(db, "wraps", cache) if h.kind == "transcript"] == ["naya transcript me wraps"]
    db.close()


def test_search_dialog_jumps(tmp_path, monkeypatch):
    from app.ui.search_dialog import SearchDialog

    app = QApplication.instance() or QApplication([])
    db, course_id, _intro, decorators = setup_library(tmp_path, monkeypatch)
    dialog = SearchDialog(db)
    jumps = []
    dialog.jump_requested.connect(lambda *args: jumps.append(args))
    dialog.input.setText("wraps")
    dialog.input.returnPressed.emit()  # Enter: search now and open the first result
    app.processEvents()
    assert jumps == [(course_id, decorators.id, 90_000)]
    assert dialog.results.count() == 2 and "Note" in dialog.results.item(0).text()
    db.close()
