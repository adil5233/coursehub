from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from ..config import CACHE_DIR, format_bytes
from .utils import run_with_progress, show_error


class SettingsDialog(QDialog):
    def __init__(self, services, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.services = services

        title = QLabel("Storage")
        title.setObjectName("SectionTitle")

        self.cache_label = QLabel()
        self.cache_label.setObjectName("CardTitle")
        info = QLabel(
            "Video parts downloaded while streaming from Telegram / Google Drive, plus subtitles and thumbnails.\n"
            "Clearing it frees disk space; everything simply downloads again when needed.\n"
            "Your courses, progress, notes and logins are NOT affected."
        )
        info.setObjectName("Muted")
        info.setWordWrap(True)
        path = QLabel(str(CACHE_DIR))
        path.setObjectName("Muted")

        self.clear_button = QPushButton("Clear cache")
        self.clear_button.setObjectName("Primary")
        self.clear_button.clicked.connect(self._clear)
        row = QHBoxLayout()
        row.addWidget(self.cache_label, 1)
        row.addWidget(self.clear_button)

        card = QFrame()
        card.setObjectName("SidePanel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)
        card_layout.addLayout(row)
        card_layout.addWidget(info)
        card_layout.addWidget(path)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(card)
        layout.addWidget(buttons)
        self._refresh()

    def _refresh(self) -> None:
        size = self.services.cache_size()
        self.cache_label.setText(f"Cache: {format_bytes(size)}")
        self.clear_button.setEnabled(size > 0)

    def _clear(self) -> None:
        if QMessageBox.question(
            self, "Clear cache", "Delete all cached video parts and subtitles?"
        ) != QMessageBox.StandardButton.Yes:
            return
        before = self.services.cache_size()
        try:
            run_with_progress(self, "Clearing cache...", self.services.clear_cache)
        except Exception as exc:
            show_error(self, "Clear cache", exc)
        freed = max(0, before - self.services.cache_size())
        self._refresh()
        QMessageBox.information(self, "Clear cache", f"Freed {format_bytes(freed)}.")
