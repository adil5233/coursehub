import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("vlc")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.course_view import CourseView  # noqa: E402


class FakeDb:
    def __init__(self, playlist_visible: str) -> None:
        self.state = {"playlist_visible": playlist_visible}

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value


class FakeThumbnails(QObject):
    ready = Signal(int)


class FakeTranscripts(QObject):
    progress = Signal(int, str, float)
    finished = Signal(int)
    failed = Signal(int, str)
    status: dict = {}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make_view(playlist_visible: str) -> CourseView:
    view = CourseView(FakeDb(playlist_visible), None, FakeThumbnails(), FakeTranscripts())
    view.transcript_btn.setEnabled(True)  # as when a lecture is open
    return view


def test_transcript_button_toggles_back_to_content(app):
    view = make_view("1")
    view.transcript_btn.click()
    assert view.side_stack.currentIndex() == 1 and view._playlist_visible and view.transcript_btn.isChecked()
    view.transcript_btn.click()  # second click hides the transcript, playlist stays as it was
    assert view.side_stack.currentIndex() == 0 and view._playlist_visible and not view.transcript_btn.isChecked()
    view.player.release()


def test_transcript_button_restores_hidden_side_panel(app):
    view = make_view("0")
    assert not view._playlist_visible
    view.transcript_btn.click()
    assert view._playlist_visible and view.side_stack.currentIndex() == 1
    view.transcript_btn.click()
    assert not view._playlist_visible and not view.transcript_btn.isChecked()
    view.player.release()


def test_transcript_button_follows_tabs_and_playlist(app):
    view = make_view("1")
    view.side_tabs.button(1).click()
    assert view.transcript_btn.isChecked()
    view.side_tabs.button(0).click()
    assert not view.transcript_btn.isChecked()
    view.show_transcript()
    view.set_playlist_visible(False)  # Playlist button hides the whole panel
    assert not view.transcript_btn.isChecked()
    view.player.release()


def test_notes_panel_grows_with_its_notes(app):
    from tests.test_notes_panel import FakeDb as NotesDb

    view = make_view("1")
    view.notes.db = NotesDb()
    view.resize(1300, 900)
    view.show()
    view.notes.set_video(1)

    def settle():
        for _ in range(10):
            app.processEvents()
        return view.left_split.sizes()[1]

    empty = settle()
    view.notes.db.add_note(1, 0, "short note")
    view.notes.set_video(1)
    one = settle()
    view.notes.db.add_note(1, 5000, "line\n" * 6)
    view.notes.set_video(1)
    two = settle()
    assert empty < one < two  # the panel grows with each note
    for i in range(30):
        view.notes.db.add_note(1, i * 1000, "many notes")
    view.notes.set_video(1)
    capped = settle()
    assert capped <= sum(view.left_split.sizes()) * 0.5 + 2  # never more than half: the rest scrolls
    view.player.release()
    view.close()

