import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

import app.transcribe as transcribe  # noqa: E402
from app.transcribe import Segment, ask, groq_client  # noqa: E402

LECTURE = [
    Segment(0, 5, "Aaj hum neural networks ke baare me baat karenge."),
    Segment(30, 35, "Har neuron inputs ko weights se multiply karta hai aur bias add karta hai."),
    Segment(70, 75, "Training me gradient descent weights ko update karta hai."),
    Segment(130, 135, "Ab break lete hain, phir LSTM dekhenge."),
]


def test_passages_and_retrieval():
    items = ask.passages(7, LECTURE)
    assert [(p.start_ms, p.text[:12]) for p in items] == [(0, "Aaj hum neur"), (70_000, "Training me "),
                                                          (130_000, "Ab break let")]
    best = ask.retrieve(items, "weights kaise update hote hain?", k=2)
    assert [p.start_ms for p in best] == [0, 70_000]  # timeline order, both mention weights
    assert ask.retrieve(items, "kya hai?") == []  # only stopwords
    assert ask.tokens("Kya WEIGHTS hai?") == ["weights"]


def test_answer_prompt_and_no_match(monkeypatch):
    prompts = []
    monkeypatch.setattr(ask, "ask", lambda key, prompt, *a, **k: prompts.append(prompt) or "Weights update [C1].")
    found = ask.retrieve(ask.passages(7, LECTURE), "gradient descent")
    assert ask.answer("key", "gradient descent kya hai?", found, time_label=lambda ms: f"{ms // 1000}s") == \
        "Weights update [C1]."
    assert "[C1] (70s): Training me gradient" in prompts[0] and "gradient descent kya hai?" in prompts[0]
    assert "nahi mila" in ask.answer("key", "xyz", [])


def test_ask_panel_answers_with_clickable_times(tmp_path, monkeypatch):
    from app.ui.ask_panel import AskPanel

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(groq_client, "GROQ_CONFIG", tmp_path / "groq.json")
    groq_client.save_settings({"api_key": "key", "auto": False})
    transcribe.save_transcript(7, LECTURE)
    monkeypatch.setattr(ask, "ask", lambda *a, **k: "Gradient descent weights badalta hai [C1].")

    panel = AskPanel()
    video = SimpleNamespace(id=7)
    panel.set_lecture(video, [(video, 3)])
    panel.input.setText("gradient descent kya karta hai")
    panel._ask()
    deadline = time.monotonic() + 5
    while panel.asker.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    page = panel.answers.toHtml()
    assert "Gradient descent weights badalta hai" in page and 'href="jump:7:70000"' in page
    jumps = []
    panel.jump_requested.connect(lambda *args: jumps.append(args))
    from PySide6.QtCore import QUrl
    panel._on_link(QUrl("jump:7:70000"))
    assert jumps == [(7, 70_000)]
    rendered = panel.render("q", "**Bias** <add> hota hai\u0964 [C9]", [])
    assert "<b>Bias</b> &lt;add&gt; hota hai." in rendered and "C9" not in rendered

    other = SimpleNamespace(id=8)  # no transcript
    panel.set_lecture(other, [(video, 3), (other, 4)])
    panel.input.setText("weights")
    panel._ask()
    assert "No transcript yet" in panel.status.text()
    panel.scope.setChecked(True)  # whole course: lecture 3 has a transcript
    assert [p.label for p in panel._items()] == ["Lecture 3"] * 3
