"""Study steps: a lecture cut into 3-5 minute pieces, each with a one-line gist, new words and a check question."""
import json
import threading

from PySide6.QtCore import QObject, Signal

from . import Segment, groq_client
from .chapters import _ms, marked_parts
from .summary import LANGUAGE_RULE, Cancelled, ask

MIN_GAP_MS = 150_000  # steps closer than 2.5 minutes are merged away
MAX_NEW_WORDS = 2  # never more than two new words per step
CHECK_KINDS = ("recall", "predict")

STEP_PROMPT = (
    "Neeche ek lecture ke transcript ka hissa hai, beech-beech me [mm:ss] ya [h:mm:ss] time markers hain. "
    "Ise chhote study steps me baanto: har step 3-5 minute ka ek hi concept. Har step ke liye:\n"
    "- time: step ka start, sirf diye gaye markers me se\n"
    "- title: 3-7 words (technical terms English me)\n"
    "- ek_line: is step ki baat ek line me, jaise dost ko samjha rahe ho\n"
    "- naye_shabd: is step ke max 2 naye technical words, har ek ka ek chhota simple matlab "
    "(koi naya shabd nahi to [])\n"
    "- check: ek sawaal jo samajh check kare. kind \"predict\" tabhi jab is hisse me code chala ya output dikhaya "
    "gaya ho (sawaal: chalane se pehle batao kya hoga); warna kind \"recall\" (apne shabdon me samjhao). "
    "a = chhota sahi jawab\n"
    "Sirf JSON array do, aur kuch nahi:\n"
    '[{{"time": "3:20", "title": "Weights ka role", "ek_line": "...", '
    '"naye_shabd": [{{"word": "bias", "meaning": "..."}}], '
    '"check": {{"kind": "recall", "q": "...", "a": "..."}}}}]\n\n' + LANGUAGE_RULE + "\n\nTranscript:\n{text}"
)
MANTRA_PROMPT = (
    "Neeche ek lecture ke steps ki ek-ek line hai. Poore lecture ka ek yaad rehne wala mantra likho: "
    "sirf ek line, max 15 words, Hinglish Roman script, koi quotes ya symbol nahi.\n\n{lines}"
)


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _words(value) -> list[dict]:
    words = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict) and (word := _text(item.get("word"), 40)):
            words.append({"word": word, "meaning": _text(item.get("meaning"), 160)})
    return words[:MAX_NEW_WORDS]


def _check(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    question, answer = _text(value.get("q"), 300), _text(value.get("a"), 400)
    if not question:
        return None
    kind = value.get("kind") if value.get("kind") in CHECK_KINDS else "recall"
    return {"kind": kind, "q": question, "a": answer}


def parse_steps(reply: str, start_ms: int, end_ms: int) -> list[dict]:
    """Valid steps from the model's JSON reply (anything malformed or outside the part is skipped)."""
    begin, finish = reply.find("["), reply.rfind("]")
    if begin < 0 or finish <= begin:
        return []
    try:
        items = json.loads(reply[begin:finish + 1])
    except ValueError:
        return []
    steps = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        ms = _ms(str(item.get("time", "")))
        title = _text(item.get("title"), 80)
        if ms is None or not title or not start_ms <= ms <= end_ms:
            continue
        steps.append({"start_ms": ms, "title": title, "ek_line": _text(item.get("ek_line"), 300),
                      "words": _words(item.get("naye_shabd")), "check": _check(item.get("check"))})
    return steps


def tidy_steps(steps: list[dict]) -> list[dict]:
    """Sorted, at least MIN_GAP_MS apart, and the first one starts at 0:00."""
    result: list[dict] = []
    for step in sorted(steps, key=lambda s: s["start_ms"]):
        if result and step["start_ms"] - result[-1]["start_ms"] < MIN_GAP_MS:
            continue
        result.append(dict(step))
    if result:
        result[0]["start_ms"] = 0
    return result


def make_steps(api_key: str, segments: list[Segment], report=lambda _text: None,
               cancel: threading.Event | None = None) -> dict:
    """{"steps": [...], "mantra": "..."} for the whole lecture."""
    cancel = cancel or threading.Event()
    parts = marked_parts(segments)
    if not parts:
        raise groq_client.GroqError("The transcript is empty.")
    found: list[dict] = []
    for index, (start_ms, end_ms, text) in enumerate(parts, 1):
        report(f"Making steps... part {index} of {len(parts)}")
        found += parse_steps(ask(api_key, STEP_PROMPT.format(text=text), 3000, report, cancel), start_ms, end_ms)
    steps = tidy_steps(found)
    if not steps:
        raise groq_client.GroqError("Groq did not return usable steps. Please try again.")
    report("Writing the mantra...")
    lines = "\n".join(f"- {s['title']}: {s['ek_line']}" for s in steps)
    mantra = _text(ask(api_key, MANTRA_PROMPT.format(lines=lines), 300, report, cancel), 160).strip("\"'")
    return {"steps": steps, "mantra": mantra}


def step_index_at(steps: list[dict], ms: int) -> int:
    """Index of the step playing at `ms` (-1 before the first one)."""
    index = -1
    for position, step in enumerate(steps):
        if step["start_ms"] > ms:
            break
        index = position
    return index


# ---- storage (next to the transcript) ----
def _path(video_id: int):
    from . import TRANSCRIPTS_DIR  # read at call time so tests can point it elsewhere
    return TRANSCRIPTS_DIR / f"{video_id}.steps.json"


def load_steps(video_id: int) -> dict:
    try:
        data = json.loads(_path(video_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"steps": [], "mantra": ""}
    return {"steps": list(data.get("steps") or []), "mantra": str(data.get("mantra") or "")}


def save_steps(video_id: int, data: dict) -> None:
    path = _path(video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


class StepMaker(QObject):
    progress = Signal(int, str)
    finished = Signal(int, dict)
    failed = Signal(int, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self.busy_video: int | None = None

    def start(self, video_id: int, segments: list[Segment]) -> None:
        self.busy_video = video_id
        threading.Thread(target=self._run, args=(video_id, segments), name="steps", daemon=True).start()

    def shutdown(self) -> None:
        self._cancel.set()

    def _run(self, video_id: int, segments: list[Segment]) -> None:
        try:
            api_key = groq_client.load_settings()["api_key"]
            if not api_key:
                raise groq_client.InvalidKey("No Groq API key.")
            data = make_steps(api_key, segments, lambda text: self.progress.emit(video_id, text), self._cancel)
            save_steps(video_id, data)
        except Cancelled:
            self.busy_video = None
            return
        except groq_client.InvalidKey as exc:
            self.busy_video = None
            self.failed.emit(video_id, f"{exc} Check the key in Settings.")
            return
        except Exception as exc:
            self.busy_video = None
            self.failed.emit(video_id, f"Could not make steps: {exc}")
            return
        self.busy_video = None
        self.finished.emit(video_id, data)
