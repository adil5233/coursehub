from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from ..config import display_title, format_ms
from ..search import Hit, TranscriptCache, search

HIT = Qt.ItemDataRole.UserRole
SNIPPET_CHARS = 160


def _snippet(text: str, words: list[str]) -> str:
    """One line of text around the first matching word."""
    flat = " ".join(text.split())
    position = min((i for w in words if (i := flat.lower().find(w)) >= 0), default=0)
    start = max(0, position - SNIPPET_CHARS // 3)
    piece = flat[start:start + SNIPPET_CHARS]
    return ("\u2026" if start else "") + piece + ("\u2026" if start + SNIPPET_CHARS < len(flat) else "")


class SearchDialog(QDialog):
    """Ctrl+K: search all notes and transcripts; picking a result jumps to that lecture and moment."""

    jump_requested = Signal(int, int, int)  # course_id, video_id, time_ms

    def __init__(self, db, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Search notes and transcripts")
        self.resize(820, 560)
        self.db = db
        self._cache = TranscriptCache()

        self.input = QLineEdit()
        self.input.setPlaceholderText("Search every note and transcript... (e.g. decorator wraps)")
        self.input.setClearButtonEnabled(True)
        self.input.textChanged.connect(lambda: self._timer.start())
        self.input.returnPressed.connect(self._open_current)
        self.status = QLabel("Type at least 2 letters.")
        self.status.setObjectName("Muted")
        self.results = QListWidget()
        self.results.setWordWrap(False)  # two fixed lines per result; long snippets are elided
        self.results.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setCursor(Qt.CursorShape.PointingHandCursor)
        self.results.itemActivated.connect(self._open)
        self.results.itemClicked.connect(self._open)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._run)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self.input)
        layout.addWidget(self.status)
        layout.addWidget(self.results, 1)

    def _run(self) -> None:
        query = self.input.text().strip()
        self.results.clear()
        hits = search(self.db, query, self._cache)
        words = query.lower().split()
        for hit in hits:
            kind = "Note" if hit.kind == "note" else "Transcript"
            header = (f"{kind}  \u2022  {hit.course_name}  \u203a  {hit.lecture_number}. "
                      f"{display_title(hit.video_title)}  \u2022  {format_ms(hit.time_ms)}")
            item = QListWidgetItem(f"{header}\n{_snippet(hit.text, words)}")
            item.setData(HIT, hit)
            self.results.addItem(item)
        if len(query.replace(" ", "")) < 2:
            self.status.setText("Type at least 2 letters.")
        else:
            notes = sum(h.kind == "note" for h in hits)
            self.status.setText(f"{notes} note(s), {len(hits) - notes} transcript line(s)  \u2022  "
                                "click a result to jump there" if hits else "Nothing found.")
        if hits:
            self.results.setCurrentRow(0)

    def _open_current(self) -> None:
        if self._timer.isActive():  # Enter right after typing: search first
            self._timer.stop()
            self._run()
        if (item := self.results.currentItem()) is not None:
            self._open(item)

    def _open(self, item: QListWidgetItem) -> None:
        hit: Hit = item.data(HIT)
        self.jump_requested.emit(hit.course_id, hit.video_id, hit.time_ms)
        self.accept()
