"""Study pack: steps, chapters and quiz questions from ONE read of the transcript.

Each feature used to send the whole transcript on its own (steps, chapters, MCQ, own-words questions), so a long
lecture was read four times and the free daily token limit ran out quickly. Here every transcript part goes to the
model once and comes back with everything; the existing parsers check each piece.
"""
import json
import math
import threading

from PySide6.QtCore import QObject, Signal

from . import Segment, groq_client
from .chapters import load_chapters, marked_parts, parse_chapters, save_chapters, tidy
from .quiz import OPEN_SIZE, QUIZ_SIZE, _spread, parse_open, parse_questions
from .steps import load_steps, make_mantra, parse_steps, save_steps, tidy_steps
from .summary import LANGUAGE_RULE, Cancelled, ask

PACK_PROMPT = (
    "Neeche ek lecture ke transcript ka hissa hai, beech-beech me [mm:ss] ya [h:mm:ss] time markers hain. "
    "Isi ek hisse se poora study pack banao. Sirf JSON object do, aur kuch nahi:\n"
    '{{"steps": [{{"time": "3:20", "title": "...", "ek_line": "...", '
    '"naye_shabd": [{{"word": "...", "meaning": "..."}}], "check": {{"kind": "recall", "q": "...", "a": "..."}}}}], '
    '"chapters": [{{"time": "0:00", "title": "..."}}], '
    '"mcq": [{{"q": "...", "options": ["...", "...", "...", "..."], "answer": 0, "explain": "..."}}], '
    '"open": [{{"q": "...", "a": "..."}}], "interview": [{{"q": "...", "a": "..."}}]}}\n\n'
    "Rules:\n"
    "- steps: 3-5 minute ke chhote study steps, har step ek concept. time = step ka start (sirf diye gaye "
    "markers me se). title 3-7 words. ek_line = step ki baat ek line me, jaise dost ko samjha rahe ho. "
    "naye_shabd = max 2 naye technical words aur unka chhota simple matlab ([] agar koi nahi). check = ek sawaal: "
    "kind \"predict\" tabhi jab is hisse me code chala ya output dikhaya gaya ho (chalane se pehle batao kya hoga), "
    "warna \"recall\" (apne shabdon me samjhao); a = chhota sahi jawab\n"
    "- chapters: is hisse ke 1-3 bade topics (YouTube chapters jaise), har ek kam se kam ~6 minute, time markers "
    "me se, title 3-7 words\n"
    "- mcq: {mcq} multiple-choice sawaal jo samajh check karein (concepts, 'kyun', 'kaise'; trivia nahi). 4 "
    "believable options, sirf ek sahi, options ke aage A/B/C/D mat likho, explain me option letter ka zikr mat karo. "
    "answer = sahi option ka index (0-3)\n"
    "- open: {open} sawaal jinka jawab student apne shabdon me de ('kyun', 'kaise', 'farq kya hai'), jawab 1-3 line\n"
    "- interview: {open} AI engineer interview sawaal is topic pe; sawaal aur jawab dono simple English me, "
    "jawab 2-3 chhote sentences\n"
    "Technical terms English me rakho. Sirf transcript ki baatein, apni taraf se facts mat jodo.\n\n"
    + LANGUAGE_RULE + "\n\nTranscript:\n{text}"
)


