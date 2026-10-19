"""Search every note and transcript in the library."""
from dataclasses import dataclass
from pathlib import Path

from . import transcribe

MAX_HITS = 300


@dataclass
class Hit:
    kind: str  # "note" | "transcript"
    course_id: int
    course_name: str
    video_id: int
    video_title: str
    lecture_number: int
    time_ms: int
    text: str


class TranscriptCache:
    """Transcript lines per lecture, re-read only when the file changes."""

    def __init__(self) -> None:
        self._cache: dict[int, tuple[float, list[tuple[int, str, str]]]] = {}

    def lines(self, video_id: int, path: Path) -> list[tuple[int, str, str]]:
        """[(start_ms, text, lowercase text)]"""
        mtime = path.stat().st_mtime
        cached = self._cache.get(video_id)
        if cached is None or cached[0] != mtime:
            segments = transcribe.load_transcript(video_id) or []
            cached = (mtime, [(int(s.start * 1000), s.text, s.text.lower()) for s in segments])
            self._cache[video_id] = cached
        return cached[1]


def _words(query: str) -> list[str]:
    return [word for word in query.lower().split() if word]


def search(db, query: str, cache: TranscriptCache | None = None, limit: int = MAX_HITS) -> list[Hit]:
    """Notes first, then transcript lines; a result must contain every word of the query."""
    words = _words(query)
    if not words or len("".join(words)) < 2:
        return []
    cache = cache or TranscriptCache()
    lectures = db.lecture_index()
    hits: list[Hit] = []

    def hit(kind: str, video_id: int, time_ms: int, text: str) -> None:
        course, video, number = lectures[video_id]
        hits.append(Hit(kind, course.id, course.name, video.id, video.title, number, time_ms, text))

    for note in db.all_notes():
        if note.video_id in lectures and all(word in note.text.lower() for word in words):
            hit("note", note.video_id, note.timestamp_ms, note.text)
    hits.sort(key=lambda h: (h.course_id, h.lecture_number, h.time_ms))
    if transcribe.TRANSCRIPTS_DIR.is_dir():
        for video_id, path in _ordered_transcripts(lectures):
            for start_ms, text, lowered in cache.lines(video_id, path):
                if all(word in lowered for word in words):
                    hit("transcript", video_id, start_ms, text)
                    if len(hits) >= limit:
                        return hits
    return hits[:limit]


def _ordered_transcripts(lectures) -> list[tuple[int, Path]]:
    """Transcript files of known lectures, in course then lecture order."""
    found = []
    for path in transcribe.TRANSCRIPTS_DIR.glob("*.json"):
        if path.stem.isdigit() and int(path.stem) in lectures:
            course, _video, number = lectures[int(path.stem)]
            found.append((course.id, number, int(path.stem), path))
    return [(video_id, path) for _course, _number, video_id, path in sorted(found)]
