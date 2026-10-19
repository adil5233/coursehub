"""Animated progress bar for making a study pack: a gradient fill with a moving shimmer and a glowing tip."""
import re

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

_PART = re.compile(r"part (\d+) of (\d+)", re.IGNORECASE)
PARTS_SHARE = 0.9  # the transcript parts are 90 % of the work; the mantra is the rest
MANTRA_AT = 0.93
CREEP_EVERY_MS = 120
CREEP_RATE = 0.012  # inside a part the bar drifts towards the part's end, never past it
COLORS = ("#3ea6ff", "#a970ff", "#ff3d71")  # blue -> violet -> red, like the app's accent


def stage_range(text: str) -> tuple[float, float] | None:
    """(start, end) fraction of the work the status text stands for, or None when it does not say."""
    match = _PART.search(text)
    if match:
        index, total = int(match.group(1)), max(1, int(match.group(2)))
        return (index - 1) / total * PARTS_SHARE, index / total * PARTS_SHARE
    if "mantra" in text.lower():
        return MANTRA_AT, 0.99
    return None


class GlowBar(QWidget):
    """The bar itself; value 0..1, or None for an indeterminate sweep."""

    HEIGHT = 10

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.value: float | None = None
        self.phase = 0.0
        self._shimmer = QVariantAnimation(self)
        self._shimmer.setStartValue(0.0)
        self._shimmer.setEndValue(1.0)
        self._shimmer.setDuration(1400)
        self._shimmer.setLoopCount(-1)
        self._shimmer.valueChanged.connect(self._set_phase)

    def _set_phase(self, phase) -> None:
        self.phase = float(phase)
        self.update()

    def start_shimmer(self) -> None:
        self._shimmer.start()

    def stop_shimmer(self) -> None:
        self._shimmer.stop()

    def paintEvent(self, event) -> None:
        width, height = float(self.width()), float(self.height())
        radius = height / 2
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
        painter.fillPath(track, QColor(255, 255, 255, 28))
        painter.setClipPath(track)

        if self.value is None:  # unknown progress: a soft band sweeps across
            band = width * 0.35
            left = -band + (width + band) * self.phase
            fill = QRectF(left, 0, band, height)
            gradient = QLinearGradient(fill.left(), 0, fill.right(), 0)
            gradient.setColorAt(0.0, QColor(62, 166, 255, 0))
            gradient.setColorAt(0.5, QColor(COLORS[1]))
            gradient.setColorAt(1.0, QColor(255, 61, 113, 0))
            painter.fillRect(fill, gradient)
            painter.end()
            return

        filled = max(height, width * max(0.0, min(1.0, self.value)))
        fill = QRectF(0, 0, filled, height)
        gradient = QLinearGradient(0, 0, width, 0)  # colours sit on the whole track: the bar "warms up" as it grows
        for position, color in zip((0.0, 0.5, 1.0), COLORS):
            gradient.setColorAt(position, QColor(color))
        path = QPainterPath()
        path.addRoundedRect(fill, radius, radius)
        painter.fillPath(path, gradient)

        painter.setClipPath(path)  # shimmer: a light band running over the filled part only
        band = max(40.0, filled * 0.25)
        left = -band + (filled + band) * self.phase
        shine = QLinearGradient(left, 0, left + band, 0)
        shine.setColorAt(0.0, QColor(255, 255, 255, 0))
        shine.setColorAt(0.5, QColor(255, 255, 255, 110))
        shine.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(QRectF(left, 0, band, height), shine)

        painter.setClipping(False)  # a pulsing glow at the tip
        pulse = 0.6 + 0.4 * abs(1 - 2 * self.phase)
        tip = QPointF(filled - radius, height / 2)
        glow = QRadialGradient(tip, height * 1.4)
        tip_color = QColor(COLORS[2] if self.value > 0.66 else COLORS[1] if self.value > 0.33 else COLORS[0])
        tip_color.setAlphaF(0.75 * pulse)
        glow.setColorAt(0.0, tip_color)
        tip_color.setAlphaF(0.0)
        glow.setColorAt(1.0, tip_color)
        painter.setBrush(glow)
        painter.drawEllipse(tip, height * 1.4, height * 1.4)
        painter.end()


class PackProgress(QWidget):
    """Bar + percentage. update_stage(text) follows the study-pack status; finish() / fail() end it."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.bar = GlowBar()
        self.percent = QLabel()
        self.percent.setObjectName("PackPercent")
        self.percent.setFixedWidth(42)
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)
        layout.addWidget(self.bar, 1)
        layout.addWidget(self.percent)
        self.value = 0.0
        self.cap = 0.0  # the bar may drift up to here while the current stage runs
        self._grow = QVariantAnimation(self)
        self._grow.setDuration(450)
        self._grow.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._grow.valueChanged.connect(self._show_value)
        self._creep = QTimer(self)
        self._creep.setInterval(CREEP_EVERY_MS)
        self._creep.timeout.connect(self._drift)
        self._hide_later = QTimer(self)
        self._hide_later.setSingleShot(True)
        self._hide_later.setInterval(700)
        self._hide_later.timeout.connect(self.hide)
        self.hide()

    def finishing(self) -> bool:
        """Showing 100 % for a moment before it hides."""
        return self._hide_later.isActive()

    def start(self) -> None:
        self._hide_later.stop()
        self._grow.stop()
        self.value, self.cap = 0.0, 0.0
        self.bar.value = None  # sweep until the first "part 1 of N"
        self.percent.setText("")
        self.bar.start_shimmer()
        self._creep.start()
        self.show()

    def update_stage(self, text: str) -> None:
        if self.isHidden() or not self._creep.isActive():  # not running yet (or the last pack just finished)
            self.start()
        stage = stage_range(text)
        if stage is None:  # e.g. "switching to ..." or "Waiting 20 s": keep the bar where it is
            return
        start, end = stage
        self.cap = end - 0.01
        if self.bar.value is None:
            self.bar.value = 0.0
        if start > self.value:
            self._animate_to(start)

    def finish(self) -> None:
        """Done: fill up to 100 %, then fade away."""
        if self.isHidden():
            return
        self._creep.stop()
        if self.bar.value is None:
            self.bar.value = 0.0
        self.cap = 1.0
        self._animate_to(1.0)
        self._hide_later.start()

    def fail(self) -> None:
        self._creep.stop()
        self._grow.stop()
        self.bar.stop_shimmer()
        self.hide()

    def hideEvent(self, event) -> None:
        self.bar.stop_shimmer()
        self._creep.stop()
        super().hideEvent(event)

    def _animate_to(self, target: float) -> None:
        self._grow.stop()
        self._grow.setStartValue(self.value)
        self._grow.setEndValue(target)
        self._grow.start()

    def _drift(self) -> None:
        if self.bar.value is None or self._grow.state() == QVariantAnimation.State.Running:
            return
        if self.value < self.cap:
            self._show_value(self.value + (self.cap - self.value) * CREEP_RATE)

    def _show_value(self, value) -> None:
        self.value = float(value)
        self.bar.value = self.value
        self.percent.setText(f"{round(self.value * 100)}%")
        self.bar.update()
