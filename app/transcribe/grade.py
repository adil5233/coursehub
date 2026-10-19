"""Check an answer written (or spoken) in the learner's own words, and re-explain what keeps going wrong."""
import json
import threading
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from . import Segment, groq_client
from .summary import LANGUAGE_RULE, Cancelled, ask

CONTEXT_CHARS = 6000  # enough lecture text around the question for a fair check
PASS_SCORE = 6  # below this the answer counts as a mistake

GRADE_PROMPT = (
    "Ek student ne lecture ke sawaal ka jawab apne shabdon me diya hai (bol ke diya ho to transcription me "
    "chhoti galtiyan ho sakti hain, unhe ignore karo). Sirf lecture ke hisse aur sahi jawab ke basis par check "
    "karo, shabd nahi, samajh dekho. 0-10 score do: 10 = poori baat sahi, 5 = aadhi, 0 = galat ya khaali.\n"
    "Sirf JSON object do, aur kuch nahi:\n"
    '{{"score": 7, "sahi": "jo sahi bola (1 line)", "chhoota": "jo galat ya chhoot gaya (1 line, kuch nahi to \'\')", '
    '"better": "isi jawab ka behtar version, 1-2 line, student ke style me"}}\n\n' + LANGUAGE_RULE
    + "\n\nSawaal: {question}\nSahi jawab: {reference}\nLecture ka hissa:\n{context}\n\nStudent ka jawab:\n{answer}"
)
INTERVIEW_PROMPT = (
    "You are a friendly interviewer for an AI engineer role. The candidate answered in their own words "
    "(maybe spoken, so ignore small transcription errors). Check the content against the reference and the "
    "lecture, and also how clearly it is said in simple English. Score 0-10.\n"
    "Reply with only a JSON object:\n"
    '{{"score": 7, "sahi": "what was good (1 line)", "chhoota": "what was wrong or missing (1 line, or \'\')", '
    '"better": "a better 2-3 sentence answer in simple, short English sentences"}}\n\n'
    "Question: {question}\nReference answer: {reference}\nLecture part:\n{context}\n\nCandidate's answer:\n{answer}"
)
INTERVIEW_SYSTEM = "You coach a learner for AI engineer interviews. Always reply in simple English."
REEXPLAIN_PROMPT = (
    "Student ne ye sawaal {count} baar galat kiya hai. Pehle wala explanation kaam nahi aaya, isliye bilkul naye "
    "tareeke se samjhao, jaise 10 saal ke bachche ko:\n"
    "- ek roz-marra ki life ka example ya analogy\n"
    "- zaroorat ho to 3-6 line ka chhota code example (``` block me)\n"
    "- max 2 naye shabd, har ek ka 1 line matlab\n"
    "- aakhri line: 'Yaad rakhna: ...' ek line ka mantra\n"
    "Max ~12 lines. Sirf lecture ke content se, apni taraf se naye facts mat jodo.\n\n" + LANGUAGE_RULE
    + "\n\nSawaal: {question}\nSahi jawab: {correct}\nStudent ka galat jawab: {mine}\nLecture ka hissa:\n{context}"
)


@dataclass
class Grade:
    score: int  # 0..10
    sahi: str
    chhoota: str
    better: str

    @property
    def passed(self) -> bool:
        return self.score >= PASS_SCORE


def parse_grade(reply: str) -> Grade | None:
    begin, finish = reply.find("{"), reply.rfind("}")
    if begin < 0 or finish <= begin:
        return None
    try:
        data = json.loads(reply[begin:finish + 1])
        score = round(float(data["score"]))
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return Grade(max(0, min(10, score)), str(data.get("sahi") or "").strip(),
                 str(data.get("chhoota") or "").strip(), str(data.get("better") or "").strip())


def context_for(segments: list[Segment] | None, start_ms: int = 0, end_ms: float = float("inf"),
                max_chars: int = CONTEXT_CHARS) -> str:
    """Transcript text between two times (the whole lecture by default), cut to max_chars."""
    text = " ".join(s.text.strip() for s in segments or [] if start_ms <= s.start * 1000 < end_ms and s.text.strip())
    return text[:max_chars]


def grade(api_key: str, question: str, reference: str, answer: str, context: str = "", interview: bool = False,
          report=lambda _text: None, cancel: threading.Event | None = None) -> Grade:
    if not answer.strip():
        missing = "No answer given." if interview else "Kuch likha / bola hi nahi."
        return Grade(0, "", missing, reference)
    prompt = (INTERVIEW_PROMPT if interview else GRADE_PROMPT).format(
        question=question, reference=reference or "(not given, use the lecture)", context=context or "-",
        answer=answer.strip())
    report("Checking your answer...")
    extra = {"system": INTERVIEW_SYSTEM} if interview else {}  # the default system prompt asks for Hinglish
    result = parse_grade(ask(api_key, prompt, 1200, report, cancel or threading.Event(), **extra))
    if result is None:
        raise groq_client.GroqError("Groq sent an answer check that could not be read. Please try again.")
    return result


def reexplain(api_key: str, question: str, correct: str, mine: str, count: int, context: str = "",
              report=lambda _text: None, cancel: threading.Event | None = None) -> str:
    report("Naye tareeke se samjha raha hun...")
    prompt = REEXPLAIN_PROMPT.format(question=question, correct=correct or "-", mine=mine or "(khaali)",
                                     count=count, context=context or "-")
    return ask(api_key, prompt, 1500, report, cancel or threading.Event()).strip()


class BackgroundCall(QObject):
    """Runs one Groq helper on a thread with the saved API key; results come back as signals."""

    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self.busy = False

    def start(self, fn, *args, **kwargs) -> bool:
        if self.busy:
            return False
        self.busy = True
        self._cancel.clear()
        threading.Thread(target=self._run, args=(fn, args, kwargs), name="groq-call", daemon=True).start()
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, fn, args, kwargs) -> None:
        try:
            api_key = groq_client.load_settings()["api_key"]
            if not api_key:
                raise groq_client.InvalidKey("No Groq API key.")
            result = fn(api_key, *args, report=self.progress.emit, cancel=self._cancel, **kwargs)
        except Cancelled:
            self.busy = False
            return
        except groq_client.InvalidKey as exc:
            self.busy = False
            self.failed.emit(f"{exc} Check the key in Settings.")
            return
        except Exception as exc:
            self.busy = False
            self.failed.emit(str(exc) or type(exc).__name__)
            return
        self.busy = False
        self.finished.emit(result)


def speech_to_text(api_key: str, audio: bytes, filename: str, report=lambda _text: None,
                   cancel: threading.Event | None = None) -> str:
    """A recorded answer as plain text (Groq Whisper, same settings as lecture transcripts)."""
    report("Turning your voice into text...")
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    return " ".join(s.text.strip() for s in groq_client.transcribe(api_key, audio, filename) if s.text.strip())