def _object(reply: str) -> dict:
    begin, finish = reply.find("{"), reply.rfind("}")
    if begin < 0 or finish <= begin:
        return {}
    try:
        data = json.loads(reply[begin:finish + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def parse_pack(reply: str, start_ms: int, end_ms: int) -> dict:
    """One part's reply checked piece by piece with the same rules as the single-feature parsers."""
    data = _object(reply)

    def as_json(key: str) -> str:
        return json.dumps(data.get(key) if isinstance(data.get(key), list) else [])

    return {
        "steps": parse_steps(as_json("steps"), start_ms, end_ms),
        "chapters": parse_chapters(as_json("chapters"), start_ms, end_ms),
        "quiz": parse_questions(as_json("mcq"), start_ms),
        "open": parse_open(json.dumps({"open": data.get("open"), "interview": data.get("interview")}), start_ms),
    }


def make_pack(api_key: str, segments: list[Segment], report=lambda _text: None,
              cancel: threading.Event | None = None) -> dict:
    """{"steps", "mantra", "chapters", "quiz", "open": {"open", "interview"}} for the whole lecture."""
    cancel = cancel or threading.Event()
    parts = marked_parts(segments)
    if not parts:
        raise groq_client.GroqError("The transcript is empty.")
    mcq_per = max(1, math.ceil(QUIZ_SIZE / len(parts)))
    open_per = max(1, math.ceil(OPEN_SIZE / len(parts)))
    steps, chapters, quiz = [], [], []
    opened: dict[str, list] = {"open": [], "interview": []}
    for index, (start_ms, end_ms, text) in enumerate(parts, 1):
        report(f"Making the study pack... part {index} of {len(parts)}")
        prompt = PACK_PROMPT.format(mcq=mcq_per, open=open_per, text=text)
        part = parse_pack(ask(api_key, prompt, 4000 + 400 * mcq_per, report, cancel), start_ms, end_ms)
        steps += part["steps"]
        chapters += part["chapters"]
        quiz += part["quiz"][:mcq_per]
        for kind in opened:
            opened[kind] += part["open"][kind][:open_per]
    steps = tidy_steps(steps)
    if not steps:
        raise groq_client.GroqError("Groq did not return a usable study pack. Please try again.")
    return {
        "steps": steps,
        "mantra": make_mantra(api_key, steps, report, cancel),
        "chapters": tidy(chapters),
        "quiz": quiz,
        "open": {kind: _spread(items, OPEN_SIZE) for kind, items in opened.items()},
    }


def save_pack(db, video_id: int, pack: dict, replace: bool = True) -> None:
    """Store every piece (main thread: sqlite). replace=False only fills in what the lecture does not have yet."""
    if pack["steps"] and (replace or not load_steps(video_id)["steps"]):
        save_steps(video_id, {"steps": pack["steps"], "mantra": pack["mantra"]})
        db.clear_step_progress(video_id)  # new steps: the old step indexes no longer match
    if pack["chapters"] and (replace or not load_chapters(video_id)):
        save_chapters(video_id, pack["chapters"])
    if pack["quiz"] and (replace or not db.get_quiz(video_id)):
        db.save_quiz(video_id, pack["quiz"])
    opened = pack["open"]
    if (opened["open"] or opened["interview"]) and (replace or not db.get_open_quiz(video_id)):
        db.save_open_quiz(video_id, opened)


class PackMaker(QObject):
    """Builds one lecture's study pack on a background thread; the caller saves it with save_pack()."""

    progress = Signal(int, str)
    finished = Signal(int, dict)
    failed = Signal(int, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self.busy_video: int | None = None

    def start(self, video_id: int, segments: list[Segment]) -> None:
        self.busy_video = video_id
        threading.Thread(target=self._run, args=(video_id, segments), name="study-pack", daemon=True).start()

    def shutdown(self) -> None:
        self._cancel.set()

    def _run(self, video_id: int, segments: list[Segment]) -> None:
        try:
            api_key = groq_client.load_settings()["api_key"]
            if not api_key:
                raise groq_client.InvalidKey("No Groq API key.")
            pack = make_pack(api_key, segments, lambda text: self.progress.emit(video_id, text), self._cancel)
        except Cancelled:
            self.busy_video = None
            return
        except groq_client.InvalidKey as exc:
            self.busy_video = None
            self.failed.emit(video_id, f"{exc} Check the key in Settings.")
            return
        except Exception as exc:
            self.busy_video = None
            self.failed.emit(video_id, f"Could not make the study pack: {exc}")
            return
        self.busy_video = None
        self.finished.emit(video_id, pack)
