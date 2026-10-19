import threading
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QProgressDialog, QWidget

from ..notes_export import notes_markdown, revision_file_name, revision_markdown, revision_questions


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


def export_course_notes(parent: QWidget | None, db, course) -> None:
    """Ask where to save, then write every note of the course as one Markdown file."""
    numbers = {video.id: index for index, video in enumerate(db.get_videos(course.id), 1)}
    safe_name = "".join(c for c in course.name if c not in '<>:"/\\|?*').strip() or "course"
    default = Path.home() / "Documents" / f"{safe_name} - notes.md"
    path, _ = QFileDialog.getSaveFileName(parent, "Export notes", str(default), "Markdown (*.md);;Text (*.txt)")
    if not path:
        return
    items = db.course_notes(course.id)
    try:
        Path(path).write_text(notes_markdown(course.name, items, numbers), encoding="utf-8")
    except OSError as exc:
        show_error(parent, "Export notes", exc)
        return
    QMessageBox.information(parent, "Export notes", f"Saved {len(items)} note(s) to:\n{path}")


def export_revision_notes(parent: QWidget | None, db, course) -> None:
    """One review.py-style file per lecture (QUESTIONS / ANSWERS / MERI GALTI / EK LINE ME) in a chosen folder."""
    from ..transcribe.steps import load_steps

    folder = QFileDialog.getExistingDirectory(parent, "Export revision notes (one file per lecture)",
                                              str(Path.home() / "Documents"))
    if not folder:
        return
    written = 0
    try:
        for number, video in enumerate(db.get_videos(course.id), 1):
            steps_data = load_steps(video.id)
            pairs = revision_questions(steps_data, db.get_open_quiz(video.id))
            mistakes = db.mistakes_for_video(video.id)
            if not pairs and not mistakes:
                continue
            first_line = next((s.get("ek_line") for s in steps_data["steps"] if s.get("ek_line")), "")
            text = revision_markdown(number, video.title, pairs, mistakes, steps_data["mantra"] or first_line)
            (Path(folder) / revision_file_name(number, video.title)).write_text(text, encoding="utf-8")
            written += 1
    except OSError as exc:
        show_error(parent, "Export revision notes", exc)
        return
    if written:
        QMessageBox.information(parent, "Export revision notes", f"Saved {written} lecture file(s) to:\n{folder}")
    else:
        QMessageBox.information(parent, "Export revision notes",
                                "Nothing to export yet: make study steps (Steps tab) or an own-words quiz first.")
