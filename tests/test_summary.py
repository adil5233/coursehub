import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.transcribe import Segment, groq_client, summary  # noqa: E402
from tests.test_notes_panel import FakeDb  # noqa: E402


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload


def test_chat_returns_content_and_maps_errors(monkeypatch):
    sent = {}

    def fake_post(url, headers, json, timeout):
        sent.update(url=url, body=json)
        return FakeResponse(200, {"choices": [{"message": {"content": "  Title\n- point  "}}]})

    monkeypatch.setattr(groq_client.requests, "post", fake_post)
    assert groq_client.chat("key", [{"role": "user", "content": "hi"}]) == "Title\n- point"
    assert sent["url"].endswith("/chat/completions") and sent["body"]["model"] == groq_client.CHAT_MODEL

    monkeypatch.setattr(groq_client.requests, "post", lambda *a, **k: FakeResponse(401))
    with pytest.raises(groq_client.InvalidKey):
        groq_client.chat("key", [])
    monkeypatch.setattr(groq_client.requests, "post", lambda *a, **k: FakeResponse(429, headers={"retry-after": "7"}))
    with pytest.raises(groq_client.RateLimited) as info:
        groq_client.chat("key", [])
    assert info.value.retry_after == 7


def segments(count, text="shabd " * 20):
    return [Segment(i * 5.0, i * 5.0 + 5, f"{i} {text}".strip()) for i in range(count)]


def test_transcript_parts_keep_order_and_size():
    parts = summary.transcript_parts(segments(300), max_chars=2000)
    assert len(parts) > 1 and all(len(p) <= 2000 for p in parts)
    assert " ".join(parts).split()[0] == "0" and parts[-1].split()[-21] == "299"
    assert summary.transcript_parts([Segment(0, 1, "  ")]) == []


def test_short_lecture_is_one_call(monkeypatch):
    calls = []
    monkeypatch.setattr(groq_client, "chat", lambda key, messages, max_tokens, model, url: calls.append(messages) or "Note")
    assert summary.summarize("key", segments(10)) == "Note"
    assert len(calls) == 1 and "Hinglish" in calls[0][0]["content"] and "Transcript:" in calls[0][1]["content"]


def test_markdown_bold_is_removed_outside_code(monkeypatch):
    reply = "Title\n- **LLM** matlab Large Language Model\n```\nx = a ** 2\n```"
    monkeypatch.setattr(groq_client, "chat", lambda *a, **k: reply)
    assert summary.summarize("key", segments(3)) == "Title\n- LLM matlab Large Language Model\n```\nx = a ** 2\n```"


def test_long_lecture_is_map_reduce_and_waits_for_rate_limit(monkeypatch):
    prompts, statuses = [], []
    limited = {"once": True}

    def fake_chat(key, messages, max_tokens, model, url):
        if limited.pop("once", False):
            raise groq_client.RateLimited(0.01, "slow down")
        prompts.append(messages[1]["content"])
        return f"- notes {len(prompts)}"

    monkeypatch.setattr(groq_client, "chat", fake_chat)
    monkeypatch.setattr(summary, "PART_CHARS", 2000)
    result = summary.summarize("key", segments(300), statuses.append)
    parts = len(summary.transcript_parts(segments(300), 2000))
    assert len(prompts) == parts + 1  # one per part, then one to combine
    assert "- notes 1" in prompts[-1] and f"- notes {parts}" in prompts[-1]
    assert result == f"- notes {parts + 1}"
    assert any("switching to" in s for s in statuses) and statuses[-1] == "Writing the summary..."


def test_daily_limit_fails_instead_of_waiting_for_hours(monkeypatch):
    monkeypatch.setattr(groq_client, "chat", lambda *a, **k: (_ for _ in ()).throw(
        groq_client.RateLimited(3600, "daily")))
    with pytest.raises(groq_client.GroqError, match="used up"):
        summary.summarize("key", segments(5))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def close_windows():
    yield
    for widget in QApplication.topLevelWidgets():
        widget.close()


def wait_until(app, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_notes_panel_ai_summary_saves_a_note(app, monkeypatch, tmp_path):
    import app.transcribe as transcribe
    from app.ui.notes_panel import NotesPanel

    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(groq_client, "GROQ_CONFIG", tmp_path / "groq.json")
    groq_client.save_settings({"api_key": "key", "auto": False})
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: messages.append(a[2]))
    release = threading.Event()
    monkeypatch.setattr(summary, "summarize", lambda key, segs, report, cancel: release.wait(5) and
                        "Decorators\nKey points:\n- wrap karte hain")

    db = FakeDb()
    panel = NotesPanel(db, lambda: 0, summary.NoteSummarizer())
    panel.resize(600, 400)
    panel.set_video(1)
    panel.show()

    panel.ai_button.click()  # no transcript yet
    assert messages and "transcript" in messages[0].lower() and db.notes == {}

    transcribe.save_transcript(1, [Segment(0, 3, "aaj decorators")])
    panel.ai_button.click()
    assert not panel.ai_button.isEnabled() and panel.ai_button.text() == "Summarizing..."
    release.set()
    wait_until(app, lambda: db.notes)
    assert [(n.timestamp_ms, n.text) for n in db.notes.values()] == [(0, "Decorators\nKey points:\n- wrap karte hain")]
    assert [c.text for c in panel.cards()] == ["Decorators\nKey points:\n- wrap karte hain"]
    assert panel.ai_button.isEnabled() and panel.ai_status.text().startswith("Summary added")
