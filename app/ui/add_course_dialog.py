from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QRadioButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .drive_folder_dialog import DriveFolderDialog
from .remote import ensure_drive, ensure_telegram, scan_source
from .telegram_dialog import TelegramChannelDialog
from .utils import run_with_progress, show_error

LOCAL, DRIVE, TELEGRAM = 0, 1, 2
_SOURCES = {LOCAL: "local", DRIVE: "drive", TELEGRAM: "telegram"}


class AddCourseDialog(QDialog):
    def __init__(self, db, services, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Course")
        self.setMinimumWidth(520)
        self.db = db
        self.services = services
        self.course_id: int | None = None
        self._drive_folder: tuple[str, str] | None = None
        self._channel: tuple[int, str] | None = None

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Python Bootcamp")

        self.group = QButtonGroup(self)
        source_box = QGroupBox("Videos source")
        source_layout = QVBoxLayout(source_box)
        for source_id, label in (
            (LOCAL, "Local folder (PC / already downloaded videos)"),
            (DRIVE, "Google Drive (stream online)"),
            (TELEGRAM, "Telegram channel (stream online)"),
        ):
            radio = QRadioButton(label)
            self.group.addButton(radio, source_id)
            source_layout.addWidget(radio)
        self.group.button(LOCAL).setChecked(True)

        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("No folder selected")
        self.drive_label = QLabel("No folder selected")
        self.channel_label = QLabel("No channel selected")

        self.stack = QStackedWidget()
        self.stack.addWidget(self._row(self.path_edit, "Browse...", self._browse_local))
        self.stack.addWidget(self._row(self.drive_label, "Choose Drive folder...", self._choose_drive))
        self.stack.addWidget(self._row(self.channel_label, "Choose channel...", self._choose_channel))
        self.group.idToggled.connect(lambda source_id, checked: checked and self.stack.setCurrentIndex(source_id))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add Course")
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Course name", self.name_edit)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(source_box)
        layout.addWidget(self.stack)
        hint = QLabel("Sub-folders become sections. On Telegram, a #hashtag in the caption becomes the section.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    @staticmethod
    def _row(display: QWidget, button_text: str, slot) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(display, 1)
        button = QPushButton(button_text)
        button.clicked.connect(slot)
        row.addWidget(button)
        return page

    def _default_name(self, name: str) -> None:
        if not self.name_edit.text().strip():
            self.name_edit.setText(name)

    def _browse_local(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select course folder")
        if path:
            self.path_edit.setText(path)
            self._default_name(Path(path).name)

    def _choose_drive(self) -> None:
        if not ensure_drive(self, self.services):
            return
        dialog = DriveFolderDialog(self.services.drive, self)
        if dialog.exec() and dialog.selected:
            self._drive_folder = dialog.selected
            self.drive_label.setText(f"Drive folder: {dialog.selected[1]}")
            self._default_name(dialog.selected[1])

    def _choose_channel(self) -> None:
        tg = ensure_telegram(self, self.services)
        if tg is None:
            return
        try:
            channels = run_with_progress(self, "Loading your channels...", tg.list_channels)
        except Exception as exc:
            show_error(self, "Telegram", exc)
            return
        dialog = TelegramChannelDialog(channels, self)
        if dialog.exec() and dialog.selected:
            self._channel = dialog.selected
            self.channel_label.setText(f"Channel: {dialog.selected[1]}")
            self._default_name(dialog.selected[1])

    def _selected_root(self, source_id: int) -> str | None:
        if source_id == LOCAL:
            return self.path_edit.text() or None
        if source_id == DRIVE:
            return self._drive_folder[0] if self._drive_folder else None
        return str(self._channel[0]) if self._channel else None

    def _submit(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Course name", "Please enter a course name.")
            return
        source_id = self.group.checkedId()
        root = self._selected_root(source_id)
        if root is None:
            QMessageBox.warning(self, "Source", "Please choose a folder or channel first.")
            return
        source = _SOURCES[source_id]
        videos = scan_source(self, self.services, source, root)
        if videos is None:
            return
        if not videos:
            QMessageBox.warning(self, "No videos", "No video files were found there.")
            return
        self.course_id = self.db.add_course(name, source, root, videos)
        self.accept()
