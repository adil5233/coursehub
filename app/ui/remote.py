"""Connecting to remote sources (Google Drive / Telegram) and scanning any course source."""
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMessageBox, QWidget

from ..models import ScannedVideo
from ..scanner.local_scanner import scan_local
from .telegram_dialog import TelegramApiDialog
from .utils import run_with_progress, show_error

_DRIVE_SETUP = (
    "One-time Google Drive setup:\n\n"
    "1. Open console.cloud.google.com and create a project.\n"
    "2. Enable the 'Google Drive API'.\n"
    "3. Configure the OAuth consent screen (External) and add your Gmail as a test user.\n"
    "4. Credentials > Create OAuth client ID > type 'Desktop app'.\n"
    "5. Download the JSON file.\n\n"
    "Click OK and select that JSON file."
)


def ensure_drive(parent: QWidget, services) -> bool:
    if services.drive is not None:
        return True
    from ..drive import auth

    try:
        creds = run_with_progress(parent, "Connecting to Google Drive...", auth.load_saved)
        if creds is None:
            if not auth.has_client_secrets():
                if QMessageBox.information(
                    parent, "Google Drive setup", _DRIVE_SETUP,
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                ) != QMessageBox.StandardButton.Ok:
                    return False
                path, _ = QFileDialog.getOpenFileName(parent, "Select OAuth client JSON", "", "JSON (*.json)")
                if not path:
                    return False
                auth.install_client_secrets(path)
            creds = run_with_progress(
                parent, "Complete the Google sign-in in your browser...\n(waits up to 5 minutes)", auth.login
            )
    except Exception as exc:
        show_error(parent, "Google Drive", exc)
        return False
    services.set_drive(auth.DriveSession(creds))
    return True


def ensure_telegram(parent: QWidget, services):
    tg = services.get_telegram()
    if tg.is_authorized:
        return tg
    if tg.load_config() is None:
        dialog = TelegramApiDialog(parent)
        if not dialog.exec():
            return None
        tg.save_config(dialog.api_id, dialog.api_hash)
    try:
        if run_with_progress(parent, "Connecting to Telegram...", tg.connect):
            return tg
        phone, ok = QInputDialog.getText(parent, "Telegram login", "Phone number with country code (e.g. +91...):")
        if not ok or not phone.strip():
            return None
        phone = phone.strip()
        run_with_progress(parent, "Sending login code...", tg.send_code, phone)
        code, ok = QInputDialog.getText(parent, "Telegram login", "Enter the code Telegram sent you:")
        if not ok or not code.strip():
            return None
        if run_with_progress(parent, "Signing in...", tg.sign_in, phone, code.strip()):
            password, ok = QInputDialog.getText(
                parent, "Telegram login", "Two-step verification password:", QLineEdit.EchoMode.Password
            )
            if not ok:
                return None
            run_with_progress(parent, "Signing in...", tg.sign_in_password, password)
    except Exception as exc:
        if "ApiId" in type(exc).__name__:
            tg.clear_config()
        show_error(parent, "Telegram login failed", exc)
        return None
    return tg


def ensure_source(parent: QWidget, services, source: str) -> bool:
    if source == "drive":
        return ensure_drive(parent, services)
    if source == "telegram":
        return ensure_telegram(parent, services) is not None
    return True


def scan_source(parent: QWidget, services, source: str, root: str) -> list[ScannedVideo] | None:
    """Returns the scanned videos, or None if cancelled / failed (error already shown)."""
    try:
        if source == "local":
            return run_with_progress(parent, "Scanning folder...", scan_local, root)
        if not ensure_source(parent, services, source):
            return None
        if source == "drive":
            from ..scanner.drive_scanner import scan_drive

            session = services.drive
            return run_with_progress(
                parent, "Scanning Google Drive folder...", lambda: scan_drive(session.service(), root)
            )
        return run_with_progress(parent, "Scanning Telegram channel...", services.telegram.scan, int(root))
    except Exception as exc:
        show_error(parent, "Scan failed", exc)
        return None
