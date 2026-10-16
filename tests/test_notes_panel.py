import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.models import Note  # noqa: E402
from app.ui.notes_panel import NotesPanel  # noqa: E402


class FakeDb:
    def __init__(self) -> None:
        self.notes: dict[int, Note] = {}
        self.next_id = 1

    def list_notes(self, video_id):
        return sorted((n for n in self.notes.values() if n.video_id == video_id), key=lambda n: n.timestamp_ms)

    def add_note(self, video_id, timestamp_ms, text):
        self.notes[self.next_id] = Note(self.next_id, video_id, timestamp_ms, text)
        self.next_id += 1
        return self.next_id - 1

    def update_note(self, note_id, text):
        old = self.notes[note_id]
        self.notes[note_id] = Note(old.id, old.video_id, old.timestamp_ms, text)

    def delete_note(self, note_id):
        del self.notes[note_id]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def close_windows():
    yield
    for widget in QApplication.topLevelWidgets():  # left open, they would catch other tests' mouse events
        widget.close()


def test_long_notes_get_full_height_and_can_be_deleted(app, monkeypatch):
    db = FakeDb()
    panel = NotesPanel(db, lambda: 65_000)
    panel.resize(500, 400)
    panel.set_video(1)
    panel.show()
    assert panel.empty_label.isVisible() and not panel.scroll.isVisible()

    long_text = "Decorators ek function ko wrap karte hain. " * 12
    panel.input.setPlainText(long_text + "\nSecond line")
    panel._add()
    panel.input.setPlainText("short")
    panel._add()
    app.processEvents()
    cards = panel.cards()
    assert [c.text for c in cards] == [(long_text + "\nSecond line").strip(), "short"]
    assert panel.input.toPlainText() == ""
    assert cards[0].height() > cards[1].height() * 2  # the card grows with its text instead of cutting it

    jumps = []
    panel.seek_requested.connect(jumps.append)
    cards[1].jump.emit(cards[1].time_ms)
    assert jumps == [65_000]

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    cards[1].delete_button.click()
    app.processEvents()
    assert [c.text for c in panel.cards()] == [(long_text + "\nSecond line").strip()]
    assert panel.count_label.text() == "1"


def test_input_grows_with_lines(app):
    panel = NotesPanel(FakeDb(), lambda: 0)
    panel.resize(500, 400)
    panel.show()
    one_line = panel.input.height()
    panel.input.setPlainText("a\nb\nc")
    app.processEvents()
    assert panel.input.height() > one_line


def test_split_note_into_text_and_code():
    from app.ui.notes_panel import split_note

    note = "Neural net:\n```\n  (x1) ──┐\n  (x2) ──┼──> (z1)\n```\nyaad rakhna"
    assert split_note(note) == [(False, "Neural net:"), (True, "  (x1) ──┐\n  (x2) ──┼──> (z1)"), (False, "yaad rakhna")]
    assert split_note("plain") == [(False, "plain")]
    assert split_note("```python\nprint(1)") == [(True, "print(1)")]  # unclosed fence runs to the end


def test_code_block_in_card_and_editor_helpers(app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    db = FakeDb()
    panel = NotesPanel(db, lambda: 0)
    panel.resize(600, 400)
    panel.set_video(1)
    panel.show()

    panel.input.setFocus()
    panel.input.insert_code_block()
    assert panel.input.in_code()
    QTest.keyClicks(panel.input, "x = 1")
    QTest.keyClick(panel.input, Qt.Key.Key_Return)  # inside the code block Enter is a new line, not "Add"
    QTest.keyClick(panel.input, Qt.Key.Key_Tab)
    QTest.keyClicks(panel.input, "y = 2")
    assert db.notes == {}
    assert panel.input.toPlainText() == "```\nx = 1\n    y = 2\n```\n"

    panel._add()
    app.processEvents()
    card = panel.cards()[0]
    assert len(card.code_blocks) == 1
    block = card.code_blocks[0]
    assert block.toPlainText() == "x = 1\n    y = 2" and block.isReadOnly() and block.blockCount() == 2
    assert block.height() >= 2 * block.fontMetrics().lineSpacing()


def test_code_block_line_numbers_survive_notes_added_above(app):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    db = FakeDb()
    times = iter([100_000, 50_000, 40_000, 30_000, 20_000])  # every later note sorts above the code note
    panel = NotesPanel(db, lambda: next(times))
    panel.resize(700, 300)
    panel.set_video(1)
    panel.show()
    panel.input.setPlainText("```\n" + "\n".join(f"line {i}" for i in range(1, 9)) + "\n```")
    panel._add()
    for i in range(4):
        panel.input.setPlainText(f"note above {i}\nwith two lines")
        panel._add()
        for _ in range(5):
            app.processEvents()
        block = next(card for card in panel.cards() if card.code_blocks).code_blocks[0]
        frame = 2 * block.frameWidth()
        assert block.verticalScrollBar().value() == 0
        assert block.verticalScrollBar().maximum() == 0  # every line fits: nothing hidden below
        assert block.height() - frame >= block.content_height()
        assert block._numbers.height() == block.contentsRect().height()  # gutter covers the whole box
        from app.ui.notes_panel import NoteCard
        visible = [c for c in panel._cards.findChildren(NoteCard) if c.isVisible()]
        assert len(visible) == len(panel.cards()) == i + 2  # replaced cards don't linger behind the new ones

    wheel = QWheelEvent(QPointF(20, 20), QPointF(block.mapToGlobal(QPoint(20, 20))), QPoint(0, 0), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(block.viewport(), wheel)
    assert block.verticalScrollBar().value() == 0
