from PySide6.QtCore import QByteArray, QPoint, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMainWindow, QMenu, QMessageBox, QStackedWidget

from ..transcribe.service import TranscriptManager
from .add_course_dialog import AddCourseDialog
from .course_view import CourseView
from .library_view import LibraryView
from .quiz_dialog import QuizDialog
from .remote import scan_source
from .search_dialog import SearchDialog
from .settings_dialog import SettingsDialog
from .thumbnails import ThumbnailManager
from .utils import export_course_notes


class MainWindow(QMainWindow):
    def __init__(self, db, services) -> None:
        super().__init__()
        self.db = db
        self.services = services
        self.setWindowTitle("CourseHub")
        self.setMinimumSize(900, 560)

        self.library = LibraryView(db)
        self.thumbnails = ThumbnailManager(services, self)
        self.transcripts = TranscriptManager(services, self)
        self.course_view = CourseView(db, services, self.thumbnails, self.transcripts)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.library)
        self.stack.addWidget(self.course_view)
        self.setCentralWidget(self.stack)

        self.library.open_course.connect(lambda course_id: self.open_course(course_id, autoplay=True))
        self.library.continue_lecture.connect(self.continue_lecture)
        self.library.revise_lecture.connect(self.revise_lecture)
        self.library.add_course.connect(self.add_course)
        self.library.settings_requested.connect(lambda: SettingsDialog(self.services, self).exec())
        self.library.course_menu.connect(self.show_course_menu)
        self.library.search_requested.connect(self.open_search)
        self.course_view.back_requested.connect(self.show_library)
        self.course_view.search_requested.connect(self.open_search)
        search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        search_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        search_shortcut.activated.connect(self.open_search)

    def open_search(self) -> None:
        self.course_view.exit_fullscreen()
        dialog = SearchDialog(self.db, self)
        dialog.jump_requested.connect(self.jump_to)
        dialog.exec()
        dialog.deleteLater()

    def jump_to(self, course_id: int, video_id: int, time_ms: int) -> None:
        """Open a lecture at a moment (from search results)."""
        view = self.course_view
        if view.course is not None and view.course.id == course_id and self.stack.currentWidget() is view:
            view.jump_to(video_id, time_ms)
            return
        self.db.save_position(video_id, time_ms)
        self.continue_lecture(course_id, video_id)

    def show_fitted(self) -> None:
        """Restore the last window size, but never let the player controls fall off-screen."""
        available = self.screen().availableGeometry()
        saved = self.db.get_state("window_geometry")
        restored = bool(saved) and self.restoreGeometry(QByteArray.fromHex(saved.encode()))
        if restored and (self.isMaximized() or available.contains(self.geometry())):
            self.show()
        else:
            self.showMaximized()

    def restore_session(self) -> None:
        """Reopen the course that was open when the app was closed (paused at the saved second)."""
        last = self.db.get_state("last_course_id")
        if last and last.isdigit() and self.db.get_course(int(last)) is not None:
            self.open_course(int(last), autoplay=False)
        else:
            self.show_library()

    def open_course(self, course_id: int, autoplay: bool = True) -> None:
        self.stack.setCurrentWidget(self.course_view)
        if self.course_view.open_course(course_id, autoplay):
            self.db.set_state("last_course_id", str(course_id))
        else:
            self.show_library()

    def revise_lecture(self, course_id: int, video_id: int) -> None:
        course, video = self.db.get_course(course_id), self.db.get_video(video_id)
        if course is None or video is None:
            return
        dialog = QuizDialog(self.db, course, video, self)
        dialog.jump_requested.connect(self.jump_to)
        dialog.exec()
        dialog.deleteLater()
        if self.stack.currentWidget() is self.library:
            self.library.refresh()

    def continue_lecture(self, course_id: int, video_id: int) -> None:
        self.db.set_last_video(course_id, video_id)  # open_course resumes the course's last lecture
        self.open_course(course_id, autoplay=True)

    def show_library(self) -> None:
        self.course_view.close_course()
        self.db.set_state("last_course_id", None)
        self.library.refresh()
        self.stack.setCurrentWidget(self.library)

    def add_course(self) -> None:
        dialog = AddCourseDialog(self.db, self.services, self)
        if dialog.exec() and dialog.course_id is not None:
            self.library.refresh()
            self.open_course(dialog.course_id, autoplay=True)

    def show_course_menu(self, course_id: int, pos: QPoint) -> None:
        course = self.db.get_course(course_id)
        if course is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction("Open")
        rename_action = menu.addAction("Rename...")
        rescan_action = menu.addAction("Sync videos (check for new)")
        transcribe_action = menu.addAction("Transcribe whole course")
        export_action = menu.addAction("Export notes (Markdown)...")
        reset_action = menu.addAction("Reset progress")
        menu.addSeparator()
        remove_action = menu.addAction("Remove course")
        chosen = menu.exec(pos)

        if chosen is open_action:
            self.open_course(course_id)
        elif chosen is transcribe_action:
            self.open_course(course_id, autoplay=False)
            if self.course_view.course is not None:
                self.course_view.show_transcript()
                self.course_view.transcribe_course()
            return
        elif chosen is export_action:
            export_course_notes(self, self.db, course)
        elif chosen is rename_action:
            name, ok = QInputDialog.getText(self, "Rename course", "Course name:", QLineEdit.EchoMode.Normal, course.name)
            if ok and name.strip():
                self.db.rename_course(course_id, name.strip())
        elif chosen is rescan_action:
            videos = scan_source(self, self.services, course.source, course.root)
            if videos is None:
                return
            if not videos:
                QMessageBox.warning(self, "No videos", "No videos found, so the course was left unchanged.")
                return
            added, removed = self.db.sync_videos(course_id, videos)
            QMessageBox.information(self, "Sync complete", f"{added} new video(s) added, {removed} removed.")
        elif chosen is reset_action:
            if QMessageBox.question(self, "Reset progress", f"Reset all progress for \"{course.name}\"?") \
                    == QMessageBox.StandardButton.Yes:
                self.db.reset_progress(course_id)
        elif chosen is remove_action:
            if QMessageBox.question(
                self, "Remove course",
                f"Remove \"{course.name}\" from CourseHub?\nYour video files are NOT deleted.",
            ) == QMessageBox.StandardButton.Yes:
                self.db.delete_course(course_id)
        self.library.refresh()

    def closeEvent(self, event) -> None:
        self.course_view.exit_fullscreen()
        self.course_view.shutdown()
        self.thumbnails.shutdown()
        self.transcripts.shutdown()
        self.db.set_state("window_geometry", bytes(self.saveGeometry().toHex()).decode())
        super().closeEvent(event)
