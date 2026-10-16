import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.transcribe import Segment  # noqa: E402
from app.ui.transcript_panel import TranscriptPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_copy_whole_transcript(app):
    panel = TranscriptPanel()
    panel.show_segments([Segment(0, 2, "Namaste guys."), Segment(65, 70, "Aaj loops padhenge.")])
    panel.search.setText("loops")  # copying ignores the search filter
    assert not panel.copy_button.isHidden()

    panel._copy(timestamps=False)
    assert QApplication.clipboard().text() == "Namaste guys. Aaj loops padhenge."
    panel._copy(timestamps=True)
    assert QApplication.clipboard().text() == "[0:00] Namaste guys.\n[1:05] Aaj loops padhenge."

    panel.show_idle("No transcript yet.")
    assert panel.copy_button.isHidden()
