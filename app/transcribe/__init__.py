"""Lecture transcripts: segments, SRT files and merging per-chunk results."""
import json
from dataclasses import dataclass
from pathlib import Path

from ..config import TRANSCRIPTS_DIR

MAX_LINE_S = 7.0  # Whisper segments run up to ~30 s; subtitles and the panel need short lines
MAX_LINE_CHARS = 84
SENTENCE_MIN_S = 2.5  # end a line at a full stop once it is at least this long
SUBTITLE_LINE_CHARS = 42  # Netflix's limit per subtitle row


@dataclass
class Segment:
    start: float  # seconds
    end: float
    text: str


def _join(words: list[Segment]) -> Segment:
    return Segment(words[0].start, words[-1].end, " ".join(w.text.strip() for w in words if w.text.strip()))


def group_words(words: list[Segment]) -> list[Segment]:
    """Group timed words into short lines, preferring to break after a sentence ends."""
    lines: list[Segment] = []
    current: list[Segment] = []
    for word in words:
        if current:
            chars = sum(len(w.text.strip()) + 1 for w in current) + len(word.text.strip())
            too_long = word.end - current[0].start > MAX_LINE_S or chars > MAX_LINE_CHARS
            sentence_end = (current[-1].text.rstrip().endswith((".", "?", "!"))
                            and current[-1].end - current[0].start >= SENTENCE_MIN_S)
            if too_long or sentence_end:
                lines.append(_join(current))
                current = []
        current.append(word)
    if current:
        lines.append(_join(current))
    return lines


def _is_long(segment: Segment) -> bool:
    return segment.end - segment.start > MAX_LINE_S or len(segment.text) > MAX_LINE_CHARS


def _estimated_words(segment: Segment) -> list[Segment]:
    """Word times spread over the segment by character position (when real word times are missing)."""
    tokens = segment.text.split()
    per_char = (segment.end - segment.start) / max(1, sum(len(t) + 1 for t in tokens))
    words, position = [], 0
    for token in tokens:
        start = segment.start + position * per_char
        position += len(token) + 1
        words.append(Segment(start, segment.start + position * per_char, token))
    return words


def split_segments(segments: list[Segment], words: list[Segment] | None = None) -> list[Segment]:
    """Break long segments into short lines, using Whisper's word timestamps when given."""
    result: list[Segment] = []
    for segment in segments:
        if not _is_long(segment):
            result.append(segment)
            continue
        inside = [w for w in words or [] if segment.start <= (w.start + w.end) / 2 < segment.end]
        tokens = segment.text.split()
        if inside and len(inside) == len(tokens):  # keep the segment's punctuation, use the word times
            inside = [Segment(w.start, w.end, token) for w, token in zip(inside, tokens)]
        result.extend(group_words(inside or _estimated_words(segment)))
    return result


def srt_time(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def subtitle_rows(text: str) -> str:
    """Break a long line into two balanced rows at the space nearest the middle."""
    if len(text) <= SUBTITLE_LINE_CHARS or " " not in text:
        return text
    middle = len(text) / 2
    cut = min((i for i, char in enumerate(text) if char == " "), key=lambda i: abs(i - middle))
    return f"{text[:cut]}\n{text[cut + 1:]}"


def to_srt(segments: list[Segment]) -> str:
    blocks = [
        f"{number}\n{srt_time(s.start)} --> {srt_time(s.end)}\n{subtitle_rows(s.text)}\n"
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
    segments = split_segments(segments)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    srt_path(video_id).write_text(to_srt(segments), encoding="utf-8")
    data = [[round(s.start, 2), round(s.end, 2), s.text] for s in segments]
    segments_path(video_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_transcript(video_id: int) -> list[Segment] | None:
    try:
        data = json.loads(segments_path(video_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    segments = [Segment(float(start), float(end), str(text)) for start, end, text in data]
    if any(_is_long(s) for s in segments):  # made before lines were split: upgrade the files once
        save_transcript(video_id, segments)
        segments = split_segments(segments)
    else:
        srt = to_srt(segments)
        try:
            current = srt_path(video_id).read_text(encoding="utf-8")
        except OSError:
            current = None
        if current != srt:  # older subtitle layout (or missing file): rewrite it
            srt_path(video_id).write_text(srt, encoding="utf-8")
    return segments


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
