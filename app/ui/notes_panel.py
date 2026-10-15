from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPushButton, QVBoxLayout,
)

from ..config import format_ms

NOTE_ID = Qt.ItemDataRole.UserRole
NOTE_TIME = Qt.ItemDataRole.UserRole + 1
NOTE_TEXT = Qt.ItemDataRole.UserRole + 2


class NotesPanel(QFrame):
    seek_requested = Signal(int)
    hide_requested = Signal()

    def __init__(self, db, time_provider, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("NotesPanel")
        self.db = db
        self._time = time_provider
        self.video_id: int | None = None

        title = QLabel("Notes")
        title.setObjectName("SectionTitle")
        self.count_label = QLabel()
        self.count_label.setObjectName("Muted")
        hide_button = QPushButton("Hide")
        hide_button.setObjectName("Ghost")
        hide_button.setToolTip("Hide notes")
        hide_button.clicked.connect(self.hide_requested)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addWidget(self.count_label)
        header.addStretch(1)
        header.addWidget(hide_button)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Add a note at the current time and press Enter...")
        self.input.returnPressed.connect(self._add)
        add_button = QPushButton("Add")
        add_button.setObjectName("Primary")
        add_button.clicked.connect(self._add)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(add_button)

        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_menu)
        self.list.itemClicked.connect(self._jump)
        self.list.setToolTip("Click a note to jump to that moment. Right-click to edit or delete.")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addLayout(row)
        layout.addWidget(self.list, 1)
        self.setEnabled(False)

    def set_video(self, video_id: int | None) -> None:
        self.video_id = video_id
        self.setEnabled(video_id is not None)
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        notes = self.db.list_notes(self.video_id) if self.video_id is not None else []
        for note in notes:
            item = QListWidgetItem(f"{format_ms(note.timestamp_ms)}   {note.text}")
            item.setData(NOTE_ID, note.id)
            item.setData(NOTE_TIME, note.timestamp_ms)
            item.setData(NOTE_TEXT, note.text)
            self.list.addItem(item)
        self.count_label.setText(f"{len(notes)}" if notes else "")

    def _add(self) -> None:
        text = self.input.text().strip()
        if not text or self.video_id is None:
            return
        self.db.add_note(self.video_id, self._time(), text)
        self.input.clear()
        self._reload()

    def _jump(self, item: QListWidgetItem) -> None:
        self.seek_requested.emit(int(item.data(NOTE_TIME)))

    def _show_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        jump_action = menu.addAction("Jump to this time")
        edit_action = menu.addAction("Edit")
        delete_action = menu.addAction("Delete")
        chosen = menu.exec(self.list.viewport().mapToGlobal(pos))
        if chosen is jump_action:
            self._jump(item)
        elif chosen is edit_action:
            text, ok = QInputDialog.getText(self, "Edit note", "Note:", text=item.data(NOTE_TEXT))
            if ok and text.strip():
                self.db.update_note(item.data(NOTE_ID), text.strip())
                self._reload()
        elif chosen is delete_action:
            if QMessageBox.question(self, "Delete note", "Delete this note?") == QMessageBox.StandardButton.Yes:
                self.db.delete_note(item.data(NOTE_ID))
                self._reload()
