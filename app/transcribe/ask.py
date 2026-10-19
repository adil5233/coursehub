"""Ask questions about a lecture (or a whole course): keyword retrieval over transcripts + Groq answer."""
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from . import Segment, groq_client
from .summary import LANGUAGE_RULE, Cancelled, ask

WINDOW_MS = 60_000  # transcript is cut into ~1 minute passages
NOW_BEFORE_MS, NOW_AFTER_MS = 90_000, 30_000  # "what was just said": the last 90 s and a little ahead
TOP_K = 6
_WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an the is are was were be been to of in on at for and or but if so it this that these those i you he she we "
    "they me my your our with as by from what which who how why when where do does did can could will would should "
    "not no yes there here then than also just about into okay ok right so hai hain ha ka ki ke ko me mein se aur "
    "ya bhi to toh kya kyun kaise kab kaha jo ye yeh wo woh is us un ek hum tum aap na nahi nahin ho hota hoti hote "
    "kar karo karte karna raha rahe rahi tha thi the ab jab tab phir par pe".split()
)

ELI10_RULE = (
    "Samjhane ka tareeka: jaise 10 saal ke bachche ko, ek chhota roz-marra ka example ke saath. Max 2 naye "
    "technical shabd, har naye shabd ke saath uska 1 line matlab."
)
ANSWER_PROMPT = (
    "Student ka sawaal: {question}\n{step}\n"
    "Neeche lecture transcript ke kuch hisse hain, har hisse ka label [C1], [C2]... hai. Sirf inhi hisson ke basis par "
    "jawab do. Har important baat ke baad us hisse ka label likho, jaise [C2]. Agar jawab in hisson me nahi hai to "
    "saaf likho: 'Is lecture me iska jawab nahi mila.' Jawab short aur clear rakho (max ~8 lines), zaroorat ho to "
    "'- ' bullets. Sawaal me 'ye' / 'isme' jaisa kuch ho to wo abhi chal rahe hisse ki baat hai (pehla hissa).\n"
    + ELI10_RULE + "\n\n" + LANGUAGE_RULE + "\n\nTranscript ke hisse:\n{context}"
)
EXPLAIN_PROMPT = (
    "Student video dekh raha tha aur abhi jo bataya gaya wo samajh nahi aaya.{question}\n{step}\n"
    "Neeche wahi hissa hai (label [C1]). Use bilkul simple tareeke se dobara samjhao: pehle 1 line me kya bola, "
    "phir ek example, aur aakhri line 'Ek line me: ...'. Baat ke baad [C1] likho. Max ~8 lines.\n"
    + ELI10_RULE + "\n\n" + LANGUAGE_RULE + "\n\nTranscript ke hisse:\n{context}"
)


@dataclass
class Passage:
    video_id: int
    start_ms: int
    text: str
    label: str = ""  # e.g. "Lecture 3" when asking a whole course
    end_ms: int = 0  # set for the current-moment passage


def tokens(text: str) -> list[str]:
    return [word for word in _WORD.findall(text.lower()) if word not in STOPWORDS and len(word) > 1]


def passages(video_id: int, segments: list[Segment], label: str = "", window_ms: int = WINDOW_MS) -> list[Passage]:
    result: list[Passage] = []
    current: list[str] = []
    start_ms = 0
    for segment in segments:
        segment_ms = int(segment.start * 1000)
        if current and segment_ms - start_ms >= window_ms:
            result.append(Passage(video_id, start_ms, " ".join(current), label))
            current = []
        if not current:
            start_ms = segment_ms
        current.append(segment.text.strip())
    if current:
        result.append(Passage(video_id, start_ms, " ".join(current), label))
    return result


def time_passage(video_id: int, segments: list[Segment], now_ms: int, before_ms: int = NOW_BEFORE_MS,
                 after_ms: int = NOW_AFTER_MS, label: str = "") -> Passage | None:
    """What was said around `now_ms` (the moment the learner is at), as one passage."""
    inside = [s for s in segments if now_ms - before_ms <= s.start * 1000 <= now_ms + after_ms and s.text.strip()]
    if not inside:
        return None
    return Passage(video_id, int(inside[0].start * 1000), " ".join(s.text.strip() for s in inside), label,
                   int(inside[-1].end * 1000))


