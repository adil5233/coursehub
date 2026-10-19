import os
from datetime import date, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.db import Database  # noqa: E402
from app.models import ScannedVideo  # noqa: E402
from app.review import due_after, next_interval  # noqa: E402
from app.transcribe import Segment, quiz  # noqa: E402

GOOD = ('Sure! [{"q": "Neuron kya karta hai?", "options": ["Sum + bias", "Sort", "Print", "Nothing"], '
        '"answer": 0, "explain": "Weighted sum + bias."}, {"q": "bad", "options": ["a"], "answer": 0}]')


def test_spaced_repetition_intervals():
    assert next_interval(None, 1.0) == 3  # great first quiz: 3 days
    assert next_interval(3, 1.0) == 7.5 and next_interval(40, 1.0) == 60  # grows, capped
    assert next_interval(4, 0.75) == pytest.approx(7.2)
    assert next_interval(6, 0.5) == 6 and next_interval(20, 0.2) == 1  # poor score: tomorrow again
    assert due_after(1.4, date(2026, 10, 19)) == date(2026, 10, 20)


def test_parse_questions_skips_bad_items():
    questions = quiz.parse_questions(GOOD, 65_000)
    assert questions == [{"q": "Neuron kya karta hai?", "options": ["Sum + bias", "Sort", "Print", "Nothing"],
                          "answer": 0, "explain": "Weighted sum + bias.", "time_ms": 65_000}]
    assert quiz.parse_questions("no json here", 0) == [] and quiz.parse_questions("[{oops", 0) == []
    lettered = '[{"q": "x", "options": ["A) one", "B. two", "(C) three", "D: 4D model"], "answer": 1}]'
    assert quiz.parse_questions(lettered, 0)[0]["options"] == ["one", "two", "three", "4D model"]


def test_make_quiz_spreads_questions_over_parts(monkeypatch):
    prompts = []
    monkeypatch.setattr(quiz, "ask", lambda key, prompt, *a, **k: prompts.append(prompt) or GOOD)
    monkeypatch.setattr(quiz, "timed_parts", lambda segments: [(0, "part one"), (600_000, "part two")])
    questions = quiz.make_quiz("key", [Segment(0, 1, "x")], size=8)
    assert len(prompts) == 2 and "4 multiple-choice" in prompts[0]  # 8 questions over 2 parts
    assert [q["time_ms"] for q in questions] == [0, 600_000]
    monkeypatch.setattr(quiz, "ask", lambda *a, **k: "[]")
    with pytest.raises(quiz.groq_client.GroqError):
        quiz.make_quiz("key", [Segment(0, 1, "x")])


def make_db(tmp_path):
    db = Database(tmp_path / "data.db")
    course_id = db.add_course("AI", "local", str(tmp_path), [ScannedVideo("", "01 ANN.mp4", "a", 0),
                                                            ScannedVideo("", "02 RNN.mp4", "b", 0)])
    return db, db.get_course(course_id), db.get_videos(course_id)


def test_reviews_due_and_recorded(tmp_path):
    db, course, (ann, rnn) = make_db(tmp_path)
    today = date.today()
    db.ensure_review(ann.id, today)
    db.ensure_review(rnn.id, today + timedelta(days=1))
    db.ensure_review(ann.id, today + timedelta(days=9))  # existing schedule is kept
    assert [v.id for _c, v, _r in db.due_reviews(today)] == [ann.id]
    db.record_review(ann.id, 0.8, 3.0, today + timedelta(days=3))
    assert db.due_reviews(today) == [] and db.get_review(ann.id)["attempts"] == 1
    assert [v.id for _c, v, _r in db.due_reviews(today + timedelta(days=3))] == [rnn.id, ann.id]  # oldest first
    db.save_quiz(ann.id, [{"q": "x"}])
    db.save_quiz(ann.id, [{"q": "y"}])
    assert db.get_quiz(ann.id) == [{"q": "y"}] and db.get_quiz(rnn.id) is None
    db.close()


def test_quiz_dialog_scores_and_schedules(tmp_path, monkeypatch):
    from app.ui import quiz_dialog

    app = QApplication.instance() or QApplication([])
    db, course, (ann, _rnn) = make_db(tmp_path)
    question = quiz.parse_questions(GOOD, 30_000)[0]
    db.save_quiz(ann.id, [question, dict(question, q="Doosra sawaal")])
    monkeypatch.setattr(quiz_dialog.random, "sample", lambda population, k: list(population))  # no shuffling
    dialog = quiz_dialog.QuizDialog(db, course, ann)
    assert dialog.pages.currentIndex() == 1 and dialog.counter.text() == "Question 1 of 2"

    dialog._choose(0)  # right
    dialog._next()
    assert dialog.options[0].property("state") == "correct" and dialog.explain.text().startswith("Sahi")
    jumps = []
    dialog.jump_requested.connect(lambda *args: jumps.append(args))
    dialog._next()
    dialog._choose(2)  # wrong
    dialog._next()
    assert dialog.options[2].property("state") == "wrong" and dialog.options[0].property("state") == "correct"
    dialog._next()  # see result
    assert dialog.pages.currentIndex() == 2 and dialog.score_label.text().startswith("1 / 2")
    review = db.get_review(ann.id)
    assert review["last_score"] == 0.5 and review["due_date"] == (date.today() + timedelta(days=1)).isoformat()
    assert "Doosra sawaal" in dialog.wrong_label.text()
    dialog._start()
    dialog._choose(1)
    dialog._next()
    dialog._watch()
    assert jumps == [(course.id, ann.id, 30_000)]
    db.close()


def test_quiz_dialog_needs_a_transcript(tmp_path, monkeypatch):
    import app.transcribe as transcribe
    from app.ui.quiz_dialog import QuizDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path / "none")
    db, course, (ann, _rnn) = make_db(tmp_path)
    dialog = QuizDialog(db, course, ann)
    assert dialog.pages.currentIndex() == 0 and "transcript first" in dialog.loading_label.text()
    db.close()
