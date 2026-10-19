from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout,
)

from ..config import CACHE_DIR, TRANSCRIPTS_DIR, format_bytes
from ..transcribe import groq_client
from .utils import run_with_progress, show_error

GROQ_FREE_SECONDS_PER_DAY = 8 * 3600


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
        info.setObjectName("Muted")  # no word wrap: wrapped labels make Qt size this dialog too short
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
        layout.addSpacing(6)
        layout.addWidget(self._transcripts_section())
        layout.addWidget(buttons)
        self._refresh()

    def _transcripts_section(self) -> QFrame:
        settings = groq_client.load_settings()
        section = QFrame()
        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        title = QLabel("Transcripts")
        title.setObjectName("SectionTitle")

        info = QLabel(
            "Lecture transcripts and subtitles are made with Whisper on Groq's free API.<br>"
            'Get a free key at <a href="https://console.groq.com/keys">console.groq.com/keys</a> and paste it here.'
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setOpenExternalLinks(True)
        info.setObjectName("Muted")

        self.key_edit = QLineEdit(settings["api_key"])
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("gsk_...")
        self.key_edit.editingFinished.connect(self._save_groq)
        test_button = QPushButton("Save && test")
        test_button.clicked.connect(self._test_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(test_button)

        self.auto_check = QCheckBox("Automatically transcribe the lecture I open")
        self.auto_check.setChecked(settings["auto"])
        self.auto_check.toggled.connect(self._save_groq)

        used = groq_client.audio_seconds_today(settings)
        usage = QLabel(f"Used today: {used / 60:.0f} min of audio (free limit about "
                       f"{GROQ_FREE_SECONDS_PER_DAY // 3600} hours / day)\nSaved in {TRANSCRIPTS_DIR}")
        usage.setObjectName("Muted")

        card = QFrame()
        card.setObjectName("SidePanel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)
        card_layout.addWidget(info)
        card_layout.addLayout(key_row)
        card_layout.addWidget(self.auto_check)
        card_layout.addWidget(usage)

        # Lines are broken by hand like the Storage text (word wrap makes Qt size the dialog too short).
        backup_info = QLabel("Backup AI (optional): when every Groq model has used up its free limit,\n"
                             "steps, quizzes and answers continue on OpenRouter's free models\n"
                             "(about 50 requests a day).")
        backup_info.setObjectName("Muted")
        backup_links = QLabel(
            'Free key: <a href="https://openrouter.ai/keys">openrouter.ai/keys</a>  •  Requests fail? '
            '<a href="https://openrouter.ai/settings/privacy">Allow free models</a>'
        )
        backup_links.setTextFormat(Qt.TextFormat.RichText)
        backup_links.setOpenExternalLinks(True)
        backup_links.setObjectName("Muted")
        self.openrouter_edit = QLineEdit(settings["openrouter_key"])
        self.openrouter_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.openrouter_edit.setPlaceholderText("sk-or-...")
        self.openrouter_edit.editingFinished.connect(self._save_groq)
        openrouter_test = QPushButton("Save && test")
        openrouter_test.clicked.connect(self._test_openrouter_key)
        openrouter_row = QHBoxLayout()
        openrouter_row.addWidget(self.openrouter_edit, 1)
        openrouter_row.addWidget(openrouter_test)
        card_layout.addSpacing(6)
        card_layout.addWidget(backup_info)
        card_layout.addWidget(backup_links)
        card_layout.addLayout(openrouter_row)
        outer.addWidget(title)
        outer.addWidget(card)
        return section

    def _save_groq(self) -> None:
        settings = groq_client.load_settings()
        settings["api_key"] = self.key_edit.text().strip()
        settings["auto"] = self.auto_check.isChecked()
        settings["openrouter_key"] = self.openrouter_edit.text().strip()
        groq_client.save_settings(settings)

    def _test_key(self) -> None:
        self._save_groq()
        key = self.key_edit.text().strip()
        if not key:
            QMessageBox.warning(self, "Groq API key", "Paste your Groq API key first.")
            return
        try:
            run_with_progress(self, "Checking the key with Groq...", groq_client.check_key, key)
        except Exception as exc:
            show_error(self, "Groq API key", exc)
            return
        QMessageBox.information(self, "Groq API key", "Key works. Transcripts are ready to use.")

    def _test_openrouter_key(self) -> None:
        self._save_groq()
        key = self.openrouter_edit.text().strip()
        if not key:
            QMessageBox.warning(self, "OpenRouter API key", "Paste your OpenRouter API key first.")
            return
        try:
            run_with_progress(self, "Checking the key with OpenRouter...", groq_client.check_openrouter_key, key)
        except Exception as exc:
            show_error(self, "OpenRouter API key", exc)
            return
        QMessageBox.information(self, "OpenRouter API key",
                                "Key works. OpenRouter's free models will take over when Groq's limit runs out.")

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
