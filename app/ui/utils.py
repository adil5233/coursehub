import threading
from contextlib import contextmanager

from PySide6.QtCore import QEventLoop, QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget


class _Finished(QObject):
    done = Signal()


def run_with_progress(parent: QWidget | None, text: str, fn, *args):
    """Run a blocking call in a thread while the UI stays responsive; returns its result or re-raises."""
    box: dict = {}
    finished = _Finished()
    loop = QEventLoop()
    finished.done.connect(loop.quit, Qt.ConnectionType.QueuedConnection)

    def target() -> None:
        try:
            box["value"] = fn(*args)
        except BaseException as exc:
            box["error"] = exc
        finally:
            finished.done.emit()

    dialog = QProgressDialog(text, "", 0, 0, parent)
    dialog.setCancelButton(None)
    dialog.setWindowTitle("Please wait")
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.show()
    threading.Thread(target=target, daemon=True).start()
    loop.exec()
    dialog.close()
    dialog.deleteLater()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def error_text(exc: BaseException) -> str:
    seconds = getattr(exc, "seconds", None)
    if type(exc).__name__ == "FloodWaitError" and seconds:
        return f"Telegram rate limit reached. Please try again after {seconds} seconds."
    return str(exc) or type(exc).__name__


def show_error(parent: QWidget | None, title: str, exc: BaseException) -> None:
    QMessageBox.warning(parent, title, error_text(exc))


@contextmanager
def wait_cursor():
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()
