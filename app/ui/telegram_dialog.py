import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QVBoxLayout,
)

CHAT_ID = Qt.ItemDataRole.UserRole


class TelegramApiDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Telegram API setup")
        self.setMinimumWidth(440)
        self.api_id = 0
        self.api_hash = ""

        info = QLabel(
            'Log in at <a href="https://my.telegram.org">my.telegram.org</a> &rarr; '
            "<b>API development tools</b>, create an app and paste its values below. "
            "They are stored only on this PC."
        )
        info.setWordWrap(True)
        info.setOpenExternalLinks(True)

        self.id_edit = QLineEdit()
        self.id_edit.setValidator(QIntValidator(1, 2_147_483_647, self))
        self.hash_edit = QLineEdit()
        self.hash_edit.setEchoMode(QLineEdit.EchoMode.Password)

        form = QFormLayout()
        form.addRow("api_id", self.id_edit)
        form.addRow("api_hash", self.hash_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def accept(self) -> None:
        api_hash = self.hash_edit.text().strip()
        if not self.id_edit.hasAcceptableInput() or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
            QMessageBox.warning(self, "Invalid values", "api_id must be a number and api_hash 32 hex characters.")
            return
        self.api_id = int(self.id_edit.text())
        self.api_hash = api_hash
        super().accept()


class TelegramChannelDialog(QDialog):
    def __init__(self, channels: list[tuple[int, str]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose Telegram channel")
        self.resize(460, 520)
        self.selected: tuple[int, str] | None = None

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Search channels...")
        self.filter_edit.textChanged.connect(self._filter)

        self.list = QListWidget()
        for chat_id, name in channels:
            item = QListWidgetItem(name)
            item.setData(CHAT_ID, chat_id)
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.filter_edit)
        layout.addWidget(self.list, 1)
        layout.addWidget(buttons)

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(needle not in item.text().lower())

    def accept(self) -> None:
        item = self.list.currentItem()
        if item is None or item.isHidden():
            QMessageBox.information(self, "Choose channel", "Please select a channel.")
            return
        self.selected = (item.data(CHAT_ID), item.text())
        super().accept()
