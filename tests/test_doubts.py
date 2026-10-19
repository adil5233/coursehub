import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import app.transcribe as transcribe  # noqa: E402
from app.transcribe import ask, groq_client  # noqa: E402
from tests.test_ask import LECTURE  # noqa: E402
from tests.test_quiz import make_db  # noqa: E402
from tests.test_steps import view  # noqa: E402,F401  (fixture)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_idle(app, panel, timeout=5):
    deadline = time.monotonic() + timeout
    while panel.asker.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_time_passage_and_merge():
    now = ask.time_passage(7, LECTURE, 100_000)  # 10 s .. 130 s
    assert (now.start_ms, now.text) == (30_000, LECTURE[1].text + " " + LECTURE[2].text + " " + LECTURE[3].text)
    assert ask.time_passage(7, LECTURE, 900_000) is None
    found = ask.passages(7, LECTURE)  # starts 0, 70 s, 130 s
    merged = ask.with_now(now, found)
    assert merged[0] is now and [p.start_ms for p in merged[1:]] == []  # all overlap the current moment
    early = ask.time_passage(7, LECTURE, 0, before_ms=0, after_ms=5_000)
    assert [p.start_ms for p in ask.with_now(early, found)] == [0, 70_000, 130_000]
    assert ask.with_now(None, found) == found


def test_prompts_have_eli10_step_and_explain_mode(monkeypatch):
    prompts = []
    monkeypatch.setattr(ask, "ask", lambda key, prompt, *a, **k: prompts.append(prompt) or "ok")
    found = [ask.time_passage(7, LECTURE, 40_000)]
    ask.answer("k", "ye bias kya hai?", found, step="Neuron: weighted sum")
    assert "ye bias kya hai?" in prompts[-1] and "(Abhi ka step: Neuron: weighted sum)" in prompts[-1]
    assert "10 saal ke bachche" in prompts[-1] and "abhi chal rahe hisse" in prompts[-1]
    ask.answer("k", "", found, explain=True)
    assert prompts[-1].startswith("Student video dekh raha tha") and "Student ne poocha" not in prompts[-1]
    ask.answer("k", "multiply kyun?", found, explain=True)
    assert "Student ne poocha: multiply kyun?" in prompts[-1] and "Ek line me" in prompts[-1]


def test_db_doubts(tmp_path):
    db, _course, (ann, rnn) = make_db(tmp_path)
    db.add_doubt(ann.id, 5000, "Q?", "A [C1]", [{"video_id": ann.id, "start_ms": 0, "label": ""}])
    assert [(d["question"], d["time_ms"], d["sources"]) for d in db.list_doubts(ann.id)] == [
        ("Q?", 5000, [{"video_id": ann.id, "start_ms": 0, "label": ""}])]
    assert db.list_doubts(rnn.id) == []
    db.close()


def test_ask_panel_explain_now_history_and_typing(app, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.ui.ask_panel import AskPanel

    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(groq_client, "GROQ_CONFIG", tmp_path / "groq.json")
    groq_client.save_settings({"api_key": "key", "auto": False})
    db, _course, (ann, _rnn) = make_db(tmp_path)
    transcribe.save_transcript(ann.id, LECTURE)
    prompts = []
    monkeypatch.setattr(ask, "ask", lambda key, prompt, *a, **k: prompts.append(prompt) or "Weights jodo [C1].")

    position = {"ms": 40_000}
    panel = AskPanel(db, lambda: position["ms"], lambda ms: f"step at {ms}")
    typed = []
    panel.typing_started.connect(lambda: typed.append(1))
    video = SimpleNamespace(id=ann.id)
    panel.set_lecture(video, [(video, 1)])
    panel.input.textEdited.emit("w")
    panel.input.textEdited.emit("we")
    assert typed == [1]  # once per question

    panel.explain_now()
    wait_idle(app, panel)
    assert prompts[-1].startswith("Student video dekh raha tha") and "(Abhi ka step: step at 40000)" in prompts[-1]
    assert "multiply karta hai" in prompts[-1] and "LSTM" not in prompts[-1]  # only the last ~90 s + 30 s ahead
    saved = db.list_doubts(ann.id)
    assert saved[0]["question"] == "Abhi wala samjhao (0:40)" and saved[0]["time_ms"] == 40_000
    page = panel.answers.toHtml()
    assert "Abhi wala samjhao (0:40)" in page and f'href="jump:{ann.id}:0"' in page

    position["ms"] = 130_000
    panel.input.setText("gradient descent kya karta hai")
    panel._ask()
    wait_idle(app, panel)
    assert "[C1] (1:10): Training" in prompts[-1] and "LSTM" in prompts[-1]  # the current moment comes first
    assert len(db.list_doubts(ann.id)) == 2 and panel.input.text() == ""

    fresh = AskPanel(db)
    fresh.set_lecture(video, [(video, 1)])  # opened again later: earlier doubts are still there
    history = fresh.answers.toHtml()
    assert "Abhi wala samjhao (0:40)" in history and "gradient descent kya karta hai" in history
    db.close()


def test_typing_a_question_pauses_the_video(view):
    assert view.player_state["playing"]
    view.ask.input.textEdited.emit("y")
    assert not view.player_state["playing"]
    assert view._step_text(250_000) == "Code run" and view._step_text(10) == (
        "Neuron: Neuron inputs ko weights se multiply karta hai.")
