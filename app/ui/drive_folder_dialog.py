from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QLineEdit, QMessageBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from ..scanner.drive_scanner import SHARED_WITH_ME, get_folder_name, list_subfolders, parse_folder_id
from .utils import show_error, wait_cursor

FOLDER_ID = Qt.ItemDataRole.UserRole
LOADED = Qt.ItemDataRole.UserRole + 1


class DriveFolderDialog(QDialog):
    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose Google Drive folder")
        self.resize(520, 540)
        self.service = session.service()
        self.selected: tuple[str, str] | None = None

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemExpanded.connect(self._load_children)
        self.tree.itemDoubleClicked.connect(lambda *_: self.accept())
        for folder_id, label in (("root", "My Drive"), (SHARED_WITH_ME, "Shared with me")):
            self.tree.addTopLevelItem(self._folder_item(folder_id, label))

        self.link_edit = QLineEdit()
        self.link_edit.setPlaceholderText("...or paste a Google Drive folder link here")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select the course folder (sub-folders become sections):"))
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.link_edit)
        layout.addWidget(buttons)

    @staticmethod
    def _folder_item(folder_id: str, name: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([name])
        item.setData(0, FOLDER_ID, folder_id)
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        return item

    def _load_children(self, item: QTreeWidgetItem) -> None:
        if item.data(0, LOADED):
            return
        try:
            with wait_cursor():
                folders = list_subfolders(self.service, item.data(0, FOLDER_ID))
        except Exception as exc:
            show_error(self, "Google Drive", exc)
            return
        item.setData(0, LOADED, True)
        for folder_id, name in folders:
            item.addChild(self._folder_item(folder_id, name))
        if not folders:
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator)

    def accept(self) -> None:
        link = self.link_edit.text().strip()
        if link:
            folder_id = parse_folder_id(link)
            if folder_id is None:
                QMessageBox.warning(self, "Invalid link", "That does not look like a Google Drive folder link.")
                return
            try:
                with wait_cursor():
                    name = get_folder_name(self.service, folder_id)
            except Exception as exc:
                show_error(self, "Google Drive", exc)
                return
            self.selected = (folder_id, name)
        else:
            item = self.tree.currentItem()
            if item is None or item.data(0, FOLDER_ID) == SHARED_WITH_ME:
                QMessageBox.information(self, "Choose folder", "Please select a folder.")
                return
            self.selected = (item.data(0, FOLDER_ID), item.text(0))
        super().accept()
