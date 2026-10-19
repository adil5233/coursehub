from bisect import bisect_right

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QProgressBar, QPushButton,
    QStyledItemDelegate, QVBoxLayout,
)

from ..config import format_ms
from ..transcribe import Segment

START_MS = Qt.ItemDataRole.UserRole
ITEM_PAD_X, ITEM_PAD_Y = 6, 8  # matches "QListWidget::item { padding }" in the theme


class _WrapDelegate(QStyledItemDelegate):
    """Row height for word-wrapped lines (Qt's own hint ignores the stylesheet padding and clips them)."""

    def sizeHint(self, option, index) -> QSize:
        width = max(80, self.parent().viewport().width() - 2 * ITEM_PAD_X - 4)
        bounds = option.fontMetrics.boundingRect(QRect(0, 0, width, 100_000), Qt.TextFlag.TextWordWrap,
                                                 str(index.data()))
        return QSize(width, bounds.height() + 2 * ITEM_PAD_Y + 2)


class TranscriptPanel(QFrame):
    """YouTube-like transcript: timed lines, click to jump, search, current line highlighted."""

    seek_requested = Signal(int)
    generate_requested = Signal()
    cancel_requested = Signal()
    chapters_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TranscriptPanel")
        self._starts: list[int] = []
        self._segments: list[Segment] = []
        self._current_row = -1

        self.status = QLabel()
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        self.generate_button = QPushButton("Generate transcript")
        self.generate_button.setObjectName("Primary")
        self.generate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.generate_button.clicked.connect(lambda: self.generate_requested.emit())
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(lambda: self.cancel_requested.emit())
        buttons = QHBoxLayout()
        buttons.addWidget(self.generate_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search in transcript...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)

        self.copy_button = QPushButton("Copy")
        self.copy_button.setToolTip("Copy the whole transcript")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_menu = QMenu(self.copy_button)
        copy_menu.addAction("Copy text", lambda: self._copy(timestamps=False))
        copy_menu.addAction("Copy with timestamps", lambda: self._copy(timestamps=True))
        self.copy_button.setMenu(copy_menu)
        self.chapters_button = QPushButton("AI chapters")
        self.chapters_button.setToolTip("Split this lecture into topics, shown on the seek bar (Groq)")
        self.chapters_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chapters_button.clicked.connect(lambda: self.chapters_requested.emit())
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.chapters_button)
        search_row.addWidget(self.copy_button)

        self.chapter_status = QLabel()
        self.chapter_status.setObjectName("Muted")
        self.chapter_status.hide()
        self.chapter_list = QListWidget()
        self.chapter_list.setObjectName("ChapterList")
        self.chapter_list.setMaximumHeight(170)
        self.chapter_list.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chapter_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chapter_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.chapter_list.itemClicked.connect(lambda item: self.seek_requested.emit(int(item.data(START_MS))))
        self.chapter_list.hide()

        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.setItemDelegate(_WrapDelegate(self.list))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)  # re-wrap lines when the panel is resized
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setCursor(Qt.CursorShape.PointingHandCursor)
        self.list.itemClicked.connect(lambda item: self.seek_requested.emit(int(item.data(START_MS))))
        self.list.setToolTip("Click a line to jump to that moment")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.status)
        layout.addLayout(buttons)
        layout.addWidget(self.progress)
        layout.addLayout(search_row)
        layout.addWidget(self.chapter_status)
        layout.addWidget(self.chapter_list)
        layout.addWidget(self.list, 1)
        layout.addStretch(0)  # keeps status + buttons at the top while the list is hidden
        self.show_idle("Select a lecture to see its transcript.", can_generate=False)

    # ---- states ----
    def show_idle(self, text: str, can_generate: bool = True) -> None:
        self._set_segments(None)
        self.status.setText(text)
        self.generate_button.setVisible(can_generate)
        self.cancel_button.hide()
        self.progress.hide()

    def show_progress(self, text: str, fraction: float) -> None:
        self._set_segments(None)
        self.status.setText(text)
        self.generate_button.hide()
        self.cancel_button.show()
        self.progress.show()
        if fraction < 0:
            self.progress.setRange(0, 0)  # busy indicator
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(fraction * 100))

    def show_segments(self, segments: list[Segment]) -> None:
        self.status.setText(f"{len(segments)} lines  \u2022  click a line to jump there")
        self.generate_button.hide()
        self.cancel_button.hide()
        self.progress.hide()
        self._set_segments(segments)

    def _set_segments(self, segments: list[Segment] | None) -> None:
        self.list.clear()
        self._starts = []
        self._segments = list(segments or [])
        self._current_row = -1
        has_lines = bool(segments)
        self.search.setVisible(has_lines)
        self.copy_button.setVisible(has_lines)
        self.chapters_button.setVisible(has_lines)
        self.chapter_list.setVisible(has_lines and self.chapter_list.count() > 0)
        self.list.setVisible(has_lines)
        for segment in segments or []:
            start_ms = int(segment.start * 1000)
            item = QListWidgetItem(f"{format_ms(start_ms)}    {segment.text}")
            item.setData(START_MS, start_ms)
            self.list.addItem(item)
            self._starts.append(start_ms)
        if self.search.text():
            self._filter(self.search.text())

    # ---- chapters ----
    def set_chapters(self, chapters: list[dict]) -> None:
        self.chapter_list.clear()
        for chapter in chapters:
            item = QListWidgetItem(f"{format_ms(chapter['start_ms'])}    {chapter['title']}")
            item.setData(START_MS, chapter["start_ms"])
            self.chapter_list.addItem(item)
        self.chapter_list.setVisible(bool(chapters) and self.list.isVisibleTo(self))
        self.chapters_button.setText("Remake chapters" if chapters else "AI chapters")

    def set_chapters_busy(self, text: str | None) -> None:
        """text: progress while chapters are being made; None when done."""
        self.chapters_button.setEnabled(text is None)
        self.chapter_status.setText(text or "")
        self.chapter_status.setVisible(bool(text))

    # ---- copy ----
    def transcript_text(self, timestamps: bool) -> str:
        if timestamps:
            return "\n".join(f"[{format_ms(int(s.start * 1000))}] {s.text}" for s in self._segments)
        return " ".join(s.text for s in self._segments)

    def _copy(self, timestamps: bool) -> None:
        QGuiApplication.clipboard().setText(self.transcript_text(timestamps))
        self.copy_button.setText("Copied \u2713")
        QTimer.singleShot(1500, lambda: self.copy_button.setText("Copy"))

    # ---- playback sync ----
    def set_position(self, position_ms: int) -> None:
        row = bisect_right(self._starts, position_ms) - 1
        if row == self._current_row or row < 0:
            return
        self._current_row = row
        item = self.list.item(row)
        self.list.setCurrentItem(item)
        if not self.list.underMouse() and not self.search.text():  # don't yank the list while the user reads
            self.list.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(bool(needle) and needle not in item.text().lower())
