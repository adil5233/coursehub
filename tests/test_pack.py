import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import app.transcribe as transcribe  # noqa: E402
from app.transcribe import Segment, chapters, groq_client, pack, steps  # noqa: E402
from tests.test_quiz import make_db  # noqa: E402
from tests.test_steps import STEPS  # noqa: E402


def part_reply(minute: int, title: str) -> str:
    return "Here you go: " + json.dumps({
        "steps": [{"time": f"{minute}:00", "title": title, "ek_line": f"{title} ki baat",
                   "naye_shabd": [{"word": "a", "meaning": "1"}, {"word": "b", "meaning": "2"},
                                  {"word": "c", "meaning": "3"}],
                   "check": {"kind": "recall", "q": f"{title} kya hai?", "a": "jawab"}},
                  {"time": "bad", "title": "skipped"}],
        "chapters": [{"time": f"{minute}:00", "title": f"Chapter {title}"}],
        "mcq": [{"q": f"{title} MCQ {i}", "options": ["w", "x", "y", "z"], "answer": 1, "explain": "e"}
                for i in range(6)] + [{"q": "bad", "options": ["only one"], "answer": 0}],
        "open": [{"q": f"{title} kyun?", "a": "kyunki"}],
        "interview": [{"q": f"What is {title}?", "a": "It is."}, {"q": ""}],
    })


def test_parse_pack_uses_the_feature_parsers():
    part = pack.parse_pack(part_reply(1, "Neuron"), 0, 600_000)
    assert [s["title"] for s in part["steps"]] == ["Neuron"] and len(part["steps"][0]["words"]) == 2
    assert part["chapters"] == [{"start_ms": 60_000, "title": "Chapter Neuron"}]
    assert len(part["quiz"]) == 6 and part["quiz"][0]["time_ms"] == 0
    assert part["open"]["open"] == [{"q": "Neuron kyun?", "a": "kyunki", "time_ms": 0}]
    assert [q["q"] for q in part["open"]["interview"]] == ["What is Neuron?"]
    empty = pack.parse_pack("sorry, no json", 0, 1)
    assert empty == {"steps": [], "chapters": [], "quiz": [], "open": {"open": [], "interview": []}}
    odd = pack.parse_pack('{"steps": "not a list", "mcq": null}', 0, 1)
    assert odd["steps"] == [] and odd["quiz"] == []


def test_make_pack_reads_each_part_once(monkeypatch):
    parts = [(0, 599_000, "a"), (600_000, 1_199_000, "b")]
    monkeypatch.setattr(pack, "marked_parts", lambda segments: parts)
    replies = iter([part_reply(1, "Neuron"), part_reply(12, "Training"), '"Seekho, phir bolo"'])
    prompts = []

    def fake_ask(_key, prompt, max_tokens, *_args, **_kwargs):
        prompts.append((prompt, max_tokens))
        return next(replies)

    monkeypatch.setattr(pack, "ask", fake_ask)
    monkeypatch.setattr(steps, "ask", fake_ask)
    statuses = []
    result = pack.make_pack("k", [Segment(0, 1, "x")], statuses.append)
    assert len(prompts) == 3  # one call per transcript part, then the small mantra call
    assert "4 multiple-choice" in prompts[0][0] and "2 sawaal" in prompts[0][0] and prompts[0][0].endswith("a")
    assert [s["title"] for s in result["steps"]] == ["Neuron", "Training"] and result["steps"][0]["start_ms"] == 0
    assert [c["title"] for c in result["chapters"]] == ["Chapter Neuron", "Chapter Training"]
    assert len(result["quiz"]) == 8  # 4 per part
    assert [q["q"] for q in result["open"]["open"]] == ["Neuron kyun?", "Training kyun?"]
    assert result["mantra"] == "Seekho, phir bolo"
    assert statuses[0] == "Making the study pack... part 1 of 2" and statuses[-1] == "Writing the mantra..."


def test_make_pack_without_steps_fails(monkeypatch):
    monkeypatch.setattr(pack, "marked_parts", lambda segments: [(0, 10_000, "a")])
    monkeypatch.setattr(pack, "ask", lambda *a, **k: "{}")
    with pytest.raises(groq_client.GroqError, match="study pack"):
        pack.make_pack("k", [Segment(0, 1, "x")])
    monkeypatch.setattr(pack, "marked_parts", chapters.marked_parts)
    with pytest.raises(groq_client.GroqError, match="empty"):
        pack.make_pack("k", [])


PACK = {"steps": STEPS, "mantra": "m", "chapters": [{"start_ms": 0, "title": "C"}],
        "quiz": [{"q": "M?", "options": ["a", "b", "c", "d"], "answer": 0, "explain": "", "time_ms": 0}],
        "open": {"open": [{"q": "O?", "a": "", "time_ms": 0}], "interview": []}}


def test_save_pack_replace_and_fill_in(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    db, _course, (ann, rnn) = make_db(tmp_path)
    db.set_step_progress(ann.id, 0, "done")
    pack.save_pack(db, ann.id, PACK)
    assert steps.load_steps(ann.id) == {"steps": STEPS, "mantra": "m"} and db.step_progress(ann.id) == {}
    assert chapters.load_chapters(ann.id) == PACK["chapters"] and db.get_quiz(ann.id) == PACK["quiz"]
    assert db.get_open_quiz(ann.id) == PACK["open"]

    db.set_step_progress(ann.id, 1, "partial")
    newer = dict(PACK, steps=STEPS[:1], mantra="new", quiz=[dict(PACK["quiz"][0], q="New?")])
    pack.save_pack(db, ann.id, newer, replace=False)  # only fills in what is missing
    assert steps.load_steps(ann.id)["mantra"] == "m" and db.step_progress(ann.id) == {1: "partial"}
    assert db.get_quiz(ann.id)[0]["q"] == "M?"

    steps.save_steps(rnn.id, {"steps": STEPS, "mantra": "kept"})
    pack.save_pack(db, rnn.id, newer, replace=False)
    assert steps.load_steps(rnn.id)["mantra"] == "kept" and db.get_quiz(rnn.id)[0]["q"] == "New?"
    db.close()


def test_quiz_dialog_makes_the_pack_once_for_a_new_lecture(tmp_path, monkeypatch):
    from app.ui import quiz_dialog

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(groq_client, "GROQ_CONFIG", tmp_path / "groq.json")
    groq_client.save_settings({"api_key": "key", "auto": False})
    db, course, (ann, _rnn) = make_db(tmp_path)
    transcribe.save_transcript(ann.id, [Segment(0, 3, "neuron")])
    calls = []

    def fake_start(call, fn, *args):
        calls.append(fn)
        call.finished.emit(PACK if fn is pack.make_pack else {"open": [], "interview": [{"q": "I?", "a": ""}]})
        return True

    monkeypatch.setattr(quiz_dialog.BackgroundCall, "start", fake_start)
    dialog = quiz_dialog.QuizDialog(db, course, ann)
    assert calls == [pack.make_pack] and dialog.pages.currentIndex() == 1 and dialog.question.text() == "M?"
    assert steps.load_steps(ann.id)["steps"] == STEPS  # the same call also made the steps
    dialog.set_mode("interview")  # the pack had no interview questions: ask for just those
    assert calls == [pack.make_pack, quiz_dialog.make_open_questions] and dialog.open_page.question.text() == "I?"
    db.close()
