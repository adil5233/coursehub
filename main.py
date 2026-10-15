import os
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from app.config import APP_NAME, DB_PATH, ensure_dirs


def main() -> int:
    # Thumbnail grabbing uses Qt Multimedia, whose FFmpeg backend is very chatty on the console.
    os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia*=false")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    ensure_dirs()

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


if __name__ == "__main__":
    sys.exit(main())
