import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("vlc")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.player.vlc_widget import SeekSlider  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def slider(app):
    widget = SeekSlider(Qt.Orientation.Horizontal)
    widget.setRange(0, 1000)
    widget.resize(800, 20)
    widget.show()
    events = []
    widget.sliderPressed.connect(lambda: events.append("pressed"))
    widget.sliderReleased.connect(lambda: events.append(("released", widget.value())))
    widget.events = events
    yield widget
    widget.close()


@pytest.mark.parametrize("x, expected", [(799, 1000), (0, 0), (400, 500)])
def test_click_seeks_and_always_releases(slider, x, expected):
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, 10))
    assert slider.events[0] == "pressed"
    released = slider.events[-1]
    assert released[0] == "released"
    assert abs(released[1] - expected) <= 5


def test_drag_scrubs(slider):
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(100, 10))
    QTest.mouseMove(slider, QPoint(600, 10))
    assert slider.value() > 700
    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(600, 10))
    assert slider.events[-1][0] == "released"
