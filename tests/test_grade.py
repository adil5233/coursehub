import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import app.transcribe as transcribe  # noqa: E402
from app.transcribe import Segment, grade, groq_client, quiz  # noqa: E402
from app.transcribe.grade import Grade  # noqa: E402
from tests.test_quiz import make_db  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_until(app, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_parse_grade():
    result = grade.parse_grade('Here: {"score": 7.4, "sahi": " sum ", "chhoota": "bias", "better": "Sum + bias."}')
    assert result == Grade(7, "sum", "bias", "Sum + bias.") and result.passed
    assert grade.parse_grade('{"score": 14}').score == 10 and grade.parse_grade('{"score": -3}').score == 0
    assert not grade.parse_grade('{"score": 5}').passed
    for bad in ("no json", '{"sahi": "x"}', '{"score": "high"}', "{broken"):
        assert grade.parse_grade(bad) is None


def test_grade_prompts_and_empty_answer(monkeypatch):
    calls = []

    def fake_ask(_key, prompt, _tokens, _report, _cancel, **kwargs):
        calls.append((prompt, kwargs))
        return '{"score": 8, "sahi": "ok", "chhoota": "", "better": "b"}'

    monkeypatch.setattr(grade, "ask", fake_ask)
    assert grade.grade("k", "Q?", "ref", "   ").score == 0 and calls == []  # nothing to check: no API call
    result = grade.grade("k", "Neuron kya karta hai?", "Weighted sum", "inputs ko jodta hai", "lecture text")
    assert result.score == 8
    prompt, kwargs = calls[-1]
    assert "inputs ko jodta hai" in prompt and "lecture text" in prompt and "Weighted sum" in prompt
    assert kwargs == {}  # Hinglish system prompt
    grade.grade("k", "What is a neuron?", "", "It sums inputs.", interview=True)
    prompt, kwargs = calls[-1]
    assert prompt.startswith("You are a friendly interviewer") and "simple English" in kwargs["system"]
    monkeypatch.setattr(grade, "ask", lambda *a, **k: "sorry")
    with pytest.raises(groq_client.GroqError):
        grade.grade("k", "Q", "a", "answer")


def test_context_for():
    segments = [Segment(s, s + 5, f"t{s}") for s in range(0, 60, 10)]
    assert grade.context_for(segments, 10_000, 30_000) == "t10 t20"
    assert grade.context_for(segments) == "t0 t10 t20 t30 t40 t50"
    assert grade.context_for(segments, max_chars=5) == "t0 t1" and grade.context_for(None) == ""


def test_speech_to_text(monkeypatch):
    sent = {}

    def fake_transcribe(key, audio, filename):
        sent.update(key=key, audio=audio, filename=filename)
        return [Segment(0, 1, " weights "), Segment(1, 2, ""), Segment(2, 3, "update hote hain")]

    monkeypatch.setattr(grade.groq_client, "transcribe", fake_transcribe)
    assert grade.speech_to_text("k", b"abc", "a.m4a") == "weights update hote hain"
    assert sent == {"key": "k", "audio": b"abc", "filename": "a.m4a"}


def test_transcribe_sends_the_right_audio_type(monkeypatch):
    sent = {}

    class Response:
        status_code = 200
        headers = {}

        def json(self):
            return {"segments": [], "words": []}

    def fake_post(url, headers, files, data, timeout):
        sent["file"] = files["file"]
        return Response()

    monkeypatch.setattr(groq_client.requests, "post", fake_post)
    groq_client.transcribe("k", b"x", "answer.m4a")
    assert sent["file"][2] in ("audio/mp4", "audio/x-m4a", "audio/m4a")
    groq_client.transcribe("k", b"x")
    assert sent["file"][2] == "audio/mpeg"


def test_open_questions_parse_and_spread(monkeypatch):
    reply = ('{"open": [{"q": "Bias kyun?", "a": "shift"}, {"q": ""}, "x"], '
             '"interview": [{"q": "What is a neuron?", "a": "A unit."}], "extra": 1}')
    parsed = quiz.parse_open(reply, 5000)
    assert parsed == {"open": [{"q": "Bias kyun?", "a": "shift", "time_ms": 5000}],
                      "interview": [{"q": "What is a neuron?", "a": "A unit.", "time_ms": 5000}]}
    assert quiz.parse_open("nothing", 0) == {"open": [], "interview": []}
    assert quiz._spread(list(range(10)), 3) == [0, 4, 9] and quiz._spread([1, 2], 3) == [1, 2]

    monkeypatch.setattr(quiz, "timed_parts", lambda segments: [(0, "a"), (60_000, "b")])
    monkeypatch.setattr(quiz, "ask", lambda *a, **k: reply)
    data = quiz.make_open_questions("k", [Segment(0, 1, "x")], size=3)
    assert [q["time_ms"] for q in data["open"]] == [0, 60_000] and len(data["interview"]) == 2
    monkeypatch.setattr(quiz, "ask", lambda *a, **k: "{}")
    with pytest.raises(groq_client.GroqError):
        quiz.make_open_questions("k", [Segment(0, 1, "x")])


def test_db_open_quiz(tmp_path):
    db, _course, (ann, rnn) = make_db(tmp_path)
    db.save_open_quiz(ann.id, {"open": [{"q": "a"}], "interview": []})
    db.save_open_quiz(ann.id, {"open": [{"q": "b"}], "interview": []})
    assert db.get_open_quiz(ann.id)["open"] == [{"q": "b"}] and db.get_open_quiz(rnn.id) is None
    db.close()


def test_background_call(app, monkeypatch, tmp_path):
    monkeypatch.setattr(groq_client, "GROQ_CONFIG", tmp_path / "groq.json")
    results, errors = [], []
    call = grade.BackgroundCall()
    call.finished.connect(results.append)
    call.failed.connect(errors.append)
    call.start(lambda key, x, report, cancel: key + x, "!")
    wait_until(app, lambda: errors)
    assert errors and "No Groq API key" in errors[0] and not call.busy
    groq_client.save_settings({"api_key": "key", "auto": False})
    call.start(lambda key, x, report, cancel: key + x, "!")
    wait_until(app, lambda: results)
    assert results == ["key!"] and not call.busy


def test_checkpoint_grade_suggests_a_mark(app):
    from app.ui.steps_panel import StepsPanel
    from tests.test_steps import STEPS

    panel = StepsPanel()
    panel.set_steps(STEPS, "", {})
    panel.context_provider = lambda index: f"context {index}"
    started = []
    panel.grader.start = lambda *args, **kwargs: started.append(args) or True
    marks = []
    panel.marked.connect(lambda *args: marks.append(args))
    panel.show_checkpoint(0)
    card = panel.card
    card.grade_button.click()  # nothing written yet
    assert started == [] and "Pehle apna jawab" in card.status.text()
    card.answer_input.setPlainText("weights se multiply")
    card.grade_button.click()
    assert started[0][1:] == ("Neuron kya karta hai?", "Weighted sum + bias.", "weights se multiply", "context 0")
    assert not card.grade_button.isEnabled()
    panel._on_graded(Grade(6, "multiply sahi", "bias bhool gaye", "Weighted sum + bias."))
    assert "Score: 6/10" in card.grade_label.text() and "bias bhool gaye" in card.grade_label.text()
    assert not card.answer.isHidden() and card.mark_buttons["partial"].objectName() == "Primary"
    card.mark_buttons["partial"].click()
    assert marks == [(0, "partial", "weights se multiply", 6)]


def test_voice_button_puts_the_spoken_text_in_the_answer(app, tmp_path):
    from app.ui.steps_panel import StepsPanel
    from tests.test_steps import STEPS

    panel = StepsPanel()
    panel.set_steps(STEPS, "", {})
    panel.show_checkpoint(0)
    voice = panel.card.voice_button
    voice.speech.start = lambda fn, data, name: voice.speech.finished.emit(f"{len(data)} bytes from {name}")
    recording = tmp_path / "answer.m4a"
    recording.write_bytes(b"12345")
    panel.card.answer_input.setPlainText("Pehle:")
    voice.transcribe_file(recording)
    assert panel.card.answer_input.toPlainText() == "Pehle: 5 bytes from answer.m4a" and not recording.exists()
    errors = []
    voice.failed.connect(errors.append)
    voice.transcribe_file(tmp_path / "missing.m4a")
    assert errors and "Nothing was recorded" in errors[0]


def test_quiz_dialog_interview_mode(app, tmp_path, monkeypatch):
    from app.ui import quiz_dialog

    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    db, course, (ann, _rnn) = make_db(tmp_path)
    db.save_quiz(ann.id, [{"q": "M?", "options": ["a", "b", "c", "d"], "answer": 0, "explain": "", "time_ms": 0}])
    db.save_open_quiz(ann.id, {"open": [{"q": "Bias kyun?", "a": "shift", "time_ms": 0}],
                               "interview": [{"q": "What is a neuron?", "a": "A unit.", "time_ms": 0},
                                             {"q": "Why bias?", "a": "To shift.", "time_ms": 60_000}]})
    dialog = quiz_dialog.QuizDialog(db, course, ann)
    page = dialog.open_page
    graded = iter([Grade(9, "good", "", "A neuron sums inputs."), Grade(3, "", "missed shift", "Bias shifts.")])
    seen = []
    page.grader.start = lambda fn, q, a, answer, context, interview: (
        seen.append((q, interview)), page._on_graded(next(graded)))

    dialog.mode_buttons.button(2).click()
    assert dialog.pages.currentIndex() == 3 and page.counter.text().startswith("Interview question")
    page._next()  # empty answer is not sent
    assert seen == [] and "first" in page.status.text()
    page.answer_input.setPlainText("It adds inputs.")
    page._next()
    assert seen == [("What is a neuron?", True)] and "Score: 9/10" in page.result.text()
    assert page.reference.text() == "Reference: A unit." and page.next_button.text() == "Next"
    page._next()
    page.answer_input.setPlainText("No idea.")
    page._next()
    page._next()  # see result
    assert dialog.pages.currentIndex() == 2 and dialog.score_label.text().startswith("6.0 / 10")
    assert "Why bias?" in dialog.wrong_label.text() and "Bias shifts." in dialog.wrong_label.text()
    assert db.get_review(ann.id)["last_score"] == pytest.approx(0.6)

    dialog.mode_buttons.button(1).click()  # own words: no steps, so the saved open questions
    assert dialog.pages.currentIndex() == 3 and page.question.text() == "Bias kyun?" and not page.interview
    dialog.mode_buttons.button(0).click()
    assert dialog.pages.currentIndex() == 1 and dialog.counter.text() == "Question 1 of 1"
    db.close()


def test_own_words_mode_prefers_step_checks(app, tmp_path, monkeypatch):
    from app.transcribe import steps
    from app.ui import quiz_dialog
    from tests.test_steps import STEPS

    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    db, course, (ann, _rnn) = make_db(tmp_path)
    db.save_quiz(ann.id, [{"q": "M?", "options": ["a", "b", "c", "d"], "answer": 0, "explain": "", "time_ms": 0}])
    steps.save_steps(ann.id, {"steps": STEPS, "mantra": ""})
    dialog = quiz_dialog.QuizDialog(db, course, ann)
    dialog.set_mode("open")
    assert [q["q"] for q in dialog.open_page.questions] == ["Neuron kya karta hai?", "print(2 * 3) kya dega?"]
    assert dialog.open_page.questions[1]["time_ms"] == 200_000
    dialog.open_page._index = 1
    dialog.open_page._show_question()
    assert dialog.open_page.question.text().startswith("Chalane se pehle batao")
    db.close()
