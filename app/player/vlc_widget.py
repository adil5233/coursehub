import math
import sys
import time
from pathlib import Path

import vlc
from PySide6.QtCore import (
    QElapsedTimer, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSlider, QStyle, QStyleOptionSlider, QVBoxLayout,
    QWidget,
)

from ..config import SPEEDS, format_ms
from ..ui.theme import ACCENT
from .preview import FramePreviewer, SeekPreviewCard
from .subtitles import OVERLAY_FORMATS, CueTrack, parse_subtitles

SEEK_STEP_MS = 10_000
VOLUME_STEP = 5
CONTROLS_IDLE_S = 2.5
END_SNAP_MS = 1500  # seeks this close to the end finish the video (VLC can't seek into the last moments)
SEEK_GRACE_S = 45  # how long to trust a seek target while a slow stream catches up
NETWORK_CACHING_MS = 1500  # VLC's own buffer for streams; the proxy already reads ahead and caches
DEFAULT_RATIO = 9 / 16
VLC_ARGS = (
    "--quiet", "--no-video-title-show", "--no-sub-autodetect-file",
    # styled like the overlay subtitles, for formats VLC still renders itself (.ass, embedded tracks)
    "--sub-text-scale=60", "--freetype-font=Arial", "--freetype-bold", "--sub-margin=30",
)
SUBTITLE_FONT = 0.042  # text height as a fraction of the video height (Netflix-like)
SUBTITLE_BOTTOM = 0.045  # gap below the subtitles while the controls are hidden
_QWIDGETSIZE_MAX = 16_777_215
_SLIDER_MAX = 1000
_ICON_COLOR = QColor("#f2f2f7")


