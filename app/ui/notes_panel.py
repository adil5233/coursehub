import math

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QKeyEvent, QPainter, QTextCursor
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ..config import format_ms
from ..transcribe import groq_client, load_transcript

INPUT_MAX_LINES = 6
FENCE = "```"


def split_note(text: str) -> list[tuple[bool, str]]:
    """Split a note into (is_code, text) parts; code sits between ``` lines (an unclosed fence runs to the end)."""
    parts: list[tuple[bool, str]] = []
    current: list[str] = []
    in_code = False
    for line in text.split("\n"):
        if line.strip().startswith(FENCE):
            block = "\n".join(current)
            if in_code or block.strip():
                parts.append((in_code, block if in_code else block.strip()))
            current, in_code = [], not in_code
            continue
        current.append(line)
    block = "\n".join(current)
    if in_code or block.strip():
        parts.append((in_code, block if in_code else block.strip()))
    return parts


def _mono_font() -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setFamilies(["Cascadia Mono", "Consolas", font.family()])
    font.setPointSizeF(10)
    return font


class _LineNumbers(QWidget):
    def __init__(self, editor: "CodeBlock") -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#161616"))
        painter.setFont(self.editor.font())
        painter.setPen(QColor("#6e7681"))
        block = self.editor.firstVisibleBlock()
        top = self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top()
        height = self.editor.fontMetrics().height()
        while block.isValid() and top <= self.height():
            painter.drawText(QRect(0, int(top), self.width() - 10, height), Qt.AlignmentFlag.AlignRight,
                             str(block.blockNumber() + 1))
            top += self.editor.blockBoundingRect(block).height()
            block = block.next()


