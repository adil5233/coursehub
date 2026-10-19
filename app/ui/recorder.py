import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtWidgets import QPushButton

from ..config import format_ms
from ..transcribe.grade import BackgroundCall, speech_to_text

MAX_RECORD_MS = 180_000  # answers are short; stop on its own after 3 minutes
IDLE_TEXT = "\U0001f3a4 Bolo"  # 🎤


def has_microphone() -> bool:
    try:
        from PySide6.QtMultimedia import QMediaDevices
    except ImportError:
        return False
    return not QMediaDevices.defaultAudioInput().isNull()


class VoiceAnswerButton(QPushButton):
    """Record a spoken answer; Groq Whisper turns it into text (text_ready)."""

    text_ready = Signal(str)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(IDLE_TEXT, parent)
        self.setToolTip("Record your answer and turn it into text (Groq Whisper). Click again to stop.")
        self._recorder = None
        self._session = None
        self._audio_input = None
        self._path: Path | None = None
        self._elapsed_ms = 0
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self.speech = BackgroundCall(self)
        self.speech.progress.connect(self.setText)
        self.speech.finished.connect(self._on_text)
        self.speech.failed.connect(self._on_failed)
        self.clicked.connect(self._toggle)
        if not has_microphone():
            self.setEnabled(False)
            self.setToolTip("No microphone found")

    def is_recording(self) -> bool:
        return self._recorder is not None

    def _toggle(self) -> None:
        if self.is_recording():
            self.stop()
        elif not self.speech.busy:
            self.start()

    def start(self) -> None:
        from PySide6.QtMultimedia import QAudioInput, QMediaCaptureSession, QMediaFormat, QMediaRecorder

        handle, name = tempfile.mkstemp(prefix="coursehub-answer-", suffix=".m4a")
        os.close(handle)
        Path(name).unlink(missing_ok=True)  # the recorder creates the file itself
        self._path = Path(name)
        self._audio_input = QAudioInput(self)
        self._session = QMediaCaptureSession(self)
        self._session.setAudioInput(self._audio_input)
        self._recorder = QMediaRecorder(self)
        media_format = QMediaFormat(QMediaFormat.FileFormat.Mpeg4Audio)
        media_format.setAudioCodec(QMediaFormat.AudioCodec.AAC)
        self._recorder.setMediaFormat(media_format)
        self._recorder.setQuality(QMediaRecorder.Quality.NormalQuality)
        self._recorder.setOutputLocation(QUrl.fromLocalFile(str(self._path)))
        self._session.setRecorder(self._recorder)
        self._recorder.recorderStateChanged.connect(self._on_state)
        self._recorder.errorOccurred.connect(lambda _error, message: self._fail_recording(message))
        self._elapsed_ms = 0
        self._recorder.record()
        self._timer.start()
        self._tick()
        self.busy_changed.emit(True)

    def stop(self) -> None:
        if self._recorder is not None:
            self._recorder.stop()

    def _tick(self) -> None:
        if not self.is_recording():
            return
        self.setText(f"⏹ Stop  {format_ms(self._elapsed_ms)}")
        self._elapsed_ms += self._timer.interval()
        if self._elapsed_ms > MAX_RECORD_MS:
            self.stop()

    def _on_state(self, state) -> None:
        from PySide6.QtMultimedia import QMediaRecorder

        if state != QMediaRecorder.RecorderState.StoppedState or self._recorder is None:
            return
        actual = self._recorder.actualLocation().toLocalFile()
        self._teardown()
        self.transcribe_file(Path(actual) if actual else self._path)

    def transcribe_file(self, path: Path | None) -> None:
        """Send a finished recording to Whisper (also the entry point for tests)."""
        try:
            data = path.read_bytes() if path is not None else b""
        except OSError:
            data = b""
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
        if not data:
            self._on_failed("Nothing was recorded. Check the microphone.")
            return
        self.setEnabled(False)
        self.busy_changed.emit(True)
        self.speech.start(speech_to_text, data, path.name)

    def _fail_recording(self, message: str) -> None:
        self._teardown()
        self._on_failed(f"Recording failed: {message}")

    def _teardown(self) -> None:
        self._timer.stop()
        recorder, self._recorder = self._recorder, None
        if recorder is not None:
            recorder.deleteLater()
        for name in ("_session", "_audio_input"):
            obj = getattr(self, name)
            if obj is not None:
                obj.deleteLater()
            setattr(self, name, None)

    def _reset(self) -> None:
        self.setText(IDLE_TEXT)
        self.setEnabled(has_microphone())
        self.busy_changed.emit(False)

    def _on_text(self, text: str) -> None:
        self._reset()
        if text.strip():
            self.text_ready.emit(text.strip())
        else:
            self.failed.emit("Whisper heard nothing. Try again, a little louder.")

    def _on_failed(self, message: str) -> None:
        self._reset()
        self.failed.emit(message)