def glyph_icon(kind: str, color: QColor | str = _ICON_COLOR, size: int = 48) -> QIcon:
    """Light glyphs; the built-in style icons are near-invisible on the dark theme."""
    color = QColor(color)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)

    def pen(width: float) -> QPen:
        return QPen(color, s * width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

    def speaker() -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([
            QPointF(s * .12, s * .38), QPointF(s * .3, s * .38), QPointF(s * .52, s * .18),
            QPointF(s * .52, s * .82), QPointF(s * .3, s * .62), QPointF(s * .12, s * .62),
        ]))
        painter.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "play":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(s * .28, s * .16), QPointF(s * .28, s * .84), QPointF(s * .84, s * .5)]))
    elif kind == "pause":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(s * .22, s * .16, s * .2, s * .68), s * .04, s * .04)
        painter.drawRoundedRect(QRectF(s * .58, s * .16, s * .2, s * .68), s * .04, s * .04)
    elif kind == "check":
        painter.setPen(pen(.12))
        painter.drawPolyline(QPolygonF([QPointF(s * .2, s * .52), QPointF(s * .42, s * .74), QPointF(s * .82, s * .3)]))
    elif kind == "fullscreen":
        painter.setPen(pen(.09))
        lo, hi, arm = s * .2, s * .8, s * .2
        for x, y, dx, dy in ((lo, lo, 1, 1), (hi, lo, -1, 1), (lo, hi, 1, -1), (hi, hi, -1, -1)):
            painter.drawLine(QPointF(x, y), QPointF(x + dx * arm, y))
            painter.drawLine(QPointF(x, y), QPointF(x, y + dy * arm))
    elif kind == "volume":
        speaker()
        painter.setPen(pen(.07))
        painter.drawArc(QRectF(s * .4, s * .3, s * .3, s * .4), -60 * 16, 120 * 16)
        painter.drawArc(QRectF(s * .4, s * .16, s * .48, s * .68), -60 * 16, 120 * 16)
    elif kind == "muted":
        speaker()
        painter.setPen(pen(.07))
        painter.drawLine(QPointF(s * .64, s * .38), QPointF(s * .86, s * .62))
        painter.drawLine(QPointF(s * .86, s * .38), QPointF(s * .64, s * .62))
    elif kind == "gear":
        center = QPointF(s / 2, s / 2)
        painter.setPen(QPen(color, s * .13, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        for i in range(8):
            angle = math.radians(i * 45)
            dx, dy = math.cos(angle), math.sin(angle)
            painter.drawLine(QPointF(s / 2 + dx * s * .26, s / 2 + dy * s * .26),
                             QPointF(s / 2 + dx * s * .4, s / 2 + dy * s * .4))
        painter.setPen(pen(.1))
        painter.drawEllipse(center, s * .22, s * .22)
    elif kind == "theater":
        painter.setPen(pen(.08))
        painter.drawRoundedRect(QRectF(s * .12, s * .28, s * .76, s * .44), s * .06, s * .06)
    elif kind == "cc":
        painter.setPen(pen(.07))
        painter.drawRoundedRect(QRectF(s * .1, s * .22, s * .8, s * .56), s * .1, s * .1)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(int(s * .3))
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "CC")
    painter.end()
    return QIcon(pixmap)


class VideoFrame(QFrame):
    """Native surface VLC renders into."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0))
        self.setPalette(palette)
        self.setAutoFillBackground(True)


class SeekSlider(QSlider):
    """YouTube-style seek bar. Click anywhere to jump there, drag to scrub.

    Always emits sliderPressed/Moved/Released: QSlider's own handling treats a click outside the handle
    (e.g. the very end) as a page step and never emits sliderReleased, so the seek would not happen.
    """

    hovered = Signal(float, int)  # fraction of the video under the cursor, cursor x
    hover_left = Signal()
    HEIGHT = 22  # hit area; the visible track is much thinner
    MARGIN = 7  # room for the scrubber at both ends
    TRACK, TRACK_HOVER, SCRUBBER = 4.0, 6.0, 7.0

    def __init__(self, orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self._scrubbing = False
        self._buffered: list[tuple[float, float]] = []
        self._chapters: list[float] = []  # chapter starts as fractions (gaps in the bar, like YouTube)
        self._steps: list[float] = []  # study step starts as fractions (thin ticks)
        self._hover_fraction: float | None = None
        self._grow = 0.0  # 0 = resting, 1 = hovered
        self._grow_anim = QVariantAnimation(self)
        self._grow_anim.setDuration(120)
        self._grow_anim.valueChanged.connect(self._set_grow)
        self.setMouseTracking(True)
        self.setFixedHeight(self.HEIGHT)

    def set_buffered(self, ranges: list[tuple[float, float]]) -> None:
        """Already-downloaded parts of the video as (start, end) fractions, drawn lighter like YouTube."""
        if ranges != self._buffered:
            self._buffered = ranges
            self.update()

    def set_chapters(self, fractions: list[float]) -> None:
        if fractions != self._chapters:
            self._chapters = fractions
            self.update()

    def set_steps(self, fractions: list[float]) -> None:
        if fractions != self._steps:
            self._steps = fractions
            self.update()

    def _set_grow(self, value) -> None:
        self._grow = float(value)
        self.update()

    def _animate_grow(self, target: float) -> None:
        self._grow_anim.stop()
        self._grow_anim.setStartValue(self._grow)
        self._grow_anim.setEndValue(target)
        self._grow_anim.start()

    def _span(self) -> float:
        return max(1.0, self.width() - 2 * self.MARGIN)

    def _fraction_at(self, x: float) -> float:
        return max(0.0, min(1.0, (x - self.MARGIN) / self._span()))

    def _value_at(self, x: float) -> int:
        return self.minimum() + round(self._fraction_at(x) * (self.maximum() - self.minimum()))

    def paintEvent(self, event) -> None:
        left, span = float(self.MARGIN), self._span()
        height = self.TRACK + (self.TRACK_HOVER - self.TRACK) * self._grow
        top = self.height() / 2 - height / 2
        played = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        def bar(start: float, end: float, color: QColor) -> None:
            if end - start > 0.001:
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(left + start * span, top, (end - start) * span, height),
                                        height / 2, height / 2)

        bar(0.0, 1.0, QColor(255, 255, 255, 60))
        for start, end in self._buffered:
            bar(start, end, QColor(255, 255, 255, 110))
        if self._hover_fraction is not None:
            bar(0.0, self._hover_fraction, QColor(255, 255, 255, 150))  # shows where a click would jump to
        bar(0.0, played, QColor(ACCENT))
        for fraction in self._steps:
            painter.fillRect(QRectF(left + fraction * span - 0.5, top - 3, 1, height + 6), QColor(255, 255, 255, 170))
        for fraction in self._chapters:
            painter.fillRect(QRectF(left + fraction * span - 1.5, top - 1, 3, height + 2), QColor(0, 0, 0, 220))
        radius = self.SCRUBBER * (1.0 if self._scrubbing else self._grow)
        if radius > 0.5:
            painter.setBrush(QColor(ACCENT))
            painter.drawEllipse(QPointF(left + played * span, self.height() / 2), radius, radius)
        painter.end()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._animate_grow(1.0)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._scrubbing = True
        self.setValue(self._value_at(event.position().x()))
        self.sliderPressed.emit()
        self.sliderMoved.emit(self.value())
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        if self._scrubbing:
            self.setValue(self._value_at(x))
            self.sliderMoved.emit(self.value())
        self._hover_fraction = self._fraction_at(x)
        self.update()
        self.hovered.emit(self._hover_fraction, int(x))
        event.accept()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if not self._scrubbing:
            self._end_hover()

    def _end_hover(self) -> None:
        self._hover_fraction = None
        self._animate_grow(0.0)
        self.hover_left.emit()

    def mouseReleaseEvent(self, event) -> None:
        if self._scrubbing and event.button() == Qt.MouseButton.LeftButton:
            self._scrubbing = False
            self.setValue(self._value_at(event.position().x()))
            self.sliderReleased.emit()
            if not self.rect().contains(event.position().toPoint()):
                self._end_hover()
        event.accept()


class CountdownRing(QWidget):
    """Circular countdown: an arc that drains while the remaining whole seconds show in the middle."""

    def __init__(self, size: int = 64, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._fraction = 1.0
        self._seconds = 0

    def set_state(self, fraction: float, seconds: int) -> None:
        self._fraction = max(0.0, min(1.0, fraction))
        self._seconds = seconds
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = float(self.width())
        pen_width = side * 0.08
        circle = QRectF(pen_width, pen_width, side - 2 * pen_width, side - 2 * pen_width)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.drawEllipse(circle)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 60), pen_width))
        painter.drawEllipse(circle)
        painter.setPen(QPen(QColor("#ffffff"), pen_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(circle, 90 * 16, int(360 * 16 * self._fraction))
        font = QFont()
        font.setBold(True)
        font.setPixelSize(int(side * 0.42))
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, side, side), Qt.AlignmentFlag.AlignCenter, str(self._seconds))


class LoadingSpinner(QWidget):
    """YouTube/Netflix-style spinner: a rotating arc whose head and tail chase each other."""

    CYCLE_S = 1.3
    TRAVEL_DEG = 280

    def __init__(self, size: int = 72, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._percent: int | None = None
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)
        self.hide()

    def set_percent(self, percent: int | None) -> None:
        if percent != self._percent:
            self._percent = percent
            self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._clock.start()
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def paintEvent(self, event) -> None:
        seconds = self._clock.elapsed() / 1000 if self._clock.isValid() else 0.0
        cycle, phase = divmod(seconds / self.CYCLE_S, 1.0)
        base = seconds * 120 + cycle * self.TRAVEL_DEG
        tail = base + self.TRAVEL_DEG * phase * phase
        head = base + self.TRAVEL_DEG * (1 - (1 - phase) ** 2) + 18

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = float(self.width())
        pen_width = side * 0.075
        circle = QRectF(pen_width, pen_width, side - 2 * pen_width, side - 2 * pen_width)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 110))
        painter.drawEllipse(circle)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 45), pen_width))
        painter.drawEllipse(circle)
        painter.setPen(QPen(QColor(ACCENT), pen_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(circle, int(-tail * 16), int(-(head - tail) * 16))  # negative = clockwise
        if self._percent is not None:
            font = QFont()
            font.setBold(True)
            font.setPixelSize(int(side * 0.22))
            painter.setFont(font)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QRectF(0, 0, side, side), Qt.AlignmentFlag.AlignCenter, f"{self._percent}%")
        painter.end()


class UpNextCard(QFrame):
    """Netflix-style 'next video in 3, 2, 1' card shown over the video when a lecture ends."""

    play_now = Signal()
    cancelled = Signal()
    THUMB_W, THUMB_H = 176, 99

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("UpNextCard")
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation)
        self.setFixedWidth(430)
        self._duration_ms = 3000
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

        self.thumb = QLabel()
        self.thumb.setFixedSize(self.THUMB_W, self.THUMB_H)
        self.thumb.setObjectName("UpNextThumb")
        self.ring = CountdownRing(56, self.thumb)
        self.ring.move((self.THUMB_W - self.ring.width()) // 2, (self.THUMB_H - self.ring.height()) // 2)

        kicker = QLabel("UP NEXT")
        kicker.setObjectName("UpNextKicker")
        self.title = QLabel()
        self.title.setObjectName("UpNextTitle")
        self.title.setWordWrap(True)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("Muted")

        play_button = QPushButton("\u25b6  Play now")
        play_button.setObjectName("Primary")
        play_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        play_button.clicked.connect(lambda: self._finish(self.play_now))
        cancel_button = QPushButton("Cancel")
        cancel_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_button.clicked.connect(lambda: self._finish(self.cancelled))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(play_button)
        buttons.addWidget(cancel_button)
        buttons.addStretch(1)

        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(kicker)
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        text.addStretch(1)
        text.addLayout(buttons)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 14, 12)
        layout.setSpacing(14)
        layout.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text, 1)
        self.hide()

    def start(self, title: str, thumbnail: QPixmap | None, seconds: int) -> None:
        """Count down on a wall clock, then emit play_now."""
        self._duration_ms = max(1, seconds) * 1000
        self._open(title, thumbnail)
        self._clock.start()
        self._tick()
        self._timer.start()

    def follow(self, title: str, thumbnail: QPixmap | None, total_ms: int) -> None:
        """Show the card while the video plays; the countdown is driven by set_remaining()."""
        self._timer.stop()
        self._duration_ms = max(1, total_ms)
        self._open(title, thumbnail)
        self.set_remaining(total_ms)

    def set_remaining(self, remaining_ms: int) -> None:
        remaining_ms = max(0, remaining_ms)
        seconds = math.ceil(remaining_ms / 1000)
        self.ring.set_state(min(1.0, remaining_ms / self._duration_ms), seconds)
        self.subtitle.setText(f"Playing in {seconds}s")

    def _open(self, title: str, thumbnail: QPixmap | None) -> None:
        self.title.setText(title)
        self.thumb.setPixmap(self._cover(thumbnail))
        self.show()
        self.raise_()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _cover(self, source: QPixmap | None) -> QPixmap:
        ratio = self.devicePixelRatioF()
        width, height = int(self.THUMB_W * ratio), int(self.THUMB_H * ratio)
        result = QPixmap(width, height)
        result.fill(QColor("#272727"))
        if source is not None and not source.isNull():
            scaled = source.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
            painter = QPainter(result)
            painter.drawPixmap(0, 0, scaled, (scaled.width() - width) // 2, (scaled.height() - height) // 2,
                               width, height)
            painter.end()
        result.setDevicePixelRatio(ratio)
        return result

    def _tick(self) -> None:
        remaining = self._duration_ms - self._clock.elapsed()
        if remaining <= 0:
            self._finish(self.play_now)
            return
        self.set_remaining(remaining)

    def _finish(self, signal) -> None:
        if not self.isVisible():
            return
        self.stop()
        signal.emit()


class PlayerOverlay(QWidget):
    """See-through window kept above the native VLC surface, since Qt widgets cannot paint over it."""

    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, owner: QWidget, is_playing) -> None:
        super().__init__(
            owner,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._is_playing = is_playing
        self._controls_visible = True
        self._last_cursor = QPoint()
        self._last_move = time.monotonic()

        self.status_label = QLabel()
        self.status_label.setObjectName("OverlayStatus")
        self.status_label.hide()
        self.spinner = LoadingSpinner()
        self.seek_preview = SeekPreviewCard(self)
        self.bar = QWidget()
        self.bar.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation)
        self.up_next = UpNextCard()
        up_next_row = QHBoxLayout()
        up_next_row.setContentsMargins(0, 0, 20, 10)
        up_next_row.addStretch(1)
        up_next_row.addWidget(self.up_next)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(10)
        layout.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        layout.addLayout(up_next_row)
        layout.addWidget(self.bar)

        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(150)
        self._cursor_timer.timeout.connect(self._check_cursor)
        self._cursor_timer.start()

        self._subtitle = ""
        self._sub_lift = 1.0  # 0 = resting at the bottom, 1 = raised above the control bar
        self._lift_animation = QVariantAnimation(self)
        self._lift_animation.setDuration(200)
        self._lift_animation.valueChanged.connect(self._set_lift)

    def set_subtitle(self, text: str) -> None:
        if text != self._subtitle:
            self._subtitle = text
            self.update()

    def _set_lift(self, value) -> None:
        self._sub_lift = float(value)
        if self._subtitle:
            self.update()

    def set_controls_visible(self, visible: bool) -> None:
        if visible == self._controls_visible:
            return
        self._controls_visible = visible
        self.bar.setVisible(visible)
        if not visible:
            self.seek_preview.hide()
        self.setCursor(Qt.CursorShape.ArrowCursor if visible else Qt.CursorShape.BlankCursor)
        self._lift_animation.stop()
        self._lift_animation.setStartValue(self._sub_lift)
        self._lift_animation.setEndValue(1.0 if visible else 0.0)
        self._lift_animation.start()
        self.update()

    def _check_cursor(self) -> None:
        if not self.isVisible():
            return
        pos = QCursor.pos()
        now = time.monotonic()
        moved = pos != self._last_cursor
        if moved:
            self._last_cursor = pos
            self._last_move = now
        inside = self.geometry().contains(pos)
        if not self._is_playing() or QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            show = True  # like YouTube: paused video keeps its controls
        elif not inside:
            show = False
        elif moved:
            show = True
        else:
            over_bar = self.bar.isVisible() and self.bar.rect().contains(self.bar.mapFromGlobal(pos))
            show = over_bar or now - self._last_move < CONTROLS_IDLE_S
        self.set_controls_visible(show)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))  # alpha 1 keeps the whole area clickable
        if self._controls_visible:
            top = max(0, self.height() - 170)
            gradient = QLinearGradient(0, top, 0, self.height())
            gradient.setColorAt(0, QColor(0, 0, 0, 0))
            gradient.setColorAt(1, QColor(0, 0, 0, 200))
            painter.fillRect(QRect(0, top, self.width(), self.height() - top), gradient)
        if self._subtitle:
            self._paint_subtitle(painter)

    def _paint_subtitle(self, painter: QPainter) -> None:
        """White bold text with a dark outline and soft shadow, centred near the bottom."""
        font = QFont("Arial")
        font.setBold(True)
        font.setPixelSize(max(14, round(self.height() * SUBTITLE_FONT)))
        metrics = QFontMetrics(font)
        max_width = self.width() * 0.86
        lines: list[str] = []
        for paragraph in self._subtitle.split("\n"):
            current = ""
            for word in paragraph.split():
                trial = f"{current} {word}".strip()
                if current and metrics.horizontalAdvance(trial) > max_width:
                    lines.append(current)
                    current = word
                else:
                    current = trial
            if current:
                lines.append(current)

        resting = max(10.0, self.height() * SUBTITLE_BOTTOM)
        raised = max(resting, max(self.bar.height(), self.bar.sizeHint().height()) + 12.0)
        bottom = self.height() - (resting + (raised - resting) * self._sub_lift)
        line_height = metrics.height()
        baseline = bottom - line_height * len(lines) + metrics.ascent()
        outline = QPen(QColor(0, 0, 0, 220), max(2.0, font.pixelSize() / 8), Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        shadow_offset = max(1.0, font.pixelSize() / 16)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for line in lines:
            path = QPainterPath()
            path.addText((self.width() - metrics.horizontalAdvance(line)) / 2, baseline, font, line)
            painter.fillPath(path.translated(shadow_offset, shadow_offset * 1.5), QColor(0, 0, 0, 150))
            painter.strokePath(path, outline)
            painter.fillPath(path, QColor(255, 255, 255))
            baseline += line_height

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()


def _pill(*widgets: QWidget, margins: tuple[int, int, int, int] = (6, 0, 12, 0)) -> QFrame:
    pill = QFrame()
    pill.setObjectName("OverlayPill")
    pill.setFixedHeight(44)
    layout = QHBoxLayout(pill)
    layout.setContentsMargins(*margins)
    layout.setSpacing(4)
    for widget in widgets:
        layout.addWidget(widget)
    return pill


class VideoPlayer(QWidget):
    position_changed = Signal(int, int)  # position_ms, length_ms
    ended = Signal()
    error = Signal(str)
    fullscreen_requested = Signal()
    theater_requested = Signal()
    up_next_accepted = Signal()
    up_next_cancelled = Signal()
    _vlc_ended = Signal()
    _vlc_error = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._instance = vlc.Instance(*VLC_ARGS)
        self._player = self._instance.media_player_new()
        self._media = None
        self._mrl: str | None = None
        self._subtitle: str | None = None
        self._last_time = 0
        self._length = 0
        self._rate = 1.0
        self._volume = 80
        self._muted = False
        self._dragging = False
        self._want_playing = False
        self._settled = False
        self._buffering = 100.0
        self._playing = False
        self._spu_track = -1
        self._cues: CueTrack | None = None  # SRT/VTT drawn by the overlay instead of VLC
        self._cues_on = True
        self._chapters: list[dict] = []  # {"start_ms", "title"}
        self._steps: list[int] = []  # study step starts in ms
        self._chapters_length = 0
        self._seek_target: int | None = None
        self._seek_deadline = 0.0
        self._ended_early = False
        self._watched_window: QWidget | None = None
        self._ratio = DEFAULT_RATIO  # height / width of the current video
        self._fill = False
        self.setMinimumSize(320, 180)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self.frame = VideoFrame(self)
        self._attach_window()
        self._player.video_set_mouse_input(False)
        self._player.video_set_key_input(False)

        self.overlay = PlayerOverlay(self, lambda: self._playing)
        self.overlay.clicked.connect(self.toggle_play)
        self.overlay.double_clicked.connect(self._on_double_click)
        self.overlay.up_next.play_now.connect(self.up_next_accepted)
        self.overlay.up_next.cancelled.connect(self.up_next_cancelled)
        self.status_label = self.overlay.status_label

        self._icon_play = glyph_icon("play")
        self._icon_pause = glyph_icon("pause")
        self._icon_volume = glyph_icon("volume")
        self._icon_muted = glyph_icon("muted")

        self.slider = SeekSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName("SeekSlider")
        self.slider.setRange(0, _SLIDER_MAX)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.hovered.connect(self._on_slider_hover)
        self.slider.hover_left.connect(self._hide_preview)
        self.seek_preview = self.overlay.seek_preview
        self._preview_bucket: int | None = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self._request_preview)
        try:
            self.previewer: FramePreviewer | None = FramePreviewer(self)
            self.previewer.frame_ready.connect(self._on_preview_frame)
        except Exception:
            self.previewer = None  # Qt Multimedia unavailable: the preview shows only the time

        self.play_btn = self._button(self._icon_play, "Play / Pause (Space)", self.toggle_play, 44, "PlayCircle")
        self.mute_btn = self._button(self._icon_volume, "Mute (M)", self.toggle_mute)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("VolumeSlider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self._volume)
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.setToolTip("Volume (Up / Down)")
        self.volume_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.volume_slider.valueChanged.connect(self.set_volume)
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("PlayerTime")

        self.cc_btn = self._button(glyph_icon("cc"), "Subtitles", self._toggle_subtitles)
        self.cc_btn.setCheckable(True)
        self.cc_btn.hide()
        self.settings_btn = self._button(glyph_icon("gear"), "Playback speed", self._show_settings)
        theater_btn = self._button(glyph_icon("theater"), "Theater mode (T)", lambda: self.theater_requested.emit())
        fullscreen_btn = self._button(
            glyph_icon("fullscreen"), "Fullscreen (F)", lambda: self.fullscreen_requested.emit()
        )

        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 12)
        row.setSpacing(8)
        row.addWidget(self.play_btn)
        row.addWidget(_pill(self.mute_btn, self.volume_slider))
        row.addWidget(_pill(self.time_label, margins=(16, 0, 16, 0)))
        row.addStretch(1)
        row.addWidget(_pill(self.cc_btn, self.settings_btn, theater_btn, fullscreen_btn, margins=(6, 0, 6, 0)))

        bar_layout = QVBoxLayout(self.overlay.bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(6)
        slider_row = QHBoxLayout()
        slider_row.setContentsMargins(12, 0, 12, 0)
        slider_row.addWidget(self.slider)
        bar_layout.addLayout(slider_row)
        bar_layout.addLayout(row)

        # VLC fires events on its own thread; route them through queued Qt signals.
        self._vlc_ended.connect(self._on_end)
        self._vlc_error.connect(lambda: self.error.emit("VLC could not play this video."))
        events = self._player.event_manager()
        events.event_attach(vlc.EventType.MediaPlayerEndReached, lambda _event: self._vlc_ended.emit())
        events.event_attach(vlc.EventType.MediaPlayerEncounteredError, lambda _event: self._vlc_error.emit())
        events.event_attach(vlc.EventType.MediaPlayerBuffering, self._on_vlc_buffering)

        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._cue_timer = QTimer(self)
        self._cue_timer.setInterval(100)
        self._cue_timer.timeout.connect(self._update_cue)
        self._cue_timer.start()

    def _button(self, icon: QIcon, tooltip: str, slot, size: int = 36, name: str = "OverlayButton") -> QPushButton:
        button = QPushButton()
        button.setObjectName(name)
        button.setIcon(icon)
        button.setIconSize(QSize(size // 2, size // 2))
        button.setFixedSize(size, size)
        button.setToolTip(tooltip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def _attach_window(self) -> None:
        handle = int(self.frame.winId())
        if sys.platform.startswith("win"):
            self._player.set_hwnd(handle)
        elif sys.platform == "darwin":
            self._player.set_nsobject(handle)
        else:
            self._player.set_xwindow(handle)

    # ---- overlay placement ----
    def _sync_overlay(self) -> None:
        window = self.window()
        if window is not self._watched_window:
            if self._watched_window is not None:
                self._watched_window.removeEventFilter(self)
            window.installEventFilter(self)
            self._watched_window = window
        if not (self.isVisible() and window.isVisible() and not window.isMinimized()):
            if self.overlay.isVisible():
                self.overlay.hide()
            return
        rect = QRect(self.frame.mapToGlobal(QPoint(0, 0)), self.frame.size())
        if self.overlay.geometry() != rect:
            self.overlay.setGeometry(rect)
        if not self.overlay.isVisible():
            self.overlay.show()

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.WindowStateChange,
                            QEvent.Type.Show, QEvent.Type.Hide):
            QTimer.singleShot(0, self._sync_overlay)
        return False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_overlay)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.overlay.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_frame()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._sync_overlay()

    # ---- aspect ratio ----
    def set_fill_mode(self, fill: bool) -> None:
        """Fullscreen: use the whole area. Normal: keep the box at the video's aspect ratio."""
        self._fill = fill
        if fill:
            self.setMaximumHeight(_QWIDGETSIZE_MAX)
        self._fit_frame()

    def _fit_frame(self) -> None:
        if not self._fill:
            # Cap our own height so the layout hands the spare space to the widgets below.
            cap = max(self.minimumHeight(), round(self.width() * self._ratio))
            if self.maximumHeight() != cap:
                self.setMaximumHeight(cap)  # may resize us synchronously, so read the size afterwards
        width, height = self.width(), self.height()
        if self._fill:
            rect = QRect(0, 0, width, height)
        elif height >= width * self._ratio:
            frame_height = round(width * self._ratio)
            rect = QRect(0, (height - frame_height) // 2, width, frame_height)
        else:
            frame_width = round(height / self._ratio)
            rect = QRect((width - frame_width) // 2, 0, frame_width, height)
        if self.frame.geometry() != rect:
            self.frame.setGeometry(rect)
        self._sync_overlay()

    def _detect_ratio(self) -> None:
        try:
            video_width, video_height = self._player.video_get_size(0)
        except Exception:
            return
        if video_width > 0 and video_height > 0:
            ratio = video_height / video_width
            if abs(ratio - self._ratio) > 0.005:
                self._ratio = ratio
                self._fit_frame()

    # ---- public API ----
    def load(self, mrl: str, start_ms: int = 0, autoplay: bool = True, subtitle: str | None = None,
             length_hint: int = 0) -> None:
        """length_hint (ms, e.g. from the library) lets the seek bar work before VLC has parsed the file."""
        self.overlay.up_next.stop()
        self._player.stop()
        media = self._instance.media_new(mrl) if "://" in mrl else self._instance.media_new_path(mrl)
        if "://" in mrl:
            media.add_option(f":network-caching={NETWORK_CACHING_MS}")
        if start_ms > 0:
            media.add_option(f":start-time={start_ms / 1000:.3f}")
        self._cues = None
        self.overlay.set_subtitle("")
        if subtitle and not self._use_overlay_subtitle(subtitle):
            media.slaves_add(vlc.MediaSlaveType.subtitle, 4, Path(subtitle).as_uri())
        self._media = media
        self._mrl, self._subtitle = mrl, subtitle
        self._last_time, self._length = max(0, start_ms), max(0, length_hint)
        self._want_playing = autoplay
        self._settled = False
        self._buffering = 0.0
        self._spu_track = -1
        self._seek_target = None
        self._ended_early = False
        self.slider.set_buffered([])
        self.overlay.spinner.set_percent(None)
        self.overlay.spinner.show()
        self._hide_preview()
        self.seek_preview.clear_image()
        if self.previewer is not None:
            # Seek previews must not slow down playback, so the proxy serves them at background priority.
            self.previewer.set_source(f"{mrl}?preview=1" if "/tg/" in mrl else mrl)
        self.cc_btn.hide()
        # ":start-paused" + ":start-time" freezes VLC 3, so start playing muted and pause once ready.
        self._player.audio_set_mute(self._muted or not autoplay)
        self._player.set_media(media)
        self._player.play()
        self._update_time_label()

    def stop(self) -> None:
        self.overlay.up_next.stop()
        self._player.stop()
        self._media = None
        self._mrl = None
        self._last_time = self._length = 0
        self.slider.setValue(0)
        self.slider.set_buffered([])
        self.status_label.clear()
        self.status_label.hide()
        self.overlay.spinner.hide()
        self._hide_preview()
        if self.previewer is not None:
            self.previewer.reset()
        self.cc_btn.hide()
        self._cues = None
        self.overlay.set_subtitle("")
        self._chapters = []
        self.slider.set_chapters([])
        self._steps = []
        self.slider.set_steps([])
        self._set_playing(False)
        self._update_time_label()

    def release(self) -> None:
        self._timer.stop()
        self._cue_timer.stop()
        if self.previewer is not None:
            self.previewer.reset()
        self.overlay.close()
        self._player.stop()
        self._player.release()
        self._instance.release()

    def has_media(self) -> bool:
        return self._mrl is not None

    def is_playing(self) -> bool:
        return self._playing

    def set_chapters(self, chapters: list[dict]) -> None:
        self._chapters = sorted(chapters, key=lambda c: c["start_ms"])
        self._apply_chapters()
        self._update_time_label()

    def set_steps(self, starts_ms: list[int]) -> None:
        self._steps = sorted(starts_ms)
        self._apply_chapters()

    def _apply_chapters(self) -> None:
        self._chapters_length = self._length

        def fractions(starts: list[int]) -> list[float]:
            return [ms / self._length for ms in starts if 0 < ms < self._length] if self._length > 0 else []

        self.slider.set_chapters(fractions([c["start_ms"] for c in self._chapters]))
        self.slider.set_steps(fractions(self._steps))

    def chapter_at(self, ms: int) -> str:
        title = ""
        for chapter in self._chapters:
            if chapter["start_ms"] > ms:
                break
            title = chapter["title"]
        return title

    def has_subtitle(self) -> bool:
        return self._subtitle is not None

    def add_subtitle(self, path: str) -> None:
        """Attach a subtitle file to the playing video (e.g. a transcript that just finished) and show it."""
        if self._mrl is None:
            return
        self._subtitle = path  # reloads (seek while opening, replay) keep it
        if not self._use_overlay_subtitle(path):
            self._player.add_slave(vlc.MediaSlaveType.subtitle, Path(path).as_uri(), True)

    def _use_overlay_subtitle(self, path: str) -> bool:
        """SRT/VTT are drawn by the overlay (so they can move around the controls); others go to VLC."""
        if Path(path).suffix.lower() not in OVERLAY_FORMATS:
            return False
        try:
            self._cues = CueTrack(parse_subtitles(path))
        except OSError:
            return False
        return True

    def _update_cue(self) -> None:
        text = ""
        if self._cues is not None and self._cues_on and self._settled and self._seek_target is None:
            position = self._player.get_time()
            if position >= 0:
                text = self._cues.text_at(position)
        self.overlay.set_subtitle(text)

    def time(self) -> int:
        return self._last_time

    def length(self) -> int:
        return self._length

    def rate(self) -> float:
        return self._rate

    def volume(self) -> int:
        return self._volume

    def toggle_play(self) -> None:
        if self._mrl is None:
            return
        state = self._player.get_state()
        if state == vlc.State.Ended or self._ended_early:
            self.load(self._mrl, 0, True, self._subtitle, self._length)
        elif state in (vlc.State.Stopped, vlc.State.Error):
            self.load(self._mrl, self._last_time, True, self._subtitle, self._length)
        elif not self._settled:
            # Still opening: remember the intent and apply it once VLC is ready.
            self._want_playing = not self._want_playing
            self._set_playing(self._want_playing)
        else:
            self._want_playing = state != vlc.State.Playing
            self._player.set_pause(0 if self._want_playing else 1)
            self._set_playing(self._want_playing)

    def seek(self, ms: int) -> None:
        if self._mrl is None:
            return
        ms = max(0, int(ms))
        if self._length > 0 and ms >= self._length - END_SNAP_MS:
            self._finish_early()
            return
        if self._player.get_state() in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error):
            self.load(self._mrl, ms, True, self._subtitle, self._length)
        elif not self._settled:
            # VLC ignores set_time while still opening; reopening at the new spot is one load instead of two.
            self.load(self._mrl, ms, self._want_playing, self._subtitle, self._length)
        else:
            self._start_seek(ms)
        self._last_time = ms
        self._update_time_label()

    def _start_seek(self, ms: int) -> None:
        self._player.set_time(ms)
        self._seek_target = ms
        self._seek_deadline = time.monotonic() + SEEK_GRACE_S

    def _finish_early(self) -> None:
        """User jumped to the very end: behave as if the video played to its last frame."""
        self._player.stop()
        self._ended_early = True
        self._seek_target = None
        self._on_end()

    def seek_relative(self, delta_ms: int) -> None:
        self.seek(self._last_time + delta_ms)

    def set_rate(self, rate: float) -> None:
        self._rate = float(rate)
        self._player.set_rate(self._rate)
        self.settings_btn.setToolTip(f"Playback speed: {self._rate:g}x")

    def change_rate(self, step: int) -> None:
        current = min(range(len(SPEEDS)), key=lambda i: abs(SPEEDS[i] - self._rate))
        self.set_rate(SPEEDS[max(0, min(len(SPEEDS) - 1, current + step))])

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, int(volume)))
        self._player.audio_set_volume(self._volume)
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(self._volume)
        self.volume_slider.blockSignals(False)
        if self._muted and self._volume > 0:
            self.toggle_mute()

    def change_volume(self, delta: int) -> None:
        self.set_volume(self._volume + delta)

    def show_up_next(self, title: str, thumbnail: QPixmap | None, seconds: int) -> None:
        self.overlay.up_next.start(title, thumbnail, seconds)

    def follow_up_next(self, title: str, thumbnail: QPixmap | None, total_ms: int) -> None:
        self.overlay.up_next.follow(title, thumbnail, total_ms)

    def set_up_next_remaining(self, remaining_ms: int) -> None:
        self.overlay.up_next.set_remaining(remaining_ms)

    def hide_up_next(self) -> None:
        self.overlay.up_next.stop()

    def toggle_mute(self) -> None:
        self._muted = not self._muted
        self._player.audio_set_mute(self._muted)
        self.mute_btn.setIcon(self._icon_muted if self._muted else self._icon_volume)

    # ---- internals ----
    def _on_double_click(self) -> None:
        self.toggle_play()  # undo the toggle from the first click, like YouTube
        self.fullscreen_requested.emit()

    def _show_settings(self) -> None:
        menu = QMenu(self.overlay)
        menu.addSection("Playback speed")
        for speed in SPEEDS:
            action = menu.addAction("Normal" if speed == 1.0 else f"{speed:g}x")
            action.setCheckable(True)
            action.setChecked(abs(speed - self._rate) < 0.01)
            action.triggered.connect(lambda _checked=False, speed=speed: self.set_rate(speed))
        anchor = self.settings_btn.mapToGlobal(QPoint(0, 0))
        menu.exec(anchor - QPoint(0, menu.sizeHint().height() + 8))

    def _toggle_subtitles(self, enabled: bool) -> None:
        if self._cues is not None:
            self._cues_on = enabled
            self._update_cue()
            return
        if enabled:
            tracks = [track_id for track_id, _name in (self._player.video_get_spu_description() or []) if track_id >= 0]
            if tracks:
                self._player.video_set_spu(self._spu_track if self._spu_track in tracks else tracks[-1])
        else:
            current = self._player.video_get_spu()
            if current >= 0:
                self._spu_track = current
            self._player.video_set_spu(-1)

    def _set_playing(self, playing: bool) -> None:
        if playing != self._playing:
            self._playing = playing
            self.play_btn.setIcon(self._icon_pause if playing else self._icon_play)

    def _poll(self) -> None:
        self._sync_overlay()
        if self._mrl is None:
            return
        state = self._player.get_state()
        playing = state == vlc.State.Playing
        ready = state == vlc.State.Paused or (playing and self._player.get_time() > 0)
        if not self._settled and ready:
            self._settled = True
            if self._want_playing != playing:
                self._player.set_pause(0 if self._want_playing else 1)
                playing = self._want_playing  # set_pause is async; report the state we just requested
            self._player.audio_set_mute(self._muted)
        self._update_status(state)
        if playing:
            if abs(self._player.get_rate() - self._rate) > 0.01:
                self._player.set_rate(self._rate)
            current_volume = self._player.audio_get_volume()
            if current_volume >= 0 and current_volume != self._volume:
                self._player.audio_set_volume(self._volume)
        self._set_playing(playing if self._settled else self._want_playing)
        if self._settled:
            self._detect_ratio()
            has_vlc_subtitles = self._player.video_get_spu_count() > 0
            self.cc_btn.setVisible(self._cues is not None or has_vlc_subtitles)
            self.cc_btn.blockSignals(True)
            self.cc_btn.setChecked(self._cues_on if self._cues is not None
                                   else has_vlc_subtitles and self._player.video_get_spu() >= 0)
            self.cc_btn.blockSignals(False)

        length = self._player.get_length()
        if length > 0:
            self._length = length
        if (self._chapters or self._steps) and self._length != self._chapters_length:
            self._apply_chapters()
        position = self._player.get_time()
        # Before playback settles VLC reports 0 while buffering, which must not overwrite the resume position.
        if self._settled and position >= 0 and (playing or (state == vlc.State.Paused and position > 0)):
            if self._seek_target is not None and (
                abs(position - self._seek_target) <= 3000 or time.monotonic() > self._seek_deadline
            ):
                self._seek_target = None
            if self._seek_target is None:  # while VLC is still seeking it reports the old time
                self._last_time = position
        if not self._dragging and self._length > 0:
            self.slider.blockSignals(True)
            self.slider.setValue(int(self._last_time * _SLIDER_MAX / self._length))
            self.slider.blockSignals(False)
        self._update_time_label()
        if self._length > 0 and state in (vlc.State.Playing, vlc.State.Paused):
            self.position_changed.emit(self._last_time, self._length)

    def _on_vlc_buffering(self, event) -> None:
        self._buffering = event.u.new_cache  # VLC thread: plain float store only

    def _update_status(self, state) -> None:
        loading = state == vlc.State.Opening or (state == vlc.State.Buffering and not self._settled)
        buffering = state in (vlc.State.Playing, vlc.State.Buffering) and (
            self._buffering < 100 or self._seek_target is not None
        )
        if loading or buffering:
            self.overlay.spinner.set_percent(int(self._buffering) if 0 < self._buffering < 100 else None)
        if self.overlay.spinner.isVisible() != (loading or buffering):
            self.overlay.spinner.setVisible(loading or buffering)
        text = "Playback error" if state == vlc.State.Error else ""
        if self.status_label.text() != text:
            self.status_label.setText(text)
            self.status_label.setVisible(bool(text))

    def _on_end(self) -> None:
        if self._length > 0:
            self._last_time = self._length
            self.position_changed.emit(self._length, self._length)
        self.slider.setValue(_SLIDER_MAX)
        self._set_playing(False)
        self._update_time_label()
        self.ended.emit()

    def _on_slider_pressed(self) -> None:
        self._dragging = True

    def _on_slider_released(self) -> None:
        self._dragging = False
        if self._length > 0:
            self.seek(self.slider.value() * self._length // _SLIDER_MAX)

    def _on_slider_moved(self, value: int) -> None:
        if self._length > 0:
            self.time_label.setText(f"{format_ms(value * self._length // _SLIDER_MAX)} / {format_ms(self._length)}")

    # ---- seek preview ----
    def _on_slider_hover(self, fraction: float, x: int) -> None:
        if self._length <= 0:
            return
        ms = int(fraction * self._length)
        step = max(2000, self._length // 200)
        self._preview_bucket = ms // step * step
        card = self.seek_preview
        card.time.setText(format_ms(ms))
        card.set_chapter(self.chapter_at(ms))
        image = self.previewer.cached(self._preview_bucket) if self.previewer is not None else None
        if image is not None:
            card.set_image(image)
        else:
            self._preview_timer.start()  # debounce: only decode where the cursor rests
        card.adjustSize()
        anchor = self.slider.mapTo(self.overlay, QPoint(x, 0))
        left = max(8, min(self.overlay.width() - card.width() - 8, anchor.x() - card.width() // 2))
        card.move(left, anchor.y() - card.height() - 6)
        if not card.isVisible():
            card.show()
        card.raise_()

    def _request_preview(self) -> None:
        if self.previewer is not None and self._preview_bucket is not None and self.seek_preview.isVisible():
            self.previewer.request(self._preview_bucket)

    def _on_preview_frame(self, bucket: int) -> None:
        if bucket == self._preview_bucket and self.seek_preview.isVisible():
            self.seek_preview.set_image(self.previewer.cached(bucket))

    def _hide_preview(self) -> None:
        self._preview_timer.stop()
        self.seek_preview.hide()

    def _update_time_label(self) -> None:
        if not self._dragging:
            text = f"{format_ms(self._last_time)} / {format_ms(self._length)}"
            if title := self.chapter_at(self._last_time):
                text += f"  \u2022  {title if len(title) <= 40 else title[:39] + '\u2026'}"
            self.time_label.setText(text)