def with_now(now: Passage | None, found: list[Passage]) -> list[Passage]:
    """The current moment first, then the search results that do not overlap it."""
    if now is None:
        return found
    def overlaps(p: Passage) -> bool:  # search passages cover about WINDOW_MS from their start
        return p.video_id == now.video_id and p.start_ms < now.end_ms and p.start_ms + WINDOW_MS > now.start_ms

    others = [p for p in found if not overlaps(p)]
    return [now] + others[:TOP_K - 1]


def retrieve(items: list[Passage], question: str, k: int = TOP_K) -> list[Passage]:
    """BM25 ranking; returns the best passages in timeline order."""
    query = set(tokens(question))
    if not query or not items:
        return []
    docs = [Counter(tokens(p.text)) for p in items]
    lengths = [sum(d.values()) for d in docs]
    average = sum(lengths) / len(lengths) or 1
    frequency = Counter(word for d in docs for word in set(d) if word in query)
    k1, b = 1.5, 0.75
    scores = []
    for index, (doc, length) in enumerate(zip(docs, lengths)):
        score = 0.0
        for word in query:
            tf = doc.get(word, 0)
            if tf:
                idf = math.log(1 + (len(docs) - frequency[word] + 0.5) / (frequency[word] + 0.5))
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / average))
        if score > 0:
            scores.append((score, index))
    best = sorted(scores, reverse=True)[:k]
    return [items[index] for _score, index in sorted(best, key=lambda pair: (items[pair[1]].video_id,
                                                                              items[pair[1]].start_ms))]


def answer(api_key: str, question: str, found: list[Passage], report=lambda _text: None,
           cancel: threading.Event | None = None, time_label=lambda ms: str(ms), explain: bool = False,
           step: str = "") -> str:
    """explain=True: "Abhi wala samjhao", re-explain the current moment (found[0]) instead of answering."""
    if not found:
        return "Is lecture me iska jawab nahi mila (transcript me in words se kuch match nahi hua)."
    context = "\n\n".join(
        f"[C{i}] ({(p.label + ', ') if p.label else ''}{time_label(p.start_ms)}): {p.text}"
        for i, p in enumerate(found, 1)
    )
    step_line = f"(Abhi ka step: {step})" if step else ""
    if explain:
        prompt = EXPLAIN_PROMPT.format(question=f" Student ne poocha: {question}" if question else "",
                                       step=step_line, context=context)
    else:
        prompt = ANSWER_PROMPT.format(question=question, step=step_line, context=context)
    report("Thinking...")
    return ask(api_key, prompt, 1500, report, cancel or threading.Event())


class LectureAsker(QObject):
    progress = Signal(str)
    finished = Signal(str, str, list)  # question, answer, passages used
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self.busy = False

    def start(self, question: str, items: list[Passage], time_label, now: Passage | None = None,
              explain: bool = False, step: str = "") -> None:
        """now: what was said at the current moment; explain=True re-explains only that."""
        self.busy = True
        self._cancel.clear()
        threading.Thread(target=self._run, args=(question, items, time_label, now, explain, step), name="ask",
                         daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, question: str, items: list[Passage], time_label, now, explain: bool, step: str) -> None:
        try:
            api_key = groq_client.load_settings()["api_key"]
            if not api_key:
                raise groq_client.InvalidKey("No Groq API key.")
            found = [now] if explain and now is not None else with_now(now, retrieve(items, question))
            reply = answer(api_key, question, found, self.progress.emit, self._cancel, time_label, explain, step)
        except Cancelled:
            self.busy = False
            return
        except groq_client.InvalidKey as exc:
            self.busy = False
            self.failed.emit(f"{exc} Check the key in Settings.")
            return
        except Exception as exc:
            self.busy = False
            self.failed.emit(f"Could not answer: {exc}")
            return
        self.busy = False
        self.finished.emit(question, reply, found)
