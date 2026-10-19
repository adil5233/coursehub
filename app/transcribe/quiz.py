"""Multiple-choice revision quiz for a lecture, written by a Groq chat model from its transcript."""
import json
import math
import re
import threading

from PySide6.QtCore import QObject, Signal

from . import Segment, groq_client
from .summary import LANGUAGE_RULE, Cancelled, ask, timed_parts

QUIZ_SIZE = 8
_LETTER = re.compile(r"^\(?[A-Da-d1-4][.):]\s+")  # "A) ", "b. ", "(C) ", "1: "

QUIZ_PROMPT = (
    "Neeche ek lecture ke transcript ka hissa hai. Isse {count} multiple-choice sawaal banao jo samajh check karein: "
    "concepts, definitions, 'kyun' aur 'kaise' (trivia ya teacher ki personal baatein nahi).\n"
    "Har sawaal ke 4 options, sirf ek sahi. Galat options bhi believable hon. Options ke aage A/B/C/D mat likho "
    "(options shuffle honge), aur explanation me option letter ka zikr mat karo, seedha sahi baat samjhao.\n"
    "Sirf JSON array do, aur kuch nahi:\n"
    '[{{"q": "sawaal", "options": ["A", "B", "C", "D"], "answer": 0, "explain": "sahi jawab kyun sahi hai"}}]\n'
    "\"answer\" sahi option ka index (0-3) hai.\n\n" + LANGUAGE_RULE + "\n\nTranscript:\n{text}"
)


def parse_questions(reply: str, time_ms: int) -> list[dict]:
    """Valid questions from the model's JSON reply (anything malformed is skipped)."""
    start, end = reply.find("["), reply.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        items = json.loads(reply[start:end + 1])
    except ValueError:
        return []
    questions = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        options = item.get("options")
        answer = item.get("answer")
        question = str(item.get("q", "")).strip()
        if (question and isinstance(options, list) and len(options) == 4 and isinstance(answer, int)
                and 0 <= answer < 4 and all(str(o).strip() for o in options)):
            questions.append({"q": question, "options": [_LETTER.sub("", str(o).strip()) for o in options],
                              "answer": answer,
                              "explain": str(item.get("explain", "")).strip(), "time_ms": time_ms})
    return questions


def make_quiz(api_key: str, segments: list[Segment], report=lambda _text: None,
              cancel: threading.Event | None = None, size: int = QUIZ_SIZE) -> list[dict]:
    """About `size` questions spread over the whole lecture (each part of the transcript gets its share)."""
    cancel = cancel or threading.Event()
    parts = timed_parts(segments)
    if not parts:
        raise groq_client.GroqError("The transcript is empty.")
    per_part = max(1, math.ceil(size / len(parts)))
    questions: list[dict] = []
    for index, (start_ms, text) in enumerate(parts, 1):
        report(f"Writing questions... part {index} of {len(parts)}")
        reply = ask(api_key, QUIZ_PROMPT.format(count=per_part, text=text), 1500 + 400 * per_part, report, cancel)
        questions.extend(parse_questions(reply, start_ms)[:per_part])
    if not questions:
        raise groq_client.GroqError("Groq did not return usable questions. Please try again.")
    return questions


class QuizMaker(QObject):
    """Builds one quiz on a background thread."""

    progress = Signal(str)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()

    def start(self, segments: list[Segment]) -> None:
        threading.Thread(target=self._run, args=(segments,), name="quiz", daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, segments: list[Segment]) -> None:
        try:
            api_key = groq_client.load_settings()["api_key"]
            if not api_key:
                raise groq_client.InvalidKey("No Groq API key.")
            questions = make_quiz(api_key, segments, self.progress.emit, self._cancel)
        except Cancelled:
            return
        except groq_client.InvalidKey as exc:
            self.failed.emit(f"{exc} Check the key in Settings.")
            return
        except Exception as exc:
            self.failed.emit(f"Could not make the quiz: {exc}")
            return
        self.finished.emit(questions)
