"""AI chapters: split a lecture into titled topics (YouTube-style) using a Groq chat model."""
import json
import re
import threading

from PySide6.QtCore import QObject, Signal

from ..config import write_text_atomic
from . import Segment, groq_client
from .summary import PART_CHARS, Cancelled, ask

MARK_EVERY_MS = 30_000  # a [mm:ss] marker in the prompt text every ~30 s, so the model can cite start times
MIN_GAP_MS = 360_000  # chapters closer than 6 minutes are merged away

CHAPTER_PROMPT = (
    "Neeche ek lecture ke transcript ka hissa hai, beech-beech me [mm:ss] ya [h:mm:ss] time markers hain. "
    "Ise sirf bade topics (chapters) me baanto, jaise YouTube chapters: is hisse me 1-3 chapters, har chapter "
    "kam se kam ~6 minute ka. "
    "Har chapter ka start time sirf diye gaye markers me se lo, aur 3-7 words ka chhota title do "
    "(technical terms English me, baaki Hinglish Roman script). Sirf JSON array do, aur kuch nahi:\n"
    '[{{"time": "12:30", "title": "Neuron ka structure"}}]\n\nTranscript:\n{text}'
)
_TIME = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")


def _ms(text: str) -> int | None:
    match = _TIME.match(text.strip())
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000


def _stamp(ms: int) -> str:
    seconds = ms // 1000
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def marked_parts(segments: list[Segment], max_chars: int = PART_CHARS) -> list[tuple[int, int, str]]:
    """(start_ms, end_ms, text with [mm:ss] markers) pieces of the transcript."""
    parts: list[tuple[int, int, str]] = []
    pieces: list[str] = []
    size = start = end = 0
    last_mark = -MARK_EVERY_MS
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        ms = int(segment.start * 1000)
        if pieces and size + len(text) > max_chars:
            parts.append((start, end, " ".join(pieces)))
            pieces, size, last_mark = [], 0, -MARK_EVERY_MS
        if not pieces:
            start = ms
        if ms - last_mark >= MARK_EVERY_MS:
            text = f"[{_stamp(ms)}] {text}"
            last_mark = ms
        pieces.append(text)
        size += len(text) + 1
        end = int(segment.end * 1000)
    if pieces:
        parts.append((start, end, " ".join(pieces)))
    return parts


def parse_chapters(reply: str, start_ms: int, end_ms: int) -> list[dict]:
    begin, finish = reply.find("["), reply.rfind("]")
    if begin < 0 or finish <= begin:
        return []
    try:
        items = json.loads(reply[begin:finish + 1])
    except ValueError:
        return []
    chapters = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            ms = _ms(str(item.get("time", "")))
            title = " ".join(str(item.get("title", "")).split())
            if ms is not None and title and start_ms <= ms <= end_ms:
                chapters.append({"start_ms": ms, "title": title[:80]})
    return chapters


def tidy(chapters: list[dict]) -> list[dict]:
    """Sorted, at least MIN_GAP_MS apart, and the first one starts at 0:00."""
    result: list[dict] = []
    for chapter in sorted(chapters, key=lambda c: c["start_ms"]):
        if result and chapter["start_ms"] - result[-1]["start_ms"] < MIN_GAP_MS:
            continue
        result.append(dict(chapter))
    if result:
        result[0]["start_ms"] = 0
    return result


def make_chapters(api_key: str, segments: list[Segment], report=lambda _text: None,
                  cancel: threading.Event | None = None) -> list[dict]:
    cancel = cancel or threading.Event()
    parts = marked_parts(segments)
    if not parts:
        raise groq_client.GroqError("The transcript is empty.")
    found: list[dict] = []
    for index, (start_ms, end_ms, text) in enumerate(parts, 1):
        report(f"Making chapters... part {index} of {len(parts)}")
        found += parse_chapters(ask(api_key, CHAPTER_PROMPT.format(text=text), 1200, report, cancel), start_ms, end_ms)
    chapters = tidy(found)
    if not chapters:
        raise groq_client.GroqError("Groq did not return usable chapters. Please try again.")
    return chapters


# ---- storage (next to the transcript) ----
def _path(video_id: int):
    from . import TRANSCRIPTS_DIR  # read at call time so tests can point it elsewhere
    return TRANSCRIPTS_DIR / f"{video_id}.chapters.json"


def load_chapters(video_id: int) -> list[dict]:
    try:
        return json.loads(_path(video_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_chapters(video_id: int, chapters: list[dict]) -> None:
    write_text_atomic(_path(video_id), json.dumps(chapters, ensure_ascii=False))


class ChapterMaker(QObject):
    progress = Signal(int, str)
    finished = Signal(int, list)
    failed = Signal(int, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self.busy_video: int | None = None

    def start(self, video_id: int, segments: list[Segment]) -> None:
        self.busy_video = video_id
        threading.Thread(target=self._run, args=(video_id, segments), name="chapters", daemon=True).start()

    def shutdown(self) -> None:
        self._cancel.set()

    def _run(self, video_id: int, segments: list[Segment]) -> None:
        try:
            api_key = groq_client.load_settings()["api_key"]
            if not api_key:
                raise groq_client.InvalidKey("No Groq API key.")
            chapters = make_chapters(api_key, segments, lambda text: self.progress.emit(video_id, text), self._cancel)
            save_chapters(video_id, chapters)
        except Cancelled:
            self.busy_video = None
            return
        except groq_client.InvalidKey as exc:
            self.busy_video = None
            self.failed.emit(video_id, f"{exc} Check the key in Settings.")
            return
        except Exception as exc:
            self.busy_video = None
            self.failed.emit(video_id, f"Could not make chapters: {exc}")
            return
        self.busy_video = None
        self.finished.emit(video_id, chapters)
