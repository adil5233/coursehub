"""YouTube-style seek preview: a frame of the video at the hovered seek-bar position."""
from collections import OrderedDict

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

PREVIEW_W, PREVIEW_H = 240, 135
_CACHE_FRAMES = 300
_FRAME_TIMEOUT_MS = 20_000
_MAX_SKIPPED_FRAMES = 8


class FramePreviewer(QObject):
    """Decodes single frames with a hidden Qt Multimedia player (no audio, no window).

    Works for local files and for the localhost stream proxy URLs, so Drive/Telegram previews reuse the
    same chunk cache as playback.
    """

    frame_ready = Signal(int)  # bucket start in ms

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

        self._statuses = QMediaPlayer.MediaStatus
        self._player = QMediaPlayer(self)
        self._sink = QVideoSink(self)
        self._player.setVideoOutput(self._sink)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.errorOccurred.connect(self._on_error)
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(_FRAME_TIMEOUT_MS)
        self._timeout.timeout.connect(self._give_up)
        self._source = ""
        self._loaded = False
        self._failed = False
        self._cache: OrderedDict[int, QImage] = OrderedDict()
        self._target: int | None = None
        self._pending: int | None = None
        self._skipped = 0

    def set_source(self, source: str) -> None:
        """Remember the media; it is only opened on the first preview request."""
        if source == self._source:
            return
        self.reset()
        self._source = source

    def reset(self) -> None:
        self._source = ""
        self._loaded = self._failed = False
        self._cache.clear()
        self._target = self._pending = None
        self._timeout.stop()
        self._player.stop()
        self._player.setSource(QUrl())

    def cached(self, bucket: int) -> QImage | None:
        image = self._cache.get(bucket)
        if image is not None:
            self._cache.move_to_end(bucket)
        return image

    def request(self, bucket: int) -> None:
        if not self._source or self._failed or bucket in self._cache:
            return
        self._pending = bucket
        if self._target is not None:
            return  # the latest request is picked up when the current frame arrives
        if not self._loaded:
            if self._player.source().isEmpty():
                url = QUrl(self._source) if "://" in self._source else QUrl.fromLocalFile(self._source)
                self._timeout.start()
                self._player.setSource(url)
            return
        self._start_next()

    def _start_next(self) -> None:
        bucket, self._pending = self._pending, None
        if bucket is None or bucket in self._cache:
            self._target = None
            return
        self._target = bucket
        self._skipped = 0
        self._timeout.start()
        self._player.setPosition(bucket)
        self._player.play()

    def _on_status(self, status) -> None:
        if status == self._statuses.InvalidMedia:
            self._give_up()
        elif status in (self._statuses.LoadedMedia, self._statuses.BufferedMedia) and not self._loaded:
            self._loaded = True
            self._timeout.stop()
            self._start_next()

    def _on_error(self, *_args) -> None:
        self._give_up()

    def _on_frame(self, frame) -> None:
        if self._target is None or not frame.isValid():
            return
        frame_ms = frame.startTime() // 1000
        if frame_ms >= 0 and abs(frame_ms - self._target) > 3000 and self._skipped < _MAX_SKIPPED_FRAMES:
            self._skipped += 1  # still showing a frame from before the seek
            return
        image = frame.toImage()
        if image.isNull():
            return
        self._timeout.stop()
        self._player.pause()
        self._cache[self._target] = image.scaled(
            PREVIEW_W * 2, PREVIEW_H * 2, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        while len(self._cache) > _CACHE_FRAMES:
            self._cache.popitem(last=False)
        done, self._target = self._target, None
        self.frame_ready.emit(done)
        if self._pending is not None:
            self._start_next()

    def _give_up(self) -> None:
        if self._target is None and self._loaded:
            return
        self._timeout.stop()
        self._player.pause()
        if not self._loaded:
            self._failed = True  # cannot decode this file; keep showing only the time
        self._target = None
        if self._pending is not None and self._loaded:
            self._start_next()


class SeekPreviewCard(QFrame):
    """Floating card above the seek bar: preview frame + time."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SeekPreview")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.image = QLabel()
        self.image.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.time = QLabel()
        self.time.setObjectName("SeekPreviewTime")
        self.chapter = QLabel()
        self.chapter.setObjectName("SeekPreviewTime")
        self.chapter.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.image)
        layout.addWidget(self.chapter, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.time, 0, Qt.AlignmentFlag.AlignHCenter)
        self._has_image = False
        self.set_image(None)
        self.adjustSize()
        self.hide()

    def set_image(self, image: QImage | None) -> None:
        if image is None and self._has_image:
            return  # keep the previous frame until the new one is decoded, like YouTube
        ratio = self.devicePixelRatioF()
        width, height = int(PREVIEW_W * ratio), int(PREVIEW_H * ratio)
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        border = 2 * ratio
        frame = QRectF(border / 2, border / 2, width - border, height - border)
        clip = QPainterPath()
        clip.addRoundedRect(frame, 8 * ratio, 8 * ratio)
        painter.fillPath(clip, QColor("#202020"))
        if image is not None:
            painter.setClipPath(clip)
            scaled = image.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
            painter.drawImage((width - scaled.width()) // 2, (height - scaled.height()) // 2, scaled)
            painter.setClipping(False)
        painter.setPen(QColor(255, 255, 255, 230))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = painter.pen()
        pen.setWidthF(border)
        painter.setPen(pen)
        painter.drawRoundedRect(frame, 8 * ratio, 8 * ratio)
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        self.image.setPixmap(pixmap)
        self._has_image = self._has_image or image is not None

    def set_chapter(self, title: str) -> None:
        self.chapter.setText(title if len(title) <= 34 else title[:33] + "\u2026")
        self.chapter.setVisible(bool(title))

    def clear_image(self) -> None:
        self._has_image = False
        self.set_image(None)
