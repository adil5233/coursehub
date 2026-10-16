import ctypes
import os
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from app.config import APP_NAME, DATA_DIR, DB_PATH, ensure_dirs

AV_LOG_QUIET = -8


def quiet_ffmpeg() -> None:
    """Qt Multimedia's FFmpeg prints hardware-encoder probing ('could not find any MFT') to the console."""
    import PySide6

    for dll in Path(PySide6.__file__).parent.glob("avutil-*.dll"):
        try:
            ctypes.CDLL(str(dll)).av_log_set_level(AV_LOG_QUIET)
        except (OSError, AttributeError):
            pass


def main() -> int:
    # Thumbnail grabbing uses Qt Multimedia, whose FFmpeg backend is very chatty on the console.
    os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia*=false")
    quiet_ffmpeg()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    ensure_dirs()

    # Two copies would fight over the Telegram session file ("database is locked").
    lock = QLockFile(str(DATA_DIR / "coursehub.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, APP_NAME, "CourseHub is already running.")
        return 0

    try:
        import vlc

        if vlc.Instance() is None:
            raise RuntimeError("libVLC failed to start.")
    except Exception as exc:
        QMessageBox.critical(
            None, "VLC not found",
            "CourseHub uses VLC to play every video format.\n\n"
            "Install VLC media player (64-bit) from https://www.videolan.org and start CourseHub again.\n\n"
            f"Details: {exc}",
        )
        return 1

    from app.db import Database
    from app.services import Services
    from app.ui.main_window import MainWindow
    from app.ui.theme import apply_theme

    apply_theme(app)
    db = Database(DB_PATH)
    services = Services()
    window = MainWindow(db, services)
    window.show_fitted()
    window.restore_session()
    try:
        return app.exec()
    finally:
        services.shutdown()
        db.close()
        lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
