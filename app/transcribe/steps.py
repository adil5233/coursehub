"""Study steps: a lecture cut into 3-5 minute pieces, each with a one-line gist, new words and a check question."""
import json
import threading

from ..config import write_text_atomic
from .chapters import _ms
from .summary import ask

MIN_GAP_MS = 150_000  # steps closer than 2.5 minutes are merged away
MAX_NEW_WORDS = 2  # never more than two new words per step
CHECK_KINDS = ("recall", "predict")

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


def make_mantra(api_key: str, steps: list[dict], report=lambda _text: None,
                cancel: threading.Event | None = None) -> str:
    """One memorable line for the whole lecture, from the steps' one-line gists (a small, cheap call)."""
    report("Writing the mantra...")
    lines = "\n".join(f"- {s['title']}: {s['ek_line']}" for s in steps)
    return _text(ask(api_key, MANTRA_PROMPT.format(lines=lines), 300, report, cancel or threading.Event()),
                 160).strip("\"'")


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
    write_text_atomic(_path(video_id), json.dumps(data, ensure_ascii=False))
