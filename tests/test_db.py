import pytest

from app.db import Database
from app.models import ScannedVideo


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


def _videos(*refs: str) -> list[ScannedVideo]:
    return [ScannedVideo(section="S1", title=ref, ref=ref, duration_ms=60_000) for ref in refs]


def test_add_course_and_stats(db):
    course_id = db.add_course("Python", "local", "C:/courses/python", _videos("a.mp4", "b.mp4", "c.mp4"))

    assert [c.name for c in db.list_courses()] == ["Python"]
    stats = db.course_stats(course_id)
    assert (stats.total, stats.completed, stats.percent) == (3, 0, 0)
    assert [v.ref for v in db.get_videos(course_id)] == ["a.mp4", "b.mp4", "c.mp4"]


def test_invalid_source(db):
    with pytest.raises(ValueError):
        db.add_course("X", "ftp", "x", [])


def test_position_and_completion(db):
    course_id = db.add_course("Python", "local", "C:/x", _videos("a.mp4", "b.mp4", "c.mp4"))
    first = db.get_videos(course_id)[0]

    db.save_position(first.id, 5_000)
    assert db.get_video(first.id).last_position_ms == 5_000

    db.set_completed(first.id, True)
    video = db.get_video(first.id)
    assert video.completed and video.last_position_ms == 0
    assert db.course_stats(course_id).percent == 33

    db.set_completed(first.id, False)
    assert not db.get_video(first.id).completed


def test_rescan_keeps_progress(db):
    course_id = db.add_course("Python", "local", "C:/x", _videos("a.mp4", "b.mp4", "c.mp4"))
    a, b, _ = db.get_videos(course_id)
    db.set_completed(a.id, True)
    db.save_position(b.id, 42_000)

    added, removed = db.sync_videos(course_id, _videos("b.mp4", "c.mp4", "d.mp4"))
    assert (added, removed) == (1, 1)
    assert [v.ref for v in db.get_videos(course_id)] == ["b.mp4", "c.mp4", "d.mp4"]
    assert db.get_video(b.id).last_position_ms == 42_000

    added, removed = db.sync_videos(course_id, _videos("a.mp4", "b.mp4", "c.mp4", "d.mp4"))
    assert (added, removed) == (0, 0)
    assert db.get_video(a.id).completed


def test_reset_and_last_video(db):
    course_id = db.add_course("Python", "local", "C:/x", _videos("a.mp4", "b.mp4"))
    a, b = db.get_videos(course_id)
    db.set_completed(a.id, True)
    db.save_position(b.id, 9_000)
    db.set_last_video(course_id, b.id)
    assert db.get_course(course_id).last_video_id == b.id

    db.reset_progress(course_id)
    assert db.course_stats(course_id).completed == 0
    assert db.get_video(b.id).last_position_ms == 0
    assert db.get_course(course_id).last_video_id is None


def test_notes_state_and_delete_cascade(db):
    course_id = db.add_course("Python", "local", "C:/x", _videos("a.mp4"))
    video = db.get_videos(course_id)[0]

    note_id = db.add_note(video.id, 30_000, "important")
    db.add_note(video.id, 10_000, "first")
    assert [n.text for n in db.list_notes(video.id)] == ["first", "important"]
    db.update_note(note_id, "edited")
    assert db.list_notes(video.id)[1].text == "edited"

    db.set_state("last_course_id", str(course_id))
    assert db.get_state("last_course_id") == str(course_id)
    db.set_state("last_course_id", None)
    assert db.get_state("last_course_id", "none") == "none"

    db.delete_course(course_id)
    assert db.list_courses() == []
    assert db.list_notes(video.id) == []
