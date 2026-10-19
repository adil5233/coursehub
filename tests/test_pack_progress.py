import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.pack_progress import PARTS_SHARE, PackProgress, stage_range  # noqa: E402
from tests.test_steps import view  # noqa: E402,F401  (fixture)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def test_stage_range():
    assert stage_range("Making the study pack... part 1 of 4") == (0.0, PARTS_SHARE / 4)
    assert stage_range("Making the study pack... part 4 of 4") == (PARTS_SHARE * 3 / 4, PARTS_SHARE)
    assert stage_range("Writing the mantra...") == (0.93, 0.99)
    assert stage_range("gpt-oss-120b hit its free limit, switching to ...") is None
    assert stage_range("Waiting 20 s for the free AI limit...") is None


def test_progress_follows_the_stages_and_finishes(app):
    progress = PackProgress()
    progress.show()
    progress.update_stage("Making the study pack...")  # starts with an indeterminate sweep
    assert not progress.isHidden() and progress.bar.value is None and progress.percent.text() == ""
    progress.update_stage("Making the study pack... part 2 of 3")
    pump(app, 0.6)
    assert progress.bar.value == pytest.approx(0.3, abs=0.02) and progress.percent.text() == "30%"
    pump(app, 0.5)
    assert 0.3 < progress.value < 0.6  # drifts inside the part, never past its end
    before = progress.value
    progress.update_stage("Waiting 20 s for the free AI limit...")
    progress.update_stage("Making the study pack... part 1 of 3")  # never goes backwards
    pump(app, 0.2)
    assert progress.value >= before
    progress.finish()
    assert progress.finishing()
    pump(app, 0.6)
    assert progress.percent.text() == "100%"
    pump(app, 0.4)
    assert progress.isHidden() and not progress.finishing()


def test_fail_hides_at_once(app):
    progress = PackProgress()
    progress.update_stage("Making the study pack... part 1 of 2")
    progress.fail()
    assert progress.isHidden()
    progress.finish()  # nothing running: stays hidden
    assert progress.isHidden() and not progress.finishing()


def test_course_view_shows_and_ends_the_progress(view, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from app.ui import course_view

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    progress = view.steps.pack_progress
    view.pack_maker.busy_video = 1
    view._on_pack_progress(1, "Making the study pack... part 1 of 2")
    assert not progress.isHidden() and not view.steps.make_button.isEnabled()
    view.pack_maker.busy_video = None
    view._on_pack_failed(1, "Groq said no")
    assert progress.isHidden() and view.steps.make_button.isEnabled()

    view._on_pack_progress(1, "Making the study pack... part 2 of 2")
    saved = []
    monkeypatch.setattr(course_view, "save_pack", lambda db, video_id, pack: saved.append(video_id))
    monkeypatch.setattr(course_view, "load_steps", lambda video_id: {"steps": [], "mantra": ""})
    monkeypatch.setattr(course_view, "load_chapters", lambda video_id: [])
    view._on_pack_ready(1, {})
    assert saved == [1] and progress.finishing() and not progress.isHidden()  # 100 % for a moment
