import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import app.transcribe as transcribe  # noqa: E402
from app.models import ScannedVideo  # noqa: E402
from app.transcribe import grade  # noqa: E402
from app.transcribe.grade import Grade  # noqa: E402
from tests.test_quiz import make_db  # noqa: E402
from tests.test_steps import view  # noqa: E402,F401  (fixture)

MCQ = {"q": "Neuron kya karta hai?", "options": ["Sum + bias", "Sort", "Print", "Nothing"], "answer": 0,
       "explain": "", "time_ms": 30_000}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_db_mistakes_count_and_order(tmp_path):
    db, course, (ann, rnn) = make_db(tmp_path)
    assert db.add_mistake(ann.id, "Q1", "a", "x", 1000, step_index=2) == 1
    assert db.add_mistake(rnn.id, "Q2", "b", "y") == 1
    assert db.add_mistake(ann.id, "Q1", "a2", "z", 5000) == 2  # same question again: Baar 2, step kept
    first = db.mistakes()[0]
    assert (first["question"], first["wrong_count"], first["my_answer"], first["time_ms"], first["step_index"]) == (
        "Q1", 2, "z", 5000, 2)
    assert first["course_name"] == "AI" and first["video_title"] == "01 ANN.mp4" and first["course_id"] == course.id
    assert [m["question"] for m in db.mistakes()] == ["Q1", "Q2"] and len(db.mistakes(limit=1)) == 1
    assert [m["question"] for m in db.mistakes_for_video(ann.id)] == ["Q1"]
    assert db.get_mistake(first["id"])["question"] == "Q1" and db.get_mistake(999) is None
    db.resolve_mistake(first["id"])
    assert [m["question"] for m in db.mistakes()] == ["Q2"]
    db.sync_videos(course.id, [ScannedVideo("", "01 ANN.mp4", "a", 0)])  # lecture 2 removed at the source
    assert db.mistakes() == []
    db.close()


def test_quiz_logs_wrong_answers_and_asks_them_first(app, tmp_path, monkeypatch):
    from app.ui import quiz_dialog

    db, course, (ann, _rnn) = make_db(tmp_path)
    db.save_quiz(ann.id, [dict(MCQ, q="Pehla"), MCQ])
    monkeypatch.setattr(quiz_dialog.random, "sample", lambda population, k: list(population))
    dialog = quiz_dialog.QuizDialog(db, course, ann)
    assert dialog.question.text() == "Pehla"
    dialog._choose(1)  # display slot 1 -> "Sort": wrong
    dialog._next()
    mistakes = db.mistakes()
    assert [(m["question"], m["correct"], m["my_answer"], m["time_ms"]) for m in mistakes] == [
        ("Pehla", "Sum + bias", "Sort", 30_000)]
    dialog._start()  # next attempt: the question that went wrong comes first
    assert dialog.question.text() == "Pehla"
    db.add_mistake(ann.id, MCQ["q"], "x")
    db.add_mistake(ann.id, MCQ["q"], "x")
    dialog._start()
    assert dialog.question.text() in ("Pehla", MCQ["q"])  # both are mistakes now: either may lead
    db.close()


def test_low_scored_own_words_answer_is_a_mistake(app, tmp_path, monkeypatch):
    from app.ui import quiz_dialog

    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    db, course, (ann, _rnn) = make_db(tmp_path)
    db.save_quiz(ann.id, [MCQ])
    db.save_open_quiz(ann.id, {"open": [{"q": "Bias kyun?", "a": "", "time_ms": 7000},
                                        {"q": "Weights kya?", "a": "numbers", "time_ms": 0}], "interview": []})
    dialog = quiz_dialog.QuizDialog(db, course, ann)
    page = dialog.open_page
    grades = iter([Grade(4, "", "shift bhool gaye", "Bias output shift karta hai."), Grade(9, "ok", "", "")])
    page.grader.start = lambda *a, **k: page._on_graded(next(grades))
    dialog.set_mode("open")
    page.answer_input.setPlainText("pata nahi")
    page._next()
    page._next()
    page.answer_input.setPlainText("numbers jo seekhe jaate hain")
    page._next()
    assert [(m["question"], m["correct"], m["my_answer"], m["time_ms"]) for m in db.mistakes()] == [
        ("Bias kyun?", "Bias output shift karta hai.", "pata nahi", 7000)]  # no reference: the better answer
    db.close()


