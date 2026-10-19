import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import app.transcribe as transcribe  # noqa: E402
from app.db import Database  # noqa: E402
from app.models import ScannedVideo  # noqa: E402
from app.transcribe import Segment, steps  # noqa: E402

STEPS = [
    {"start_ms": 0, "title": "Neuron", "ek_line": "Neuron inputs ko weights se multiply karta hai.",
     "words": [{"word": "bias", "meaning": "extra number"}], "check": {"kind": "recall", "q": "Neuron kya karta hai?",
                                                                       "a": "Weighted sum + bias."}},
    {"start_ms": 200_000, "title": "Code run", "ek_line": "", "words": [],
     "check": {"kind": "predict", "q": "print(2 * 3) kya dega?", "a": "6"}},
    {"start_ms": 400_000, "title": "Wrap up", "ek_line": "Summary.", "words": [], "check": None},
]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def lecture(minutes: int) -> list[Segment]:
    return [Segment(s, s + 9, f"line {s}") for s in range(0, minutes * 60, 10)]


def test_parse_steps_keeps_valid_items_and_trims():
    reply = """Sure: [
      {"time": "0:10", "title": "  Neuron  ", "ek_line": "ek line",
       "naye_shabd": [{"word": "bias", "meaning": "m1"}, {"word": "weight", "meaning": "m2"}, {"word": "x", "meaning": "m3"}],
       "check": {"kind": "predict", "q": "Kya hoga?", "a": "6"}},
      {"time": "3:30", "title": "Odd kind", "check": {"kind": "essay", "q": "Samjhao", "a": ""}},
      {"time": "4:00", "title": "No question", "check": {"q": ""}, "naye_shabd": "bad"},
      {"time": "99:00", "title": "Outside"},
      {"time": "x", "title": "Bad time"},
      "junk"
    ]"""
    parsed = steps.parse_steps(reply, 0, 600_000)
    assert [s["title"] for s in parsed] == ["Neuron", "Odd kind", "No question"]
    first = parsed[0]
    assert first["start_ms"] == 10_000 and first["ek_line"] == "ek line"
    assert [w["word"] for w in first["words"]] == ["bias", "weight"]  # never more than two new words
    assert first["check"] == {"kind": "predict", "q": "Kya hoga?", "a": "6"}
    assert parsed[1]["check"]["kind"] == "recall"  # unknown kinds fall back to recall
    assert parsed[2]["check"] is None and parsed[2]["words"] == []
    assert steps.parse_steps("no json here", 0, 1) == [] and steps.parse_steps("[{bad json]", 0, 1) == []


def test_tidy_steps_merges_close_steps_and_starts_at_zero():
    raw = [{"start_ms": ms, "title": str(ms)} for ms in (400_000, 20_000, 100_000, 200_000)]
    assert [(s["start_ms"], s["title"]) for s in steps.tidy_steps(raw)] == [
        (0, "20000"), (200_000, "200000"), (400_000, "400000")]
    assert steps.tidy_steps([]) == []


def test_step_index_at():
    assert [steps.step_index_at(STEPS, ms) for ms in (0, 199_999, 200_000, 900_000)] == [0, 0, 1, 2]
    assert steps.step_index_at([{"start_ms": 5000}], 0) == -1


