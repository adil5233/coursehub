import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

import app.transcribe as transcribe  # noqa: E402
from app import notes_export  # noqa: E402
from app.transcribe import Segment, steps, summary  # noqa: E402
from tests.test_quiz import make_db  # noqa: E402
from tests.test_steps import STEPS  # noqa: E402

MISTAKES = [{"question": "Bias kyun?", "my_answer": "pata nahi", "correct": "shift", "wrong_count": 3}]


def collect(text: str, prefix: str) -> dict[int, str]:
    """Same rule as mylearnings/review.py: 'Q1: ...' lines, an answer ends at a blank line or a heading."""
    pattern = re.compile(rf"^{prefix}(\d+):\s*(.*)$")
    found, current = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        match = pattern.match(line)
        if match:
            current = int(match.group(1))
            found[current] = match.group(2).strip()
        elif current is not None:
            if not line or line.startswith("#"):
                current = None
            else:
                found[current] += " " + line
    return found


def test_summary_prompt_has_the_four_sections_and_mistakes(monkeypatch):
    prompts = []
    monkeypatch.setattr(summary, "ask", lambda key, prompt, *a, **k: prompts.append(prompt) or "Title\n**QUESTIONS**")
    note = summary.summarize("k", [Segment(0, 1, "bias shift karta hai")], mistakes=MISTAKES)
    assert note == "Title\nQUESTIONS"  # bold stripped
    for section in summary.SECTIONS:
        assert f"'{section}'" in prompts[0]
    assert "Mantra:" in prompts[0] and "Sawaal: Bias kyun? | Mera jawab: pata nahi | Sahi: shift | Baar 3" in prompts[0]
    summary.summarize("k", [Segment(0, 1, "x")])
    assert "(koi galti nahi)" in prompts[-1]


def test_long_lecture_combine_step_gets_the_mistakes(monkeypatch):
    prompts = []
    monkeypatch.setattr(summary, "transcript_parts", lambda segments, size: ["a", "b"])
    monkeypatch.setattr(summary, "ask", lambda key, prompt, *a, **k: prompts.append(prompt) or "note")
    summary.summarize("k", [Segment(0, 1, "x")], mistakes=MISTAKES)
    assert len(prompts) == 3 and "Bias kyun?" not in prompts[0] and "Bias kyun?" in prompts[2]


def test_notes_panel_sends_the_lectures_mistakes(monkeypatch, tmp_path):
    from app.transcribe import groq_client
    from app.ui.notes_panel import NotesPanel
    from tests.test_notes_panel import FakeDb

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(groq_client, "GROQ_CONFIG", tmp_path / "groq.json")
    groq_client.save_settings({"api_key": "key", "auto": False})
    transcribe.save_transcript(1, [Segment(0, 3, "bias")])
    db = FakeDb()
    db.mistakes_for_video = lambda video_id: MISTAKES if video_id == 1 else []
    summarizer = summary.NoteSummarizer()
    started = []
    summarizer.start = lambda *args: started.append(args) or True
    panel = NotesPanel(db, lambda: 0, summarizer)
    panel.set_video(1)
    panel.ai_button.click()
    assert started == [(1, [Segment(0, 3, "bias")], MISTAKES)]
    app.processEvents()


def test_revision_questions_and_markdown():
    data = {"steps": STEPS, "mantra": "Weights badlo"}
    pairs = notes_export.revision_questions(data, {"open": [{"q": "ignored", "a": "x"}]})
    assert pairs == [("Neuron kya karta hai?", "Weighted sum + bias."), ("print(2 * 3) kya dega?", "6")]
    fallback = notes_export.revision_questions({"steps": []}, {"open": [{"q": "Bias\nkyun?", "a": "shift"}, {"q": ""}]})
    assert fallback == [("Bias kyun?", "shift")] and notes_export.revision_questions({"steps": []}, None) == []

    text = notes_export.revision_markdown(4, "04 Neuron.mp4", pairs + [("Khaali?", "")], MISTAKES, "Weights badlo")
    assert text.splitlines()[0] == "# Lecture 4 — 04 Neuron"
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == ["## 1️⃣ QUESTIONS", "## 2️⃣ ANSWERS", "## 3️⃣ MERI GALTI", "## 4️⃣ EK LINE ME"]
    assert collect(text, "Q") == {1: "Neuron kya karta hai?", 2: "print(2 * 3) kya dega?", 3: "Khaali?"}
    assert collect(text, "A") == {1: "Weighted sum + bias.", 2: "6", 3: "(lecture me dekho)"}
    assert "- Bias kyun? → sahi: shift (Baar 3)" in text and text.rstrip().endswith("Weights badlo")
    assert "Abhi koi nahi" in notes_export.revision_markdown(1, "x", [], [], "")
    assert notes_export.revision_file_name(3, '01: Intro / "AI".mp4') == "lecture03 - 01 Intro  AI.md"


def test_export_revision_notes_writes_one_file_per_lecture(tmp_path, monkeypatch):
    from app.ui.utils import export_revision_notes

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path / "t")
    out = tmp_path / "out"
    out.mkdir()
    messages = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(out))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: messages.append(a[2]))
    db, course, (ann, rnn) = make_db(tmp_path)

    export_revision_notes(None, db, course)
    assert list(out.iterdir()) == [] and "Nothing to export" in messages[-1]

    steps.save_steps(ann.id, {"steps": STEPS, "mantra": ""})
    db.add_mistake(rnn.id, "RNN kya hai?", "loop")
    export_revision_notes(None, db, course)
    files = sorted(p.name for p in out.iterdir())
    assert files == ["lecture01 - 01 ANN.md", "lecture02 - 02 RNN.md"] and "Saved 2" in messages[-1]
    first = (out / files[0]).read_text(encoding="utf-8")
    assert collect(first, "Q")[1] == "Neuron kya karta hai?"
    assert first.rstrip().endswith("Neuron inputs ko weights se multiply karta hai.")  # no mantra: first gist
    second = (out / files[1]).read_text(encoding="utf-8")
    assert collect(second, "Q") == {} and "- RNN kya hai? → sahi: loop (Baar 1)" in second
    db.close()