class CodeBlock(QPlainTextEdit):
    """Read-only monospace code box with line numbers; never wraps, scrolls sideways when lines are long."""

    def __init__(self, code: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("NoteCode")
        self.setReadOnly(True)
        self.setFont(_mono_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                     | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.setPlainText(code)
        self._numbers = _LineNumbers(self)
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        self.updateRequest.connect(lambda _rect, _dy: self._numbers.update())
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def gutter_width(self) -> int:
        return 18 + self.fontMetrics().horizontalAdvance("9") * len(str(max(1, self.blockCount())))

    def content_height(self) -> int:
        total, block = 0.0, self.document().firstBlock()
        while block.isValid():  # real laid-out line heights; font-metric guesses can come up a line short
            total += self.blockBoundingRect(block).height()
            block = block.next()
        return math.ceil(total)

    def _fit_height(self) -> None:
        widest = max((self.fontMetrics().horizontalAdvance(line) for line in self.toPlainText().split("\n")),
                     default=0)
        needs_bar = widest + 2 * self.document().documentMargin() > self.viewport().width()
        bar = self.horizontalScrollBar().sizeHint().height() if needs_bar else 0
        frame = 2 * self.frameWidth() + math.ceil(2 * self.document().documentMargin())
        self.setFixedHeight(self.content_height() + frame + bar + 1)
        self.verticalScrollBar().setValue(0)

    def _update_gutter(self) -> None:
        rect = self.contentsRect()
        self._numbers.setGeometry(QRect(rect.left(), rect.top(), self.gutter_width(), rect.height()))
        self._numbers.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.viewportMargins().left() != self.gutter_width():  # the theme's font arrives after __init__
            self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        self._fit_height()
        self._update_gutter()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_height()
        self._update_gutter()

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y():  # the box always shows every line: let the notes list scroll instead
            event.ignore()
            return
        super().wheelEvent(event)


class _NoteInput(QPlainTextEdit):
    """Multi-line note box that grows with its text.

    Enter adds the note and Shift+Enter starts a new line; inside a ``` code block Enter and Tab type normally.
    """

    submitted = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("NoteInput")
        self.setTabChangesFocus(True)
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)
        self._fit_height()

    def in_code(self) -> bool:
        before = self.toPlainText()[: self.textCursor().position()]
        return sum(line.strip().startswith(FENCE) for line in before.split("\n")) % 2 == 1

    def insert_code_block(self) -> None:
        cursor = self.textCursor()
        selected = cursor.selectedText().replace("\u2029", "\n")  # Qt marks selected line breaks as U+2029
        prefix = "" if cursor.atBlockStart() else "\n"
        cursor.insertText(f"{prefix}{FENCE}\n{selected}\n{FENCE}\n")
        cursor.movePosition(QTextCursor.MoveOperation.Up, n=2)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        self.setTextCursor(cursor)
        self.setFocus()

    def event(self, event) -> bool:
        # Tab normally moves focus (setTabChangesFocus) before keyPressEvent sees it; inside code it indents
        if (event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Tab
                and not event.modifiers() and self.in_code()):
            self.insertPlainText("    ")
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key, shift = event.key(), event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not shift and not self.in_code():
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def _fit_height(self, *_args) -> None:
        lines = max(1, min(INPUT_MAX_LINES, round(self.document().size().height())))  # plain-text layout: lines
        frame = int(self.document().documentMargin() * 2) + self.contentsMargins().top() * 2 + 6
        self.setFixedHeight(lines * self.fontMetrics().lineSpacing() + frame)


class NoteCard(QFrame):
    """One note: time chip, the full wrapped text, Edit / Delete. Clicking the card jumps to the time."""

    jump = Signal(int)
    edit = Signal(int, str)
    delete = Signal(int)

    def __init__(self, note, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("NoteCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to jump to this moment")
        self.note_id, self.time_ms, self.text = note.id, note.timestamp_ms, note.text

        time_button = QPushButton(format_ms(note.timestamp_ms))
        time_button.setObjectName("NoteTime")
        time_button.clicked.connect(lambda: self.jump.emit(self.time_ms))
        self.edit_button = QPushButton("Edit")
        self.delete_button = QPushButton("Delete")
        for button, name in ((self.edit_button, "NoteAction"), (self.delete_button, "NoteDelete")):
            button.setObjectName(name)
        for button in (time_button, self.edit_button, self.delete_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.edit_button.clicked.connect(lambda: self.edit.emit(self.note_id, self.text))
        self.delete_button.clicked.connect(lambda: self.delete.emit(self.note_id))

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(time_button)
        top.addStretch(1)
        top.addWidget(self.edit_button)
        top.addWidget(self.delete_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 10, 10)
        layout.setSpacing(6)
        layout.addLayout(top)
        self.code_blocks: list[CodeBlock] = []
        for is_code, part in split_note(note.text):
            if is_code:
                block = CodeBlock(part)
                self.code_blocks.append(block)
                layout.addWidget(block)
                continue
            label = QLabel(part)
            label.setObjectName("NoteText")
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)  # wrap to the card width
            layout.addWidget(label)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.jump.emit(self.time_ms)
        super().mouseReleaseEvent(event)


class NotesPanel(QFrame):
    seek_requested = Signal(int)
    hide_requested = Signal()
    note_added = Signal()
    export_requested = Signal()
    content_height_changed = Signal()  # preferred_height() changed: the parent may resize the panel

    def __init__(self, db, time_provider, summarizer=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("NotesPanel")
        self.db = db
        self._time = time_provider
        self.summarizer = summarizer
        self.video_id: int | None = None
        self._has_notes = False
        self._last_width = 0
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self.content_height_changed)

        title = QLabel("Notes")
        title.setObjectName("SectionTitle")
        self.count_label = QLabel()
        self.count_label.setObjectName("Muted")
        hide_button = QPushButton("Hide")
        hide_button.setObjectName("Ghost")
        hide_button.setToolTip("Hide notes")
        hide_button.clicked.connect(self.hide_requested)
        self.ai_status = QLabel()
        self.ai_status.setObjectName("Muted")
        self.ai_button = QPushButton("AI summary")
        self.ai_button.setToolTip("Write a summary note of this lecture from its transcript (Groq, free)")
        self.ai_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ai_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ai_button.clicked.connect(self._generate_summary)
        self.ai_button.setVisible(summarizer is not None)
        export_button = QPushButton("Export")
        export_button.setObjectName("Ghost")
        export_button.setToolTip("Save every note of this course as one Markdown file")
        export_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        export_button.clicked.connect(lambda: self.export_requested.emit())
        header = QHBoxLayout()
        header.addWidget(title)
        header.addWidget(self.count_label)
        header.addStretch(1)
        header.addWidget(self.ai_status)
        header.addWidget(self.ai_button)
        header.addWidget(export_button)
        header.addWidget(hide_button)
        if summarizer is not None:
            summarizer.progress.connect(self._on_summary_progress)
            summarizer.finished.connect(self._on_summary_finished)
            summarizer.failed.connect(self._on_summary_failed)

        self.input = _NoteInput()
        self.input.setPlaceholderText("Write a note at the current time... (Ctrl+N from anywhere, Enter to add, "
                                      "Shift+Enter for a new line, </> for a code block)")
        self.input.submitted.connect(self._add)
        self.input.document().documentLayout().documentSizeChanged.connect(lambda _size: self._fit_timer.start())
        code_button = QPushButton("</>")
        code_button.setToolTip("Insert a code block (or wrap the selected text in one)")
        code_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        code_button.clicked.connect(self.input.insert_code_block)
        add_button = QPushButton("Add")
        add_button.setObjectName("Primary")
        add_button.clicked.connect(self._add)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(code_button, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(add_button, 0, Qt.AlignmentFlag.AlignTop)

        self.empty_label = QLabel("No notes for this lecture yet.")
        self.empty_label.setObjectName("Muted")
        self._cards = QWidget()
        self._cards.setObjectName("NotesList")
        self._cards_layout = QVBoxLayout(self._cards)
        self._cards_layout.setContentsMargins(0, 0, 6, 0)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("NotesScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self._cards)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addLayout(row)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.scroll, 1)
        layout.addStretch(0)  # keeps everything at the top when there are no notes
        self.setMinimumHeight(110)
        self.setEnabled(False)

    def preferred_height(self) -> int:
        """Height that shows the header, the input and every note without scrolling."""
        layout = self.layout()
        margins = layout.contentsMargins()
        height = margins.top() + margins.bottom() + 2 * layout.spacing()
        height += layout.itemAt(0).sizeHint().height() + layout.itemAt(1).sizeHint().height()
        if self._has_notes:
            cards = self._cards.heightForWidth(max(100, self.scroll.viewport().width()))
            height += layout.spacing() + (cards if cards > 0 else self._cards.sizeHint().height())
        elif not self.empty_label.isHidden():
            height += layout.spacing() + self.empty_label.sizeHint().height()
        return height

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.width() != self._last_width:  # notes re-wrap, so they need a different height
            self._last_width = self.width()
            self._fit_timer.start()

    def set_video(self, video_id: int | None) -> None:
        self.video_id = video_id
        self.setEnabled(video_id is not None)
        self._reload()
        self._sync_ai()

    # ---- AI summary ----
    def _sync_ai(self, status: str | None = None) -> None:
        if self.summarizer is None:
            return
        busy = self.summarizer.busy_video()
        mine = busy is not None and busy == self.video_id
        self.ai_button.setEnabled(self.video_id is not None and busy is None)
        self.ai_button.setText("Summarizing..." if mine else "AI summary")
        if status is not None:
            self.ai_status.setText(status)
        elif mine:
            self.ai_status.setText(self.summarizer.status)
        else:
            self.ai_status.clear()

    def _generate_summary(self) -> None:
        if self.summarizer is None or self.video_id is None:
            return
        segments = load_transcript(self.video_id)
        if not segments:
            QMessageBox.information(
                self, "Transcript needed",
                "The AI summary is written from the lecture's transcript.\n\n"
                "Make the transcript first: Transcript tab \u2192 Generate transcript.")
            return
        if not groq_client.load_settings()["api_key"]:
            QMessageBox.information(
                self, "Groq API key needed",
                "AI summaries use Groq's free API.\n\nCreate a free key at console.groq.com/keys, "
                "then paste it in Settings \u2192 Transcripts.")
            return
        self.summarizer.start(self.video_id, segments)
        self._sync_ai()

    def _on_summary_progress(self, video_id: int, text: str) -> None:
        if video_id == self.video_id:
            self.ai_status.setText(text)

    def _on_summary_finished(self, video_id: int, text: str) -> None:
        note_id = self.db.add_note(video_id, 0, text)  # main thread: the sqlite connection lives here
        if video_id == self.video_id:
            self._reload(scroll_to=note_id)
            self._sync_ai("Summary added \u2713")
        else:
            self._sync_ai()

    def _on_summary_failed(self, video_id: int, message: str) -> None:
        self._sync_ai()
        if video_id == self.video_id:
            self.ai_status.clear()
            QMessageBox.warning(self, "AI summary", message)

    def cards(self) -> list[NoteCard]:
        return [self._cards_layout.itemAt(i).widget() for i in range(self._cards_layout.count() - 1)]

    def _reload(self, scroll_to: int | None = None) -> None:
        for card in self.cards():
            self._cards_layout.removeWidget(card)
            card.hide()  # deleteLater waits for the event loop; until then the old card would still paint
            card.setParent(None)
            card.deleteLater()
        notes = self.db.list_notes(self.video_id) if self.video_id is not None else []
        target = None
        for note in notes:
            card = NoteCard(note)
            card.jump.connect(self.seek_requested)
            card.edit.connect(self._edit)
            card.delete.connect(self._delete)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            if note.id == scroll_to:
                target = card
        self.count_label.setText(f"{len(notes)}" if notes else "")
        self._has_notes = bool(notes)
        self.empty_label.setVisible(not notes and self.video_id is not None)
        self.scroll.setVisible(bool(notes))
        self._fit_timer.start()
        if target is not None:  # after layout, so the new/edited note is scrolled into view
            QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(target, 0, 8))

    def _add(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self.video_id is None:
            return
        note_id = self.db.add_note(self.video_id, self._time(), text)
        self.input.clear()
        self._reload(scroll_to=note_id)
        self.note_added.emit()

    def focus_input(self) -> None:
        self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _edit(self, note_id: int, text: str) -> None:
        new_text, ok = QInputDialog.getMultiLineText(self, "Edit note", "Note:", text)
        if ok and new_text.strip():
            self.db.update_note(note_id, new_text.strip())
            self._reload(scroll_to=note_id)

    def _delete(self, note_id: int) -> None:
        if QMessageBox.question(self, "Delete note", "Delete this note?") == QMessageBox.StandardButton.Yes:
            self.db.delete_note(note_id)
            self._reload()