def test_make_steps_mantra_and_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(steps, "marked_parts", lambda segments: [(0, 599_000, "a"), (600_000, 1_200_000, "b")])
    replies = iter(['[{"time": "0:00", "title": "Basics", "ek_line": "x"}]',
                    '[{"time": "11:00", "title": "Training", "ek_line": "y"}]',
                    '"Weights badlo, loss ghatao"'])
    prompts = []

    def fake_ask(_key, prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(steps, "ask", fake_ask)
    data = steps.make_steps("key", lecture(1))
    assert [s["title"] for s in data["steps"]] == ["Basics", "Training"]
    assert data["mantra"] == "Weights badlo, loss ghatao"  # quotes stripped
    assert "Basics: x" in prompts[-1] and "Training: y" in prompts[-1]
    steps.save_steps(7, data)
    assert steps.load_steps(7) == data
    assert steps.load_steps(8) == {"steps": [], "mantra": ""}
    assert not list(tmp_path.glob("*.tmp"))
    monkeypatch.setattr(steps, "ask", lambda *a, **k: "[]")
    with pytest.raises(steps.groq_client.GroqError):
        steps.make_steps("key", lecture(1))


def test_db_step_progress(tmp_path):
    db = Database(tmp_path / "t.db")
    course_id = db.add_course("C", "local", "x", [ScannedVideo("", "a", "a.mp4")])
    video_id = db.get_videos(course_id)[0].id
    db.set_step_progress(video_id, 0, "done", "weighted sum")
    db.set_step_progress(video_id, 1, "again")
    db.set_step_progress(video_id, 0, "partial")
    assert db.step_progress(video_id) == {0: "partial", 1: "again"}
    with pytest.raises(ValueError):
        db.set_step_progress(video_id, 2, "maybe")
    db.clear_step_progress(video_id)
    assert db.step_progress(video_id) == {}
    db.close()


def test_steps_panel_list_and_checkpoint(app):
    from app.ui.steps_panel import StepsPanel

    panel = StepsPanel()
    panel.set_steps(STEPS, "Weights badlo", {0: "done"})
    assert panel.list.count() == 3 and panel.list.item(0).text().startswith("✅")
    assert panel.mantra.text() == "Mantra: Weights badlo" and panel.make_button.text() == "Remake steps"
    assert "1 of 3" in panel.status.text()
    seeks, marks, replays = [], [], []
    panel.seek_requested.connect(seeks.append)
    panel.marked.connect(lambda *args: marks.append(args))
    panel.replay_requested.connect(replays.append)
    panel.list.itemClicked.emit(panel.list.item(1))
    assert seeks == [200_000]

    panel.show_checkpoint(1)
    card = panel.card
    assert panel.checkpoint_open() and "Step 2 of 3" in card.kicker.text()
    assert card.question.text().startswith("Chalane se pehle batao") and card.answer.isHidden()
    assert card.marks_row.isHidden()  # first answer, then see the answer, then mark
    card.answer_input.setPlainText("6 aayega")
    card.reveal()
    assert not card.answer.isHidden() and card.answer.text() == "Answer: 6"
    card.mark_buttons["again"].click()
    assert marks == [(1, "again", "6 aayega", None)] and panel.checkpoint_open() and not card.replay_button.isHidden()
    card.replay_button.click()
    assert replays == [1]
    card.mark_buttons["done"].click()
    assert not panel.checkpoint_open() and panel.list.item(1).text().startswith("✅")

    panel.show_checkpoint(2)  # a step without a question: just the self-mark
    assert card.question.isHidden() and not card.marks_row.isHidden()


@pytest.fixture
def view(app, monkeypatch):
    pytest.importorskip("vlc")
    from app.player.vlc_widget import VideoPlayer
    from app.ui.course_view import CourseView
    from tests.test_course_view_panels import FakeThumbnails, FakeTranscripts

    class StudyDb:
        def __init__(self):
            self.state, self.progress = {}, {}

        def get_state(self, key, default=None):
            return self.state.get(key, default)

        def set_state(self, key, value):
            self.state[key] = value

        def set_step_progress(self, video_id, index, status, answer="", score=None):
            self.progress[(video_id, index)] = (status, answer)

        def add_mistake(self, video_id, question, correct="", my_answer="", time_ms=0, step_index=None):
            self.mistakes = getattr(self, "mistakes", []) + [(question, correct, time_ms, step_index)]

    player = {"playing": True, "seeks": []}

    def toggle(_player):
        player["playing"] = not player["playing"]

    monkeypatch.setattr(VideoPlayer, "is_playing", lambda _p: player["playing"])
    monkeypatch.setattr(VideoPlayer, "toggle_play", toggle)
    monkeypatch.setattr(VideoPlayer, "seek", lambda _p, ms: player["seeks"].append(ms))
    course_view = CourseView(StudyDb(), None, FakeThumbnails(), FakeTranscripts())
    course_view.current = SimpleNamespace(id=1, duration_ms=600_000, completed=False)
    course_view.videos = [course_view.current]  # a one-lecture course: nothing to prefetch next
    course_view.steps.set_steps(STEPS, "", {})
    course_view.player_state = player
    yield course_view
    course_view.current = None
    course_view.player.release()
    course_view.close()


def play(view, *positions):
    for position in positions:
        view._on_position(position, 600_000)


def test_study_mode_pauses_at_step_end_and_resumes(view):
    player = view.player_state
    play(view, 190_000, 199_000, 201_000)
    assert player["playing"] and not view.steps.checkpoint_open()  # study mode is off
    view.set_study_mode(True)
    assert view.db.state["study_mode"] == "1" and view.side_stack.currentIndex() == 3
    play(view, 300_000, 399_000, 401_000)
    assert not player["playing"] and view.steps.checkpoint_open() and view.steps.card.step_index == 1
    view.steps.card.reveal()
    view.steps.card.mark_buttons["done"].click()
    assert player["playing"] and not view.steps.checkpoint_open()
    assert view.db.progress[(1, 1)] == ("done", "")
    play(view, 402_000)
    assert not view.steps.checkpoint_open()  # one check per step


def test_seeking_past_a_step_end_does_not_pause(view):
    view.set_study_mode(True)
    play(view, 100_000, 150_000, 450_000)
    assert view.player_state["playing"] and not view.steps.checkpoint_open()


def test_replay_seeks_to_step_start_and_asks_again(view):
    player = view.player_state
    view.set_study_mode(True)
    play(view, 198_000, 200_500)
    assert view.steps.card.step_index == 0 and not player["playing"]
    view.steps.card.reveal()
    view.steps.card.mark_buttons["again"].click()
    assert not player["playing"]  # "Nahi" keeps the video paused until replay / skip
    view.steps.card.replay_button.click()
    assert player["seeks"] == [0] and player["playing"] and not view.steps.checkpoint_open()
    play(view, 0, 1_000, 198_000, 199_500, 200_500)
    assert view.steps.checkpoint_open() and view.steps.card.step_index == 0


def test_seeking_back_lets_a_checked_step_ask_again(view):
    view.set_study_mode(True)
    play(view, 199_000, 200_500)
    view.steps.close_checkpoint()
    assert view.player_state["playing"]
    play(view, 100_000, 199_000, 201_000)  # seeked back into step 1 and watched to its end again
    assert view.steps.checkpoint_open()


def test_last_step_check_comes_before_up_next(view):
    view.set_study_mode(True)
    play(view, 588_000, 589_500)
    assert view.steps.checkpoint_open() and view.steps.card.step_index == 2


def test_player_draws_step_ticks(app):
    pytest.importorskip("vlc")
    from app.player.vlc_widget import VideoPlayer

    player = VideoPlayer()
    player._length = 600_000
    player.set_steps([300_000, 150_000, 700_000])
    assert player.slider._steps == [0.25, 0.5]
    player.stop()
    assert player.slider._steps == []
    player.release()
