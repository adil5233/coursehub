"""Lecture transcripts: segments, SRT files and merging per-chunk results."""
import json
from dataclasses import dataclass
from pathlib import Path

from ..config import TRANSCRIPTS_DIR


@dataclass
class Segment:
    start: float  # seconds
    end: float
    text: str


def srt_time(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def to_srt(segments: list[Segment]) -> str:
    blocks = [
        f"{number}\n{srt_time(s.start)} --> {srt_time(s.end)}\n{s.text}\n"
        for number, s in enumerate(segments, 1)
    ]
    return "\n".join(blocks)


def merge_chunks(chunks: list[tuple[float, list[Segment]]]) -> list[Segment]:
    """Join per-chunk results (chunk start offset, segments relative to it) into one timeline.

    Each chunk's audio runs a little past the next chunk's start so no word is cut in half;
    a segment belongs to the chunk in which it starts.
    """
    ordered = sorted(chunks, key=lambda chunk: chunk[0])
    merged: list[Segment] = []
    for position, (offset, segments) in enumerate(ordered):
        limit = ordered[position + 1][0] if position + 1 < len(ordered) else float("inf")
        for s in segments:
            start = offset + s.start
            if start < limit and s.text.strip():
                merged.append(Segment(start, offset + s.end, s.text.strip()))
    return merged


def srt_path(video_id: int) -> Path:
    return TRANSCRIPTS_DIR / f"{video_id}.srt"


def segments_path(video_id: int) -> Path:
    return TRANSCRIPTS_DIR / f"{video_id}.json"


def parts_path(video_id: int) -> Path:
    return TRANSCRIPTS_DIR / f"{video_id}.parts.json"


def has_transcript(video_id: int) -> bool:
    return segments_path(video_id).is_file()


def save_transcript(video_id: int, segments: list[Segment]) -> None:
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    srt_path(video_id).write_text(to_srt(segments), encoding="utf-8")
    data = [[round(s.start, 2), round(s.end, 2), s.text] for s in segments]
    segments_path(video_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_transcript(video_id: int) -> list[Segment] | None:
    try:
        data = json.loads(segments_path(video_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return [Segment(float(start), float(end), str(text)) for start, end, text in data]


def load_parts(video_id: int) -> dict[int, tuple[float, list[Segment]]]:
    """Chunks already transcribed in an earlier run, so a restart continues where it stopped."""
    try:
        data = json.loads(parts_path(video_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        int(index): (float(chunk["offset"]), [Segment(*segment) for segment in chunk["segments"]])
        for index, chunk in data.items()
    }


def save_parts(video_id: int, parts: dict[int, tuple[float, list[Segment]]]) -> None:
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        str(index): {"offset": offset, "segments": [[s.start, s.end, s.text] for s in segments]}
        for index, (offset, segments) in parts.items()
    }
    temp = parts_path(video_id).with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(parts_path(video_id))
