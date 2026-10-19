import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.db import Database  # noqa: E402
from app.models import ScannedVideo  # noqa: E402


def make_course(db, name, tmp_path, count=3):
    course_id = db.add_course(name, "local", str(tmp_path), [
        ScannedVideo("", f"{i:02d} part.mp4", f"{name}-{i}", 600_000) for i in range(1, count + 1)])
    return course_id, db.get_videos(course_id)


def test_continue_watching_order_and_next_lecture(tmp_path):
    db = Database(tmp_path / "data.db")
    first, first_videos = make_course(db, "First", tmp_path)
    second, second_videos = make_course(db, "Second", tmp_path)
    make_course(db, "Never opened", tmp_path)
    assert db.continue_watching() == []

    db.set_last_video(first, first_videos[1].id)
    db.save_position(first_videos[1].id, 120_000)
    db.conn.execute("UPDATE courses SET last_watched_at = '2026-01-01 10:00:00' WHERE id = ?", (first,))
    db.set_last_video(second, second_videos[0].id)
    db.set_completed(second_videos[0].id, True)  # finished: continue with the next lecture

    result = [(course.name, video.title, video.last_position_ms) for course, video in db.continue_watching()]
    assert result == [("Second", "02 part.mp4", 0), ("First", "02 part.mp4", 120_000)]

    for video in second_videos:
        db.set_completed(video.id, True)
    assert [course.name for course, _ in db.continue_watching()] == ["First"]  # finished courses drop out
    db.close()


def test_library_shows_continue_row(tmp_path):
    from app.ui.library_view import LibraryView

    app = QApplication.instance() or QApplication([])
    db = Database(tmp_path / "data.db")
    course_id, videos = make_course(db, "Python", tmp_path)
    view = LibraryView(db)
    view.resize(1400, 900)
    view.refresh()
    view.show()
    app.processEvents()
    assert not view.continue_title.isVisible() and not view._continue_cards

    db.set_last_video(course_id, videos[2].id)
    db.save_position(videos[2].id, 60_000)
    view.refresh()
    app.processEvents()
    assert view.continue_title.isVisible() and len(view._continue_cards) == 1
    opened = []
    view.continue_lecture.connect(lambda c, v: opened.append((c, v)))
    card = view._continue_cards[0]
    card.open_requested.emit(card.course_id, card.video_id)
    assert opened == [(course_id, videos[2].id)]
    view.close()
    db.close()
