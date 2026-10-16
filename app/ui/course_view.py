from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu, QMessageBox, QProgressBar, QPushButton, QSplitter,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..config import (
    AUTOPLAY_DELAY_S, COMPLETE_TOLERANCE_MS, SAVE_INTERVAL_MS, UP_NEXT_LEAD_MS, display_title, format_ms,
)
from ..models import Course, Video
from ..player.vlc_widget import SEEK_STEP_MS, VOLUME_STEP, VideoPlayer, glyph_icon
from .notes_panel import NotesPanel
from .remote import ensure_source, scan_source
from .theme import ACCENT
from .thumbnails import cover_pixmap, load_thumb
from .utils import show_error

VIDEO_ID = Qt.ItemDataRole.UserRole
COMPLETE_COLOR = "#2ba640"
THUMB_W, THUMB_H = 120, 68
_SCALE = 2  # draw at 2x so thumbnails stay sharp on high-DPI screens


def _thumb_icon(base: QPixmap | None, video: Video, current: bool) -> QIcon:
    width, height = THUMB_W * _SCALE, THUMB_H * _SCALE
    pixmap = cover_pixmap(base, width, height)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, width, height), 8 * _SCALE, 8 * _SCALE)
    painter.setClipPath(clip)
    if base is None:
        glyph = glyph_icon("play", "#5a5a5a").pixmap(26 * _SCALE, 26 * _SCALE)
        painter.drawPixmap((width - glyph.width()) // 2, (height - glyph.height()) // 2, glyph)

    bar = 4 * _SCALE
    progress = 1.0 if video.completed else (video.last_position_ms / video.duration_ms if video.duration_ms else 0)
    if progress > 0:
        painter.fillRect(QRectF(0, height - bar, width, bar), QColor(255, 255, 255, 80))
        painter.fillRect(QRectF(0, height - bar, width * min(progress, 1.0), bar), QColor(ACCENT))

    if video.duration_ms:
        font = QFont()
        font.setBold(True)
        font.setPixelSize(11 * _SCALE)
        painter.setFont(font)
        text = format_ms(video.duration_ms)
        text_width = painter.fontMetrics().horizontalAdvance(text) + 8 * _SCALE
        badge = QRectF(width - text_width - 4 * _SCALE, height - bar - 20 * _SCALE, text_width, 16 * _SCALE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 200))
        painter.drawRoundedRect(badge, 4 * _SCALE, 4 * _SCALE)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

    if current:
        painter.fillRect(QRectF(0, 0, width, height), QColor(0, 0, 0, 120))
        glyph = glyph_icon("play", ACCENT).pixmap(30 * _SCALE, 30 * _SCALE)
        painter.drawPixmap((width - glyph.width()) // 2, (height - glyph.height()) // 2, glyph)
    elif video.completed:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 190))
        painter.drawEllipse(QRectF(4 * _SCALE, 4 * _SCALE, 20 * _SCALE, 20 * _SCALE))
        check = glyph_icon("check", COMPLETE_COLOR).pixmap(16 * _SCALE, 16 * _SCALE)
        painter.drawPixmap(6 * _SCALE, 6 * _SCALE, check)
    painter.end()
    pixmap.setDevicePixelRatio(_SCALE)
    return QIcon(pixmap)


def _thin_bar(width: int | None = None) -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setTextVisible(False)
    bar.setFixedHeight(4)
    if width:
        bar.setFixedWidth(width)
    return bar


