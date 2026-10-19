import threading

import pytest

from app.transcribe import groq_client, summary
from tests.test_summary import FakeResponse

FIRST, SECOND = groq_client.CHAT_MODELS[0], groq_client.CHAT_MODELS[1]


def test_chat_sends_the_model_and_cleans_thinking(monkeypatch):
    sent = []

    def fake_post(url, headers, json, timeout):
        sent.append(json)
        return FakeResponse(200, {"choices": [{"message": {"content": "<think>hmm\nok</think>\n Jawab "}}]})

    monkeypatch.setattr(groq_client.requests, "post", fake_post)
    assert groq_client.chat("k", [], model="qwen/qwen3.8-27b") == "Jawab"
    assert sent[0]["model"] == "qwen/qwen3.8-27b" and "reasoning_effort" not in sent[0]
    groq_client.chat("k", [])
    assert sent[1]["model"] == FIRST and sent[1]["reasoning_effort"] == "low"


@pytest.mark.parametrize("status, message, error", [
    (404, "Not found", groq_client.ModelUnavailable),
    (400, "The model `x` has been decommissioned", groq_client.ModelUnavailable),
    (400, "model does not exist", groq_client.ModelUnavailable),
    (400, "messages must not be empty", groq_client.GroqError),
])
def test_removed_models_are_recognised(monkeypatch, status, message, error):
    monkeypatch.setattr(groq_client.requests, "post",
                        lambda *a, **k: FakeResponse(status, {"error": {"message": message}}))
    with pytest.raises(error) as info:
        groq_client.chat("k", [])
    assert (type(info.value) is groq_client.ModelUnavailable) == (error is groq_client.ModelUnavailable)


def test_pick_model_skips_cooling_and_removed_models():
    assert groq_client.pick_model() == (FIRST, 0.0)
    groq_client.cool_down(FIRST, 600)
    assert groq_client.pick_model() == (SECOND, 0.0)
    for model in groq_client.CHAT_MODELS[1:]:
        groq_client.mark_gone(model)
    model, wait = groq_client.pick_model()
    assert model is None and 590 < wait <= 600
    groq_client.mark_gone(FIRST)
    assert groq_client.pick_model() == (None, float("inf"))
    groq_client.reset_models()
    assert groq_client.pick_model() == (FIRST, 0.0)


def fake_chat_with(behaviour, used):
    def fake_chat(key, messages, max_tokens, model):
        used.append(model)
        outcome = behaviour.get(model, "ok")
        if isinstance(outcome, Exception):
            raise outcome
        return f"{outcome} from {model}"
    return fake_chat


def test_daily_limit_switches_to_the_next_model_and_remembers(monkeypatch):
    used, statuses = [], []
    monkeypatch.setattr(groq_client, "chat", fake_chat_with({FIRST: groq_client.RateLimited(36_000, "day")}, used))
    assert summary.ask("k", "p", 100, statuses.append, threading.Event()) == f"ok from {SECOND}"
    assert used == [FIRST, SECOND] and statuses == [
        f"{groq_client.short_name(FIRST)} hit its free limit, switching to {groq_client.short_name(SECOND)}..."]
    summary.ask("k", "p", 100, statuses.append, threading.Event())
    assert used[-1] == SECOND and used.count(FIRST) == 1  # the limited model is not tried again


def test_removed_model_is_skipped(monkeypatch):
    used = []
    monkeypatch.setattr(groq_client, "chat", fake_chat_with({FIRST: groq_client.ModelUnavailable("gone")}, used))
    assert summary.ask("k", "p", 100, lambda _t: None, threading.Event()) == f"ok from {SECOND}"
    summary.ask("k", "p", 100, lambda _t: None, threading.Event())
    assert used == [FIRST, SECOND, SECOND]


def test_every_model_at_its_daily_limit_fails_clearly(monkeypatch):
    used = []
    behaviour = {model: groq_client.RateLimited(7200, "day") for model in groq_client.CHAT_MODELS}
    monkeypatch.setattr(groq_client, "chat", fake_chat_with(behaviour, used))
    with pytest.raises(groq_client.GroqError, match="used up on every model. Try again in about 120 min"):
        summary.ask("k", "p", 100, lambda _t: None, threading.Event())
    assert used == list(groq_client.CHAT_MODELS)


def test_short_limits_on_every_model_wait_then_retry(monkeypatch):
    used, statuses = [], []
    limited = {model: groq_client.RateLimited(0.01, "minute") for model in groq_client.CHAT_MODELS}

    def fake_chat(key, messages, max_tokens, model):
        used.append(model)
        if limited.pop(model, None):
            raise groq_client.RateLimited(0.01, "minute")
        return model

    monkeypatch.setattr(groq_client, "chat", fake_chat)
    monkeypatch.setattr(groq_client, "cool_down", lambda model, seconds: groq_client._cooldown.__setitem__(
        model, groq_client.time.monotonic() + seconds))  # keep the tiny wait instead of the 1 s minimum
    result = summary.ask("k", "p", 100, statuses.append, threading.Event())
    assert result in groq_client.CHAT_MODELS and len(used) == len(groq_client.CHAT_MODELS) + 1
    assert any(s.startswith("Waiting") for s in statuses)


def test_no_model_left_for_the_key(monkeypatch):
    for model in groq_client.CHAT_MODELS:
        groq_client.mark_gone(model)
    with pytest.raises(groq_client.GroqError, match="No Groq chat model"):
        summary.ask("k", "p", 100, lambda _t: None, threading.Event())
