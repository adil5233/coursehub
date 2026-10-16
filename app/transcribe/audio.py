"""Extract a video's audio as small MP3 chunks for speech-to-text (PyAV / FFmpeg)."""
import io
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import av

SAMPLE_RATE = 16_000  # what Whisper works at; keeps uploads small
BIT_RATE = 48_000
CHUNK_S = 600  # 10 minutes ~ 3.6 MB, far below Groq's 25 MB upload limit
OVERLAP_S = 2  # each chunk runs this far into the next so no word is cut in half
PROGRESS_EVERY_S = 30  # report the read position every 30 s of audio


class NoAudio(Exception):
    pass


class Cancelled(Exception):
    pass


@dataclass
class AudioChunk:
    index: int
    offset: float  # seconds from the start of the video
    duration: float
    data: bytes


class _Mp3Writer:
    def __init__(self, index: int, offset: float) -> None:
        self.index, self.offset = index, offset
        self.samples = 0
        self._buffer = io.BytesIO()
        self._out = av.open(self._buffer, "w", format="mp3")
        self._stream = self._out.add_stream("libmp3lame", rate=SAMPLE_RATE, layout="mono")
        self._stream.bit_rate = BIT_RATE

    def write(self, frame: av.AudioFrame) -> None:
        frame.pts = None  # the same frame may go into two chunks (overlap)
        self.samples += frame.samples
        for packet in self._stream.encode(frame):
            self._out.mux(packet)

    def finish(self) -> AudioChunk:
        for packet in self._stream.encode(None):
            self._out.mux(packet)
        self._out.close()
        return AudioChunk(self.index, self.offset, self.samples / SAMPLE_RATE, self._buffer.getvalue())


def media_duration(source: str) -> float:
    with av.open(source, timeout=(30, 60)) as container:
        return (container.duration or 0) / av.time_base


def iter_chunks(source: str, skip: set[int] | None = None, cancel: threading.Event | None = None,
                chunk_s: int = CHUNK_S, overlap_s: float = OVERLAP_S,
                on_progress: Callable[[float], None] | None = None) -> Iterator[AudioChunk]:
    """Yield the audio in order as chunk_s-long MP3s, each overlapping the next by overlap_s.

    Chunks listed in `skip` (already transcribed) are decoded but not encoded.
    on_progress(seconds) reports how far the audio has been read (for streams this is the download).
    """
    skip = skip or set()
    with av.open(source, timeout=(30, 120)) as container:
        if not container.streams.audio:
            raise NoAudio("This video has no audio track.")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16p", layout="mono", rate=SAMPLE_RATE)
        writers: dict[int, _Mp3Writer] = {}
        position = 0  # samples at the resampled rate

        def frames():
            for packet in container.demux(stream):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                try:
                    decoded = packet.decode()
                except av.error.InvalidDataError:
                    continue  # a damaged packet: skip it instead of failing the whole lecture
                for frame in decoded:
                    yield from resampler.resample(frame)
            yield from resampler.resample(None)

        for frame in frames():
            t = position / SAMPLE_RATE
            if on_progress is not None and int(t // PROGRESS_EVERY_S) != int((t - frame.samples / SAMPLE_RATE)
                                                                            // PROGRESS_EVERY_S):
                on_progress(t)
            position += frame.samples
            index = int(t // chunk_s)
            for k in (index - 1, index):
                inside = k == index or t < (k + 1) * chunk_s + overlap_s
                if k < 0 or k in skip or not inside:
                    continue
                if k not in writers:
                    writers[k] = _Mp3Writer(k, k * chunk_s)
                writers[k].write(frame)
            for k in sorted(writers):
                if k < index and t >= (k + 1) * chunk_s + overlap_s:
                    yield writers.pop(k).finish()
        for k in sorted(writers):
            yield writers.pop(k).finish()
