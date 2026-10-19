"""AI summary note for a lecture: transcript -> Groq chat model (map-reduce when the lecture is long)."""
import math
import threading

import requests
from PySide6.QtCore import QObject, Signal

from . import Segment, groq_client

PART_CHARS = 14_000  # ~4K tokens: one part plus its reply stays under the free 12K tokens/minute
MAX_WAIT_S = 120  # longer waits mean the daily limit is used up: fail with a clear message instead
NETWORK_RETRIES = 3

SYSTEM_PROMPT = (
    "Tum ek teaching assistant ho jo programming aur AI lectures ke study notes banata hai. "
    "Hamesha Hinglish me likho: Hindi aur English mix, sirf Roman script (Devanagari bilkul nahi), "
    "jaise Indian students chat me likhte hain. Technical terms English me hi rakho. "
    "Sirf wahi likho jo transcript me hai, apni taraf se facts, examples ya code mat jodo."
)
LANGUAGE_RULE = (
    "Language: poora note Hinglish (Roman script) me, pure English me nahi. Example bullet style:\n"
    "- Neural network me har neuron inputs ko weights se multiply karke bias add karta hai.\n"
    "- Training ka matlab hai weights aur bias ko adjust karna taaki loss kam ho."
)
NOTE_FORMAT = (
    "Format (plain text):\n"
    "- Pehli line: lecture ka chhota title, bina kisi symbol ke\n"
    "- Phir 'Key points:' aur uske neeche 8-15 bullets, har bullet '- ' se shuru\n"
    "- Important definitions / terms bhi bullets me, short aur clear\n"
    "- ``` code block SIRF tab do jab lecture me sach me code likha ya padha gaya ho; warna koi code nahi\n"
    "- Markdown headings (#), bold (**) ya tables mat use karo\n\n" + LANGUAGE_RULE
)
SINGLE_PROMPT = "Neeche ek lecture ka transcript hai. Iska ek summary note banao.\n\n{format}\n\nTranscript:\n{text}"
PART_PROMPT = (
    "Neeche ek lambe lecture ke transcript ka hissa {index}/{total} hai. Is hisse ke important points, "
    "definitions aur (agar sach me bataya gaya ho to) code ko short '- ' bullets me likho "
    "(max 12 bullets, koi title nahi).\n\n" + LANGUAGE_RULE + "\n\nTranscript:\n{text}"
)
COMBINE_PROMPT = (
    "Neeche ek lecture ke alag-alag hisson ke notes hain, order me. Inhe jod kar poore lecture ka ek summary note "
    "banao; repeat points hata do.\n\n{format}\n\nHisson ke notes:\n{text}"
)


class Cancelled(Exception):
    pass


def transcript_parts(segments: list[Segment], max_chars: int = PART_CHARS) -> list[str]:
    """Transcript text in consecutive pieces of at most ~max_chars (timestamps left out to save tokens)."""
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        if current and size + len(text) + 1 > max_chars:
            parts.append(" ".join(current))
            current, size = [], 0
        current.append(text)
        size += len(text) + 1
    if current:
        parts.append(" ".join(current))
    return parts


def _ask(api_key: str, prompt: str, max_tokens: int, report, cancel: threading.Event) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    failures = 0
    while True:
        if cancel.is_set():
            raise Cancelled()
        try:
            return groq_client.chat(api_key, messages, max_tokens=max_tokens)
        except groq_client.RateLimited as exc:
            if exc.retry_after > MAX_WAIT_S:
                minutes = math.ceil(exc.retry_after / 60)
                raise groq_client.GroqError(
                    f"Groq's free limit for today is used up. Try again in about {minutes} min.") from None
            report(f"Waiting {math.ceil(exc.retry_after)} s for the Groq free limit...")
            if cancel.wait(max(1.0, exc.retry_after)):
                raise Cancelled() from None
        except requests.RequestException:
            failures += 1
            if failures > NETWORK_RETRIES:
                raise
            if cancel.wait(5 * failures):
                raise Cancelled() from None


def _clean(note: str) -> str:
    """Notes are plain text: drop markdown bold the model sometimes adds anyway (code blocks are left alone)."""
    lines, in_code = [], False
    for line in note.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
        lines.append(line if in_code else line.replace("**", "").rstrip())
    return "\n".join(lines).strip()


def summarize(api_key: str, segments: list[Segment], report=lambda _text: None,
              cancel: threading.Event | None = None) -> str:
    """One Hinglish summary note for the whole lecture."""
    cancel = cancel or threading.Event()
    parts = transcript_parts(segments, PART_CHARS)
    if not parts:
        raise groq_client.GroqError("The transcript is empty.")
    if len(parts) == 1:
        report("Writing the summary...")
        return _clean(_ask(api_key, SINGLE_PROMPT.format(format=NOTE_FORMAT, text=parts[0]), 2000, report, cancel))
    part_notes = []
    for index, part in enumerate(parts, 1):
        report(f"Reading part {index} of {len(parts)}...")
        prompt = PART_PROMPT.format(index=index, total=len(parts), text=part)
        part_notes.append(_ask(api_key, prompt, 1200, report, cancel))
    report("Writing the summary...")
    joined = "\n\n".join(f"Hissa {i}:\n{notes}" for i, notes in enumerate(part_notes, 1))
    return _clean(_ask(api_key, COMBINE_PROMPT.format(format=NOTE_FORMAT, text=joined), 2000, report, cancel))


class NoteSummarizer(QObject):
    """Runs one summary at a time on a background thread; the caller saves the note (main thread / sqlite)."""

    progress = Signal(int, str)  # video_id, status text
    finished = Signal(int, str)  # video_id, note text
    failed = Signal(int, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._busy: int | None = None
        self.status = ""
        self._cancel = threading.Event()

    def busy_video(self) -> int | None:
        with self._lock:
            return self._busy

    def start(self, video_id: int, segments: list[Segment]) -> bool:
        with self._lock:
            if self._busy is not None:
                return False
            self._busy = video_id
        self.status = "Starting..."
        threading.Thread(target=self._run, args=(video_id, segments), name="ai-summary", daemon=True).start()
        return True

    def shutdown(self) -> None:
        self._cancel.set()

    def _report(self, video_id: int, text: str) -> None:
        self.status = text
        self.progress.emit(video_id, text)

    def _run(self, video_id: int, segments: list[Segment]) -> None:
        result, error = None, None
        try:
            api_key = groq_client.load_settings()["api_key"]
            if not api_key:
                raise groq_client.InvalidKey("No Groq API key.")
            result = summarize(api_key, segments, lambda text: self._report(video_id, text), self._cancel)
        except Cancelled:
            pass
        except groq_client.InvalidKey as exc:
            error = f"{exc} Check the key in Settings."
        except Exception as exc:
            error = f"AI summary failed: {exc}"
        with self._lock:
            self._busy = None
        self.status = ""
        if result is not None:
            self.finished.emit(video_id, result)
        elif error is not None:
            self.failed.emit(video_id, error)
