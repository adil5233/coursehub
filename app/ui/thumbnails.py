"""Video thumbnails, cached as JPEG files in CACHE_DIR/thumbs/<video_id>.jpg."""
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PySide6.QtCore import QObject, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap

from ..config import CACHE_DIR
from ..models import Course, Video

THUMB_DIR = CACHE_DIR / "thumbs"
THUMB_WIDTH = 320
_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_LOCAL_TIMEOUT_MS = 15_000


def thumb_path(video_id: int) -> Path:
    return THUMB_DIR / f"{video_id}.jpg"


def load_thumb(video_id: int) -> QPixmap | None:
    path = thumb_path(video_id)
    if not path.is_file():
        return None
    pixmap = QPixmap(str(path))
    return None if pixmap.isNull() else pixmap


def cover_pixmap(source: QPixmap | None, width: int, height: int, radius: float = 0,
                 placeholder: QColor | str = "#272727") -> QPixmap:
    """Scale-and-crop source to fill width x height (like CSS object-fit: cover), with rounded corners."""
    result = QPixmap(width, height)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if radius:
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
        painter.setClipPath(clip)
    if source is None:
        painter.fillRect(0, 0, width, height, QColor(placeholder))
    else:
        scaled = source.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap(0, 0, scaled, (scaled.width() - width) // 2, (scaled.height() - height) // 2,
                           width, height)
    painter.end()
    return result


class _LocalGrabber(QObject):
    """Grabs one frame from a local file with Qt Multimedia (no window, no audio)."""

    finished = Signal(int, bool)  # video_id, ok

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

        self._statuses = QMediaPlayer.MediaStatus
        self.player = QMediaPlayer(self)
        self.sink = QVideoSink(self)
        self.player.setVideoOutput(self.sink)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.errorOccurred.connect(lambda *_: self._finish(False))
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(_LOCAL_TIMEOUT_MS)
        self.timeout.timeout.connect(lambda: self._finish(False))
        self.job: tuple[int, Path] | None = None
        self._seeked = False
        self._frames = 0

    def busy(self) -> bool:
        return self.job is not None

    def grab(self, video_id: int, source: str, target: Path) -> None:
        self.job = (video_id, target)
        self._seeked = False
        self._frames = 0
        self.timeout.start()
        self.player.setSource(QUrl.fromLocalFile(source))

    def _on_status(self, status) -> None:
        if self.job is None:
            return
        if status == self._statuses.InvalidMedia:
            self._finish(False)
        elif status in (self._statuses.LoadedMedia, self._statuses.BufferedMedia) and not self._seeked:
            self._seeked = True
            duration = self.player.duration()
            self.player.setPosition(int(duration * 0.1) if duration > 0 else 0)
            self.player.play()

    def _on_frame(self, frame) -> None:
        if self.job is None or not self._seeked or not frame.isValid():
            return
        self._frames += 1
        if self._frames < 3:  # the first frames can still be from before the seek
            return
        image = frame.toImage()
        if image.isNull():
            return
        target = self.job[1]
        ok = image.scaledToWidth(THUMB_WIDTH, Qt.TransformationMode.SmoothTransformation).save(str(target), "JPG", 85)
        self._finish(ok)

    def _finish(self, ok: bool) -> None:
        if self.job is None:
            return
        video_id = self.job[0]
        self.job = None
        self.timeout.stop()
        self.player.stop()
        QTimer.singleShot(0, lambda: self.player.setSource(QUrl()))
        self.finished.emit(video_id, ok)


class ThumbnailManager(QObject):
    ready = Signal(int)  # video_id
    _remote_done = Signal(int, bool)

    def __init__(self, services, parent=None) -> None:
        super().__init__(parent)
        self.services = services
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="thumbs")
        self._generation = 0
        self._failed: set[int] = set()
        self._local_queue: deque[tuple[int, int, str]] = deque()
        self._remote_done.connect(self._on_done)
        try:
            self._grabber: _LocalGrabber | None = _LocalGrabber(self)
            self._grabber.finished.connect(self._on_local_done)
        except Exception:
            self._grabber = None  # Qt Multimedia unavailable: local videos keep the placeholder

    def request_course(self, course: Course, videos: list[Video], start_index: int = 0) -> None:
        """Queue missing thumbnails, starting from the current video and wrapping around."""
        self.cancel()
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        ordered = videos[start_index:] + videos[:start_index]
        todo = [v for v in ordered if v.id not in self._failed and not thumb_path(v.id).is_file()]
        if not todo:
            return
        generation = self._generation
        if course.source == "local":
            if self._grabber is not None:
                self._local_queue.extend((generation, v.id, str(Path(course.root) / v.ref)) for v in todo)
                self._next_local()
        else:
            self._pool.submit(self._remote_batch, generation, course, todo)

    def cancel(self) -> None:
        self._generation += 1
        self._local_queue.clear()

    def shutdown(self) -> None:
        self.cancel()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---- remote (Telegram / Drive), runs on pool threads ----
    def _remote_batch(self, generation: int, course: Course, todo: list[Video]) -> None:
        if course.source == "telegram":
            tg = self.services.telegram
            if tg is None or not tg.is_authorized:
                return
            try:
                tg.warm_messages(int(course.root), [int(v.ref) for v in todo])
            except Exception:
                pass  # individual downloads below will retry per message
        elif self.services.drive is None:
            return
        with ThreadPoolExecutor(max_workers=3) as workers:
            for video in todo:
                workers.submit(self._fetch_one, generation, course, video)

    def _fetch_one(self, generation: int, course: Course, video: Video) -> None:
        if generation != self._generation:
            return
        target = thumb_path(video.id)
        temp = target.with_suffix(".part.jpg")
        try:
            if course.source == "telegram":
                ok = self.services.telegram.download_thumb(int(course.root), int(video.ref), temp)
            else:
                ok = self._drive_thumb(video.ref, temp)
            if ok:
                temp.replace(target)
        except Exception:
            ok = False
        finally:
            temp.unlink(missing_ok=True)
        self._remote_done.emit(video.id, ok)

    def _drive_thumb(self, file_id: str, target: Path) -> bool:
        if not _DRIVE_ID.match(file_id):
            return False
        headers = {"Authorization": f"Bearer {self.services.drive.token()}"}
        meta = requests.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params={"fields": "thumbnailLink", "supportsAllDrives": "true"}, headers=headers, timeout=20,
        )
        link = meta.json().get("thumbnailLink") if meta.ok else None
        if not link:
            return False
        image = requests.get(re.sub(r"=s\d+$", f"=s{THUMB_WIDTH}", link), headers=headers, timeout=20)
        if not image.ok or not image.content:
            return False
        target.write_bytes(image.content)
        return True

    # ---- local (Qt Multimedia), runs on the GUI thread ----
    def _next_local(self) -> None:
        if self._grabber is None or self._grabber.busy():
            return
        while self._local_queue:
            generation, video_id, source = self._local_queue.popleft()
            if generation == self._generation and Path(source).is_file():
                self._grabber.grab(video_id, source, thumb_path(video_id))
                return

    def _on_local_done(self, video_id: int, ok: bool) -> None:
        self._on_done(video_id, ok)
        QTimer.singleShot(0, self._next_local)

    def _on_done(self, video_id: int, ok: bool) -> None:
        if ok:
            self.ready.emit(video_id)
        else:
            self._failed.add(video_id)