class CourseView(QWidget):
    back_requested = Signal()

    def __init__(self, db, services, thumbnails, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.services = services
        self.thumbnails = thumbnails
        self.thumbnails.ready.connect(self._on_thumbnail_ready)
        self._thumbs: dict[int, QPixmap | None] = {}
        self.course: Course | None = None
        self.videos: list[Video] = []
        self.current: Video | None = None
        self._items: dict[int, QTreeWidgetItem] = {}
        self._sections: list[tuple[QTreeWidgetItem, str, list[Video]]] = []
        self._next_id: int | None = None
        self._up_next_early = False
        self._up_next_dismissed = False
        self._was_maximized = False
        self._notes_visible = db.get_state("notes_visible", "1") == "1"
        self._playlist_visible = db.get_state("playlist_visible", "1") == "1"

        # ---- top bar ----
        self.top_bar = QFrame()
        self.top_bar.setObjectName("TopBar")
        back_button = QPushButton("\u2190  My Courses")
        back_button.setObjectName("Ghost")
        back_button.clicked.connect(self.back_requested)
        self.course_label = QLabel()
        self.course_label.setObjectName("CardTitle")
        self.progress_label = QLabel()
        self.progress_label.setObjectName("Muted")
        self.progress_bar = _thin_bar(160)
        sync_button = QPushButton("Sync")
        sync_button.setToolTip("Check the folder / Drive / Telegram chat for new or removed videos")
        sync_button.clicked.connect(self.sync_course)
        top = QHBoxLayout(self.top_bar)
        top.setContentsMargins(12, 8, 20, 8)
        top.setSpacing(12)
        top.addWidget(back_button)
        top.addWidget(self.course_label, 1)
        top.addWidget(sync_button)
        top.addWidget(self.progress_label)
        top.addWidget(self.progress_bar)

        # ---- player ----
        self.player = VideoPlayer()
        self.player.position_changed.connect(self._on_position)
        self.player.ended.connect(self._on_ended)
        self.player.error.connect(lambda message: QMessageBox.warning(self, "Playback error", message))
        self.player.fullscreen_requested.connect(self.toggle_fullscreen)
        self.player.theater_requested.connect(lambda: self.set_playlist_visible(not self._playlist_visible))
        self.player.up_next_accepted.connect(self._play_next_now)
        self.player.up_next_cancelled.connect(self._dismiss_up_next)

        # ---- title + actions under the video (YouTube style) ----
        self.video_title = QLabel()
        self.video_title.setObjectName("VideoTitle")
        self.video_title.setWordWrap(True)
        self.video_meta = QLabel()
        self.video_meta.setObjectName("Muted")

        self.prev_btn = QPushButton("\u2039  Previous")
        self.prev_btn.setToolTip("Previous lecture (P)")
        self.prev_btn.clicked.connect(lambda: self._play_neighbor(-1))
        self.next_btn = QPushButton("Next  \u203a")
        self.next_btn.setToolTip("Next lecture (N)")
        self.next_btn.clicked.connect(lambda: self._play_neighbor(1))
        self.complete_btn = QPushButton("Mark as complete")
        self.complete_btn.setCheckable(True)
        self.complete_btn.clicked.connect(self._toggle_current_complete)
        self.notes_btn = QPushButton("Notes")
        self.notes_btn.setCheckable(True)
        self.notes_btn.setToolTip("Show / hide notes")
        self.notes_btn.toggled.connect(self.set_notes_visible)
        self.playlist_btn = QPushButton("Playlist")
        self.playlist_btn.setCheckable(True)
        self.playlist_btn.setToolTip("Show / hide course content (T)")
        self.playlist_btn.toggled.connect(self.set_playlist_visible)
        for button in (self.prev_btn, self.next_btn, self.complete_btn, self.notes_btn, self.playlist_btn):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        for button in (self.prev_btn, self.next_btn, self.complete_btn):
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(self.notes_btn)
        actions.addWidget(self.playlist_btn)

        self.info = QWidget()
        info_layout = QVBoxLayout(self.info)
        info_layout.setContentsMargins(2, 4, 2, 0)
        info_layout.setSpacing(4)
        info_layout.addWidget(self.video_title)
        info_layout.addWidget(self.video_meta)
        info_layout.addSpacing(6)
        info_layout.addLayout(actions)

        video_block = QWidget()
        video_layout = QVBoxLayout(video_block)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(10)
        video_layout.addWidget(self.player, 100)
        video_layout.addWidget(self.info)
        video_layout.addStretch(1)
        self._video_layout = video_layout

        self.notes = NotesPanel(db, self.player.time)
        self.notes.seek_requested.connect(self.player.seek)
        self.notes.hide_requested.connect(lambda: self.set_notes_visible(False))

        self.left_split = QSplitter(Qt.Orientation.Vertical)
        self.left_split.setChildrenCollapsible(False)
        self.left_split.setHandleWidth(10)
        self.left_split.addWidget(video_block)
        self.left_split.addWidget(self.notes)
        self.left_split.setStretchFactor(0, 1)
        self.left_split.setSizes([640, 210])

        # ---- playlist side panel ----
        self.side_panel = QFrame()
        self.side_panel.setObjectName("SidePanel")
        self.side_panel.setMinimumWidth(360)
        side_title = QLabel("Course content")
        side_title.setObjectName("SectionTitle")
        self.side_meta = QLabel()
        self.side_meta.setObjectName("Muted")
        self.side_progress = _thin_bar()

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setIndentation(8)
        self.tree.setIconSize(QSize(THUMB_W, THUMB_H))
        self.tree.setWordWrap(True)
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.itemClicked.connect(self._on_item_activated)

        side_layout = QVBoxLayout(self.side_panel)
        side_layout.setContentsMargins(14, 14, 8, 10)
        side_layout.setSpacing(6)
        side_layout.addWidget(side_title)
        side_layout.addWidget(self.side_meta)
        side_layout.addWidget(self.side_progress)
        side_layout.addSpacing(6)
        side_layout.addWidget(self.tree, 1)

        body_split = QSplitter(Qt.Orientation.Horizontal)
        body_split.setChildrenCollapsible(False)
        body_split.setHandleWidth(16)
        body_split.addWidget(self.left_split)
        body_split.addWidget(self.side_panel)
        body_split.setStretchFactor(0, 1)
        body_split.setSizes([960, 420])

        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(16, 12, 16, 12)
        self.body_layout.addWidget(body_split)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.top_bar)
        layout.addWidget(body, 1)

        self.save_timer = QTimer(self)
        self.save_timer.setInterval(SAVE_INTERVAL_MS)
        self.save_timer.timeout.connect(self.save_progress)

        self.player.set_rate(float(db.get_state("speed", "1.0")))
        self.player.set_volume(int(db.get_state("volume", "80")))
        self.set_notes_visible(self._notes_visible)
        self.set_playlist_visible(self._playlist_visible)
        self._update_info()
        self._setup_shortcuts()

    # ---- shortcuts ----
    def _setup_shortcuts(self) -> None:
        bindings = {
            "Space": self.player.toggle_play,
            "Left": lambda: self.player.seek_relative(-SEEK_STEP_MS),
            "Right": lambda: self.player.seek_relative(SEEK_STEP_MS),
            "Up": lambda: self.player.change_volume(VOLUME_STEP),
            "Down": lambda: self.player.change_volume(-VOLUME_STEP),
            "F": self.toggle_fullscreen,
            "Esc": self.exit_fullscreen,
            "N": lambda: self._play_neighbor(1),
            "P": lambda: self._play_neighbor(-1),
            "T": lambda: self.set_playlist_visible(not self._playlist_visible),
            "M": self.player.toggle_mute,
            "[": lambda: self.player.change_rate(-1),
            "]": lambda: self.player.change_rate(1),
        }
        for key, slot in bindings.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda slot=slot: self.course is not None and self.isVisible() and slot())

    # ---- panels ----
    def set_notes_visible(self, visible: bool) -> None:
        self._notes_visible = visible
        self.notes.setVisible(visible and not self.window().isFullScreen())
        self.notes_btn.blockSignals(True)
        self.notes_btn.setChecked(visible)
        self.notes_btn.blockSignals(False)
        self.db.set_state("notes_visible", "1" if visible else "0")

    def set_playlist_visible(self, visible: bool) -> None:
        self._playlist_visible = visible
        self.side_panel.setVisible(visible and not self.window().isFullScreen())
        self.playlist_btn.blockSignals(True)
        self.playlist_btn.setChecked(visible)
        self.playlist_btn.blockSignals(False)
        self.db.set_state("playlist_visible", "1" if visible else "0")

    # ---- course lifecycle ----
    def open_course(self, course_id: int, autoplay: bool = False) -> bool:
        course = self.db.get_course(course_id)
        if course is None:
            return False
        self.close_course()
        self.course = course
        self.videos = self.db.get_videos(course_id)
        self.course_label.setText(course.name)
        self._build_tree()
        self._update_progress()
        start = self._pick_start_video()
        if start is not None:
            self.play_video(start.id, autoplay)
            self.thumbnails.request_course(course, self.videos, self.videos.index(start))
        self.save_timer.start()
        return True

    def close_course(self) -> None:
        self.thumbnails.cancel()
        self._thumbs.clear()
        self.save_progress()
        self._cancel_up_next()
        self.save_timer.stop()
        self.exit_fullscreen()
        self.player.stop()
        self.course = None
        self.current = None
        self.videos = []
        self._items.clear()
        self._sections.clear()
        self.tree.clear()
        self.notes.set_video(None)
        self._update_info()

    def shutdown(self) -> None:
        self.save_progress()
        self.db.set_state("speed", str(self.player.rate()))
        self.db.set_state("volume", str(self.player.volume()))
        self.player.release()

    def sync_course(self) -> None:
        """Rescan the source; progress is kept and the current video keeps playing."""
        course = self.course
        if course is None:
            return
        self.save_progress()
        videos = scan_source(self, self.services, course.source, course.root)
        if videos is None or self.course is not course:
            return
        if not videos:
            QMessageBox.warning(self, "No videos", "No videos found, so the course was left unchanged.")
            return
        added, removed = self.db.sync_videos(course.id, videos)
        self.videos = self.db.get_videos(course.id)
        if self.current is not None:
            current = self._video(self.current.id)
            if current is None:  # the playing video was deleted at the source
                self._cancel_up_next()
                self.player.stop()
            self.current = current
        self._build_tree()
        self._update_progress()
        self._update_info()
        if self.current is not None and (item := self._items.get(self.current.id)) is not None:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
        if added:
            start = self.videos.index(self.current) if self.current is not None else 0
            self.thumbnails.request_course(course, self.videos, start)
        QMessageBox.information(self, "Sync complete", f"{added} new video(s) added, {removed} removed.")

    def _pick_start_video(self) -> Video | None:
        by_id = {v.id: v for v in self.videos}
        if self.course.last_video_id in by_id:
            return by_id[self.course.last_video_id]
        return next((v for v in self.videos if not v.completed), self.videos[0] if self.videos else None)

    def _video(self, video_id: int) -> Video | None:
        return next((v for v in self.videos if v.id == video_id), None)

    def play_video(self, video_id: int, autoplay: bool = True) -> None:
        video = self._video(video_id)
        if video is None or self.course is None:
            return
        self.save_progress()
        self._cancel_up_next()
        self._up_next_dismissed = False
        if not ensure_source(self, self.services, self.course.source):
            return
        try:
            mrl = self.services.media_for(self.course, video)
        except (FileNotFoundError, ValueError) as exc:
            show_error(self, "Cannot play video", exc)
            return
        subtitle = self.services.subtitle_for(self.course, video)
        self.services.prefetch(self.course, video)

        previous, self.current = self.current, video
        self.player.load(mrl, video.last_position_ms, autoplay, subtitle)
        self.db.set_last_video(self.course.id, video.id)
        self.course.last_video_id = video.id
        self.notes.set_video(video.id)
        for v in (previous, video):
            if v is not None:
                self._style_item(v)
        item = self._items.get(video.id)
        if item is not None:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
        self._update_info()

    def save_progress(self) -> None:
        if self.current is None or not self.player.has_media():
            return
        position = self.player.time()
        length = self.player.length()
        if length > 0 and position >= length - COMPLETE_TOLERANCE_MS:
            position = 0  # finished videos restart from the beginning
        self.current.last_position_ms = position
        self.db.save_position(self.current.id, position)

    # ---- player events ----
    def _on_position(self, position: int, length: int) -> None:
        video = self.current
        if video is None:
            return
        if abs(video.duration_ms - length) > 1000:
            video.duration_ms = length
            self.db.set_duration(video.id, length)
            self._style_item(video)
            self._update_info()
        if not video.completed and position >= length - COMPLETE_TOLERANCE_MS:
            self._set_completed(video, True)
        self._update_early_up_next(position, length)

    def _update_early_up_next(self, position: int, length: int) -> None:
        remaining = length - position
        if remaining > UP_NEXT_LEAD_MS:  # e.g. the user seeked back out of the last seconds
            if self._up_next_early or self._next_id is not None:
                self._cancel_up_next()
            self._up_next_dismissed = False
            return
        if self._up_next_early:
            self.player.set_up_next_remaining(remaining)
            return
        # Remaining 0 means a jump to the very end; _on_ended then shows the short countdown.
        if self._up_next_dismissed or self._next_id is not None or remaining <= 0 or length < 3 * UP_NEXT_LEAD_MS:
            return
        following = self._neighbor(1)
        if following is not None:
            self._next_id = following.id
            self._up_next_early = True
            self.player.follow_up_next(display_title(following.title), self._thumb_for(following), UP_NEXT_LEAD_MS)
            self.player.set_up_next_remaining(remaining)

    def _thumb_for(self, video: Video):
        if video.id not in self._thumbs:
            self._thumbs[video.id] = load_thumb(video.id)
        return self._thumbs[video.id]

    def _on_ended(self) -> None:
        video = self.current
        if video is None:
            return
        if not video.completed:
            self._set_completed(video, True)
        video.last_position_ms = 0
        self.db.save_position(video.id, 0)
        if self._up_next_early and self._next_id is not None:
            self._play_next_now()
            return
        if self._up_next_dismissed:
            return
        following = self._neighbor(1)
        if following is not None:
            self._next_id = following.id
            self.player.show_up_next(display_title(following.title), self._thumb_for(following), AUTOPLAY_DELAY_S)
        elif all(v.completed for v in self.videos):
            QMessageBox.information(self, "Course complete", f"Congratulations! You completed \"{self.course.name}\".")

    # ---- info under the video ----
    def _update_info(self) -> None:
        video = self.current
        has_video = video is not None
        for button in (self.complete_btn, self.notes_btn):
            button.setEnabled(has_video)
        if not has_video:
            self.video_title.clear()
            self.video_meta.clear()
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return
        index = self.videos.index(video) + 1
        parts = [video.section, f"Lecture {index} of {len(self.videos)}"]
        if video.duration_ms:
            parts.append(format_ms(video.duration_ms))
        if video.completed:
            parts.append("Completed")
        self.video_title.setText(display_title(video.title))
        self.video_meta.setText("  \u2022  ".join(p for p in parts if p))
        self.prev_btn.setEnabled(self._neighbor(-1) is not None)
        self.next_btn.setEnabled(self._neighbor(1) is not None)
        self.complete_btn.setChecked(video.completed)
        self.complete_btn.setText("\u2713  Completed" if video.completed else "Mark as complete")

    def _toggle_current_complete(self, checked: bool) -> None:
        if self.current is not None:
            self._set_completed(self.current, checked)

    # ---- up next ----
    def _play_next_now(self) -> None:
        next_id = self._next_id
        self._cancel_up_next()
        if next_id is not None:
            self.play_video(next_id, True)

    def _cancel_up_next(self) -> None:
        self._next_id = None
        self._up_next_early = False
        self.player.hide_up_next()

    def _dismiss_up_next(self) -> None:
        self._up_next_dismissed = True  # Cancel: let this video finish without autoplay
        self._cancel_up_next()

    def _neighbor(self, step: int) -> Video | None:
        if self.current is None:
            return None
        ids = [v.id for v in self.videos]
        index = ids.index(self.current.id) + step
        return self.videos[index] if 0 <= index < len(self.videos) else None

    def _play_neighbor(self, step: int) -> None:
        video = self._neighbor(step)
        if video is not None:
            self.play_video(video.id, True)

    # ---- playlist ----
    def _build_tree(self) -> None:
        self.tree.clear()
        self._items.clear()
        self._sections.clear()
        groups: dict[str, list[Video]] = {}
        for video in self.videos:
            groups.setdefault(video.section, []).append(video)
        bold = QFont(self.tree.font())
        bold.setBold(True)
        number = 0
        for name, videos in groups.items():
            section_item = QTreeWidgetItem(self.tree)
            section_item.setFont(0, bold)
            section_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            for video in videos:
                number += 1
                title = display_title(video.title)
                item = QTreeWidgetItem(section_item, [f"{number}.  {title}", ""])
                item.setData(0, VIDEO_ID, video.id)
                item.setToolTip(0, title)
                self._items[video.id] = item
                self._style_item(video)
            self._sections.append((section_item, name, videos))
        self.tree.expandAll()
        self._update_sections()

    def _style_item(self, video: Video) -> None:
        item = self._items.get(video.id)
        if item is None:
            return
        is_current = self.current is not None and self.current.id == video.id
        if video.id not in self._thumbs:
            self._thumbs[video.id] = load_thumb(video.id)
        item.setIcon(0, _thumb_icon(self._thumbs[video.id], video, is_current))
        font = item.font(0)
        font.setBold(is_current)
        item.setFont(0, font)

    def _on_thumbnail_ready(self, video_id: int) -> None:
        video = self._video(video_id)
        if video is not None:
            self._thumbs[video_id] = load_thumb(video_id)
            self._style_item(video)

    def _update_sections(self) -> None:
        for section_item, name, videos in self._sections:
            done = sum(v.completed for v in videos)
            section_item.setText(0, name or "Lectures")
            section_item.setText(1, f"{done}/{len(videos)}")

    def _update_progress(self) -> None:
        if self.course is None:
            return
        stats = self.db.course_stats(self.course.id)
        self.progress_label.setText(f"{stats.percent}% complete")
        self.progress_bar.setValue(stats.percent)
        self.side_meta.setText(f"{stats.completed} / {stats.total} lectures complete  \u2022  {stats.percent}%")
        self.side_progress.setValue(stats.percent)

    def _set_completed(self, video: Video, completed: bool) -> None:
        self.db.set_completed(video.id, completed)
        video.completed = completed
        if completed:
            video.last_position_ms = 0
        self._style_item(video)
        self._update_sections()
        self._update_progress()
        if video is self.current:
            self._update_info()

    def _on_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        video_id = item.data(0, VIDEO_ID)
        if video_id is not None and (self.current is None or video_id != self.current.id):
            self.play_video(video_id, True)

    def _tree_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        video = self._video(item.data(0, VIDEO_ID)) if item is not None and item.data(0, VIDEO_ID) else None
        if video is None:
            return
        menu = QMenu(self)
        play_action = menu.addAction("Play")
        toggle_action = menu.addAction("Mark as not complete" if video.completed else "Mark as complete")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is play_action:
            self.play_video(video.id, True)
        elif chosen is toggle_action:
            self._set_completed(video, not video.completed)

    # ---- fullscreen ----
    def toggle_fullscreen(self) -> None:
        if self.window().isFullScreen():
            self.exit_fullscreen()
            return
        window = self.window()
        self._was_maximized = window.isMaximized()
        for widget in (self.top_bar, self.info, self.notes, self.side_panel):
            widget.hide()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self._video_layout.setStretch(self._video_layout.count() - 1, 0)
        self.player.set_fill_mode(True)
        window.showFullScreen()

    def exit_fullscreen(self) -> None:
        window = self.window()
        if not window.isFullScreen():
            return
        if self._was_maximized:
            window.showMaximized()
        else:
            window.showNormal()
        self.body_layout.setContentsMargins(16, 12, 16, 12)
        self._video_layout.setStretch(self._video_layout.count() - 1, 1)
        self.player.set_fill_mode(False)
        self.top_bar.show()
        self.info.show()
        self.notes.setVisible(self._notes_visible)
        self.side_panel.setVisible(self._playlist_visible)
