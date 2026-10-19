import zlib
from datetime import date

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..config import display_title, format_ms
from ..models import Course, CourseStats, Video
from ..player.vlc_widget import glyph_icon
from .thumbnails import cover_pixmap, load_thumb

SOURCE_LABELS = {"local": "Local folder", "drive": "Google Drive", "telegram": "Telegram"}
CARD_WIDTH = 300
GRID_SPACING = 20
_THUMB_COLORS = [
    ("#3a1c71", "#d76d77"), ("#0f2027", "#2c5364"), ("#41295a", "#2f0743"), ("#1d4350", "#a43931"),
    ("#232526", "#414345"), ("#134e5e", "#71b280"), ("#4b134f", "#c94b4b"), ("#16222a", "#3a6073"),
]


def _initials(name: str) -> str:
    words = [w for w in name.replace("=", " ").split() if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "?"


class CourseCard(QFrame):
    open_requested = Signal(int)
    menu_requested = Signal(int, QPoint)

    def __init__(self, course: Course, stats: CourseStats, cover: QPixmap | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CourseCard")
        self.setFixedWidth(CARD_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.course_id = course.id

        thumb_height = CARD_WIDTH * 9 // 16
        if cover is not None:
            ratio = self.devicePixelRatioF()
            image = cover_pixmap(cover, int(CARD_WIDTH * ratio), int(thumb_height * ratio), 12 * ratio)
            image.setDevicePixelRatio(ratio)
            thumb = QLabel()
            thumb.setPixmap(image)
        else:
            start, end = _THUMB_COLORS[zlib.crc32(course.name.encode()) % len(_THUMB_COLORS)]
            thumb = QLabel(_initials(course.name))
            thumb.setObjectName("Thumb")
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb.setStyleSheet(
                f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {start}, stop:1 {end});"
            )
        thumb.setFixedSize(CARD_WIDTH, thumb_height)
        watched = QProgressBar()
        watched.setRange(0, 100)
        watched.setValue(stats.percent)
        watched.setTextVisible(False)
        watched.setFixedHeight(4)

        title = QLabel(course.name)
        title.setObjectName("CardTitle")
        title.setWordWrap(True)
        meta = QLabel(f"{SOURCE_LABELS.get(course.source, course.source)}  \u2022  {stats.total} lectures")
        meta.setObjectName("Muted")
        started = stats.completed > 0 or course.last_video_id is not None
        status = f"{stats.percent}% complete  \u2022  {stats.completed}/{stats.total} done" if started else "Not started"
        progress = QLabel(status)
        progress.setObjectName("Muted")

        text = QVBoxLayout()
        text.setContentsMargins(4, 8, 4, 10)
        text.setSpacing(3)
        text.addWidget(title)
        text.addWidget(meta)
        text.addWidget(progress)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(thumb)
        layout.addWidget(watched)
        layout.addLayout(text)
        self.setToolTip("Click to continue  \u2022  Right-click for options")

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.open_requested.emit(self.course_id)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.menu_requested.emit(self.course_id, event.globalPos())


class ContinueCard(QFrame):
    """A lecture to resume: its thumbnail with watch progress, title and course."""

    open_requested = Signal(int, int)  # course_id, video_id

    def __init__(self, course: Course, video: Video, number: int, total: int, meta_text: str | None = None,
                 tooltip: str = "Click to continue this lecture", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CourseCard")
        self.setFixedWidth(CARD_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.course_id, self.video_id = course.id, video.id

        thumb_height = CARD_WIDTH * 9 // 16
        ratio = self.devicePixelRatioF()
        image = cover_pixmap(load_thumb(video.id), int(CARD_WIDTH * ratio), int(thumb_height * ratio), 12 * ratio)
        image.setDevicePixelRatio(ratio)
        thumb = QLabel()
        thumb.setPixmap(image)
        thumb.setFixedSize(CARD_WIDTH, thumb_height)
        watched = QProgressBar()
        watched.setRange(0, 100)
        position = min(video.last_position_ms, video.duration_ms)
        watched.setValue(int(position * 100 / video.duration_ms) if video.duration_ms else 0)
        watched.setTextVisible(False)
        watched.setFixedHeight(4)

        title = QLabel(display_title(video.title))
        title.setObjectName("CardTitle")
        title.setWordWrap(True)
        course_label = QLabel(course.name)
        course_label.setObjectName("Muted")
        parts = [f"Lecture {number} of {total}"]
        if video.duration_ms:
            left = video.duration_ms - position
            parts.append(f"{format_ms(left)} left" if position > 0 else format_ms(video.duration_ms))
        meta = QLabel(meta_text or "  \u2022  ".join(parts))
        meta.setObjectName("Muted")

        text = QVBoxLayout()
        text.setContentsMargins(4, 8, 4, 10)
        text.setSpacing(3)
        text.addWidget(title)
        text.addWidget(course_label)
        text.addWidget(meta)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(thumb)
        layout.addWidget(watched)
        layout.addLayout(text)
        self.setToolTip(tooltip)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.open_requested.emit(self.course_id, self.video_id)
        super().mouseReleaseEvent(event)


class LibraryView(QWidget):
    open_course = Signal(int)
    continue_lecture = Signal(int, int)  # course_id, video_id
    revise_lecture = Signal(int, int)
    add_course = Signal()
    settings_requested = Signal()
    search_requested = Signal()
    course_menu = Signal(int, QPoint)

    def __init__(self, db, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self._cards: list[CourseCard] = []
        self._continue_cards: list[ContinueCard] = []
        self._review_cards: list[ContinueCard] = []
        self._columns = 0

        title = QLabel("My Courses")
        title.setObjectName("PageTitle")
        add_button = QPushButton("+  Add Course")
        add_button.setObjectName("Primary")
        add_button.clicked.connect(self.add_course)
        settings_button = QPushButton("Settings")
        settings_button.setIcon(glyph_icon("gear"))
        settings_button.setToolTip("Settings (clear cache)")
        settings_button.clicked.connect(lambda: self.settings_requested.emit())
        search_button = QPushButton("Search")
        search_button.setToolTip("Search every note and transcript (Ctrl+K)")
        search_button.clicked.connect(lambda: self.search_requested.emit())
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(search_button)
        header.addWidget(add_button)
        header.addWidget(settings_button)

        self.empty_label = QLabel(
            "No courses yet.\nClick \"+ Add Course\" to add a folder from your PC, Google Drive or a Telegram channel."
        )
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        container = QWidget()
        self.review_title = QLabel("Revise today")
        self.review_title.setObjectName("SectionTitle")
        self.review_row = QHBoxLayout()
        self.review_row.setSpacing(GRID_SPACING)
        self.review_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.continue_title = QLabel("Continue watching")
        self.continue_title.setObjectName("SectionTitle")
        self.continue_row = QHBoxLayout()
        self.continue_row.setSpacing(GRID_SPACING)
        self.continue_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.all_title = QLabel("All courses")
        self.all_title.setObjectName("SectionTitle")
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(GRID_SPACING)
        self.grid.setVerticalSpacing(GRID_SPACING)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        content = QVBoxLayout(container)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(12)
        content.addWidget(self.review_title)
        content.addLayout(self.review_row)
        content.addSpacing(12)
        content.addWidget(self.continue_title)
        content.addLayout(self.continue_row)
        content.addSpacing(12)
        content.addWidget(self.all_title)
        content.addLayout(self.grid)
        content.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(container)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.addLayout(header)
        layout.addSpacing(16)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.scroll, 1)

    def _cover_for(self, course: Course) -> QPixmap | None:
        """Thumbnail of the last watched lecture, else of the first lectures that have one."""
        candidates = [course.last_video_id] + [v.id for v in self.db.get_videos(course.id)[:5]]
        for video_id in candidates:
            if video_id is not None and (pixmap := load_thumb(video_id)) is not None:
                return pixmap
        return None

    def refresh(self) -> None:
        for card in self._cards + self._continue_cards + self._review_cards:
            card.deleteLater()
        self._cards = []
        self._continue_cards = []
        self._review_cards = []
        courses = self.db.list_courses()
        for course in courses:
            card = CourseCard(course, self.db.course_stats(course.id), self._cover_for(course))
            card.open_requested.connect(self.open_course)
            card.menu_requested.connect(self.course_menu)
            self._cards.append(card)
        for course, video in self.db.continue_watching():
            numbers = [v.id for v in self.db.get_videos(course.id)]
            card = ContinueCard(course, video, numbers.index(video.id) + 1, len(numbers))
            card.open_requested.connect(self.continue_lecture)
            self._continue_cards.append(card)
        for course, video, review in self.db.due_reviews(date.today()):
            numbers = [v.id for v in self.db.get_videos(course.id)]
            if video.id not in numbers:
                continue
            score = review.get("last_score")
            meta = (f"Quiz  \u2022  last score {round(score * 100)}%" if score is not None
                    else "Quiz  \u2022  first revision")
            card = ContinueCard(course, video, numbers.index(video.id) + 1, len(numbers), meta,
                                "Click for a quick revision quiz")
            card.open_requested.connect(self.revise_lecture)
            self._review_cards.append(card)
        self.empty_label.setVisible(not courses)
        self.review_title.setText(f"Revise today ({len(self._review_cards)})")
        self.review_title.setVisible(bool(self._review_cards))
        self.continue_title.setVisible(bool(self._continue_cards))
        self.all_title.setVisible(bool(self._continue_cards or self._review_cards))
        self._columns = 0
        self._relayout()

    def _relayout(self) -> None:
        available = self.scroll.viewport().width()
        columns = max(1, (available + GRID_SPACING) // (CARD_WIDTH + GRID_SPACING))
        if columns == self._columns:
            return
        self._columns = columns
        for card in self._cards:
            self.grid.removeWidget(card)
        for index, card in enumerate(self._cards):
            self.grid.addWidget(card, index // columns, index % columns)
        for index, card in enumerate(self._continue_cards):  # one row: as many as fit
            self.continue_row.removeWidget(card)
            if index < columns:
                self.continue_row.addWidget(card)
            card.setVisible(index < columns)
        for index, card in enumerate(self._review_cards):
            self.review_row.removeWidget(card)
            if index < columns:
                self.review_row.addWidget(card)
            card.setVisible(index < columns)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()
