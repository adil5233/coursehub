import zlib

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..models import Course, CourseStats
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


class LibraryView(QWidget):
    open_course = Signal(int)
    add_course = Signal()
    settings_requested = Signal()
    course_menu = Signal(int, QPoint)

    def __init__(self, db, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self._cards: list[CourseCard] = []
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
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(add_button)
        header.addWidget(settings_button)

        self.empty_label = QLabel(
            "No courses yet.\nClick \"+ Add Course\" to add a folder from your PC, Google Drive or a Telegram channel."
        )
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        container = QWidget()
        self.grid = QGridLayout(container)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(GRID_SPACING)
        self.grid.setVerticalSpacing(GRID_SPACING)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
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
        for card in self._cards:
            card.deleteLater()
        self._cards = []
        courses = self.db.list_courses()
        for course in courses:
            card = CourseCard(course, self.db.course_stats(course.id), self._cover_for(course))
            card.open_requested.connect(self.open_course)
            card.menu_requested.connect(self.course_menu)
            self._cards.append(card)
        self.empty_label.setVisible(not courses)
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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()
