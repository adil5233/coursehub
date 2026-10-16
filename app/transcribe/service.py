"""Background transcript jobs: audio chunks -> Groq Whisper -> .srt + segments, one lecture at a time."""
import math
import threading
from dataclasses import dataclass

import requests
from PySide6.QtCore import QObject, Signal

from ..config import format_ms
from . import groq_client, load_parts, merge_chunks, parts_path, save_parts, save_transcript
from .audio import CHUNK_S, Cancelled, NoAudio, iter_chunks

NETWORK_RETRIES = 3


@dataclass
class Job:
    video_id: int
    title: str
    source: str  # local path or stream-proxy URL
    duration_s: float


class TranscriptManager(QObject):
    progress = Signal(int, str, float)  # video_id, status text, fraction done (-1 = unknown)
    finished = Signal(int)
    failed = Signal(int, str)

    def __init__(self, services, parent=None) -> None:
        super().__init__(parent)
        self.services = services
        self._queue: list[Job] = []
        self._current: Job | None = None
        self._cond = threading.Condition()
        self._cancel = threading.Event()
        self._stopping = False
        self.status: dict[int, tuple[str, float]] = {}  # latest progress, for panels opened later
        self._thread = threading.Thread(target=self._worker, name="transcripts", daemon=True)
        self._thread.start()

    # ---- queue ----
    def enqueue(self, course, video, front: bool = False) -> None:
        """Queue a lecture (front=True: the one being watched goes first)."""
        source = self.services.media_for(course, video)
        if "/tg/" in source:
            source += "?bulk=1"  # transcripts read the whole file; don't let them starve or block playback
        job = Job(video.id, video.title, source, video.duration_ms / 1000)
        with self._cond:
            if self._current is not None and self._current.video_id == video.id:
                return
            self._queue = [j for j in self._queue if j.video_id != video.id]
            self._queue.insert(0 if front else len(self._queue), job)
            self.status[video.id] = ("Waiting in queue...", -1.0)
            self._cond.notify()
        self.progress.emit(video.id, "Waiting in queue...", -1.0)

    def is_busy(self, video_id: int) -> bool:
        with self._cond:
            return (self._current is not None and self._current.video_id == video_id) or any(
                j.video_id == video_id for j in self._queue
            )

    def cancel(self, video_id: int) -> None:
        with self._cond:
            self._queue = [j for j in self._queue if j.video_id != video_id]
            if self._current is not None and self._current.video_id == video_id:
                self._cancel.set()
        self.status.pop(video_id, None)
        self.failed.emit(video_id, "Cancelled.")

    def shutdown(self) -> None:
        with self._cond:
            self._stopping = True
            self._queue.clear()
            self._cancel.set()
            self._cond.notify()
        self._thread.join(timeout=5)

    # ---- worker thread ----
    def _worker(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._stopping:
                    self._cond.wait()
                if self._stopping:
                    return
                job = self._current = self._queue.pop(0)
                self._cancel.clear()
            try:
                self._run(job)
            except Cancelled:
                pass
            except groq_client.InvalidKey as exc:
                self._fail(job, f"{exc} Check the key in Settings.")
                with self._cond:
                    dropped, self._queue = self._queue, []
                for other in dropped:  # the same key would fail for every queued lecture
                    self._fail(other, "Groq API key is not valid.")
            except NoAudio as exc:
                self._fail(job, str(exc))
            except Exception as exc:
                self._fail(job, f"Transcript failed: {exc}")
            finally:
                with self._cond:
                    self._current = None

    def _report(self, job: Job, text: str, fraction: float) -> None:
        self.status[job.video_id] = (text, fraction)
        self.progress.emit(job.video_id, text, fraction)

    def _fail(self, job: Job, message: str) -> None:
        self.status.pop(job.video_id, None)
        self.failed.emit(job.video_id, message)

    def _run(self, job: Job) -> None:
        api_key = groq_client.load_settings()["api_key"]
        if not api_key:
            raise groq_client.InvalidKey("No Groq API key.")
        parts = load_parts(job.video_id)
        total = max(1, math.ceil(job.duration_s / CHUNK_S)) if job.duration_s > 0 else 0
        read_s = 0.0

        def report() -> None:
            done = f"{len(parts)} of {total} parts transcribed" if total else f"{len(parts)} parts transcribed"
            if job.duration_s > 0:
                where = f"Reading audio {format_ms(int(read_s * 1000))} of {format_ms(int(job.duration_s * 1000))}"
                fraction = min(1.0, read_s / job.duration_s)
            else:
                where, fraction = f"Reading audio {format_ms(int(read_s * 1000))}", -1.0
            self._report(job, f"{where}\n{done}", fraction)

        def on_progress(seconds: float) -> None:
            nonlocal read_s
            read_s = seconds
            report()

        report()
        for chunk in iter_chunks(job.source, skip=set(parts), cancel=self._cancel, on_progress=on_progress):
            read_s = max(read_s, chunk.offset + chunk.duration)
            segments = self._transcribe_chunk(job, chunk.data)
            parts[chunk.index] = (chunk.offset, segments)
            save_parts(job.video_id, parts)
            groq_client.add_usage(chunk.duration)
            report()
        save_transcript(job.video_id, merge_chunks(list(parts.values())))
        parts_path(job.video_id).unlink(missing_ok=True)
        self.status.pop(job.video_id, None)
        self.finished.emit(job.video_id)

    def _transcribe_chunk(self, job: Job, data: bytes):
        failures = 0
        while True:
            try:
                api_key = groq_client.load_settings()["api_key"]
                return groq_client.transcribe(api_key, data)
            except groq_client.RateLimited as exc:
                wait = max(5.0, exc.retry_after)
                self._report(job, f"Waiting {math.ceil(wait)} s for the Groq free limit...",
                             self.status.get(job.video_id, ("", -1.0))[1])
                if self._cancel.wait(wait):
                    raise Cancelled() from None
            except requests.RequestException:
                failures += 1
                if failures > NETWORK_RETRIES:
                    raise
                if self._cancel.wait(10 * failures):
                    raise Cancelled() from None
