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
TOP_K = 6
_WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an the is are was were be been to of in on at for and or but if so it this that these those i you he she we "
    "they me my your our with as by from what which who how why when where do does did can could will would should "
    "not no yes there here then than also just about into okay ok right so hai hain ha ka ki ke ko me mein se aur "
    "ya bhi to toh kya kyun kaise kab kaha jo ye yeh wo woh is us un ek hum tum aap na nahi nahin ho hota hoti hote "
    "kar karo karte karna raha rahe rahi tha thi the ab jab tab phir par pe".split()
)

ANSWER_PROMPT = (
    "Student ka sawaal: {question}\n\n"
    "Neeche lecture transcript ke kuch hisse hain, har hisse ka label [C1], [C2]... hai. Sirf inhi hisson ke basis par "
    "jawab do. Har important baat ke baad us hisse ka label likho, jaise [C2]. Agar jawab in hisson me nahi hai to "
    "saaf likho: 'Is lecture me iska jawab nahi mila.' Jawab short aur clear rakho (max ~8 lines), zaroorat ho to "
    "'- ' bullets.\n\n" + LANGUAGE_RULE + "\n\nTranscript ke hisse:\n{context}"
)


@dataclass
class Passage:
    video_id: int
    start_ms: int
    text: str
    label: str = ""  # e.g. "Lecture 3" when asking a whole course


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
           cancel: threading.Event | None = None, time_label=lambda ms: str(ms)) -> str:
    if not found:
        return "Is lecture me iska jawab nahi mila (transcript me in words se kuch match nahi hua)."
    context = "\n\n".join(
        f"[C{i}] ({(p.label + ', ') if p.label else ''}{time_label(p.start_ms)}): {p.text}"
        for i, p in enumerate(found, 1)
    )
    report("Thinking...")
    return ask(api_key, ANSWER_PROMPT.format(question=question, context=context), 1500, report,
               cancel or threading.Event())


class LectureAsker(QObject):
    progress = Signal(str)
    finished = Signal(str, str, list)  # question, answer, passages used
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self.busy = False

    def start(self, question: str, items: list[Passage], time_label) -> None:
        self.busy = True
        threading.Thread(target=self._run, args=(question, items, time_label), name="ask", daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, question: str, items: list[Passage], time_label) -> None:
        try:
            api_key = groq_client.load_settings()["api_key"]
            if not api_key:
                raise groq_client.InvalidKey("No Groq API key.")
            found = retrieve(items, question)
            reply = answer(api_key, question, found, self.progress.emit, self._cancel, time_label)
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