def test_checkpoint_again_and_low_score_are_mistakes(view):
    view.set_study_mode(True)
    view.steps._on_marked(0, "again", "pata nahi", None)
    view.steps._on_marked(1, "partial", "5", 4)  # low AI score counts too
    view.steps._on_marked(2, "again", "", None)  # a step without a question
    view.steps._on_marked(0, "done", "weighted sum", 9)  # fine: not a mistake
    assert view.db.mistakes == [
        ("Neuron kya karta hai?", "Weighted sum + bias.", 0, 0),
        ("print(2 * 3) kya dega?", "6", 200_000, 1),
        ("Wrap up: samajh nahi aaya", "Summary.", 400_000, 2),
    ]


def test_library_lists_mistakes(app, tmp_path):
    from app.ui.library_view import LibraryView

    db, _course, (ann, rnn) = make_db(tmp_path)
    view = LibraryView(db)
    view.refresh()
    assert view.mistakes_title.isHidden() and view.mistake_list.isHidden()
    db.add_mistake(rnn.id, "RNN kya hai?", "loop")
    for _ in range(3):
        db.add_mistake(ann.id, "Bias kyun?", "shift")
    view.refresh()
    assert view.mistakes_title.text() == "Meri galtiyan (2)" and not view.mistake_list.isHidden()
    first = view.mistake_list.item(0)
    assert first.text().startswith("\U0001f6a8 Baar 3  •  Bias kyun?") and "01 ANN" in first.text()
    assert view.mistake_list.item(1).text().startswith("Baar 1")
    opened = []
    view.mistake_opened.connect(opened.append)
    view.mistake_list.itemClicked.emit(first)
    assert opened == [db.mistakes()[0]["id"]]
    db.close()


def test_mistake_dialog(app, tmp_path, monkeypatch):
    from app.ui.mistakes_dialog import MistakeDialog

    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    db, course, (ann, _rnn) = make_db(tmp_path)
    transcribe.save_transcript(ann.id, [transcribe.Segment(40, 45, "bias se output shift hota hai")])
    for _ in range(3):
        db.add_mistake(ann.id, "Bias kyun?", "shift", "pata nahi", 60_000)
    dialog = MistakeDialog(db, db.mistakes()[0])
    assert not dialog.hint.isHidden() and dialog.explain_button.objectName() == "Primary"
    started = []
    dialog.caller.start = lambda *args: started.append(args) or True
    dialog.explain_button.click()
    fn, question, correct, mine, count, context = started[0]
    assert fn is grade.reexplain and (question, correct, mine, count) == ("Bias kyun?", "shift", "pata nahi", 3)
    assert "output shift" in context
    dialog._on_explained("Socho seesaw...\nYaad rakhna: bias = shift")
    assert dialog.explanation.toPlainText().startswith("Socho seesaw") and not dialog.save_button.isHidden()
    dialog.save_button.click()
    note = db.list_notes(ann.id)[0]
    assert note.timestamp_ms == 60_000 and note.text.startswith("Galti (Baar 3): Bias kyun?")
    assert "bias = shift" in note.text and not dialog.save_button.isEnabled()
    jumps = []
    dialog.jump_requested.connect(lambda *args: jumps.append(args))
    dialog._watch()
    assert jumps == [(course.id, ann.id, 60_000)]

    fresh = MistakeDialog(db, db.mistakes()[0])
    fresh._resolve()
    assert db.mistakes() == []
    db.add_mistake(ann.id, "Naya", "x")
    once = MistakeDialog(db, db.mistakes()[0])
    assert once.hint.isHidden() and once.explain_button.objectName() != "Primary"
    db.close()
