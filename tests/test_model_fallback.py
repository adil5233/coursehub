import threading
import time

import pytest
from requests.structures import CaseInsensitiveDict

from app.transcribe import groq_client, providers, summary
from tests.test_summary import FakeResponse

GROQ_FIRST, GROQ_SECOND = groq_client.CHAT_MODELS[0], groq_client.CHAT_MODELS[1]
KEYS = {providers.GROQ: "gsk", providers.OPENROUTER: ""}
BOTH = {providers.GROQ: "gsk", providers.OPENROUTER: "sk-or"}


def ask(statuses=None):
    report = statuses.append if statuses is not None else (lambda _t: None)
    return summary.ask("gsk", "p", 100, report, threading.Event())


def save_openrouter_key():
    groq_client.save_settings({"api_key": "gsk", "auto": False, "openrouter_key": "sk-or"})


# ---- the HTTP layer ----
def test_chat_sends_the_model_and_cleans_thinking(monkeypatch):
    sent = []

    def fake_post(url, headers, json, timeout):
        sent.append((url, json))
        return FakeResponse(200, {"choices": [{"message": {"content": "<think>hmm\nok</think>\n Jawab "}}]})

    monkeypatch.setattr(groq_client.requests, "post", fake_post)
    assert groq_client.chat("k", [], model="qwen/qwen3.8-27b") == "Jawab"
    assert sent[0][1]["model"] == "qwen/qwen3.8-27b" and "reasoning_effort" not in sent[0][1]
    groq_client.chat("k", [])
    assert sent[1][0].startswith(groq_client.API_URL) and sent[1][1]["reasoning_effort"] == "low"
    groq_client.chat("k", [], model="openai/gpt-oss-120b", url=groq_client.OPENROUTER_URL)
    assert sent[2][0] == "https://openrouter.ai/api/v1/chat/completions" and "reasoning_effort" not in sent[2][1]


def test_empty_reply_is_an_error(monkeypatch):
    monkeypatch.setattr(groq_client.requests, "post",
                        lambda *a, **k: FakeResponse(200, {"choices": [{"message": {"content": None}}]}))
    with pytest.raises(groq_client.EmptyReply):
        groq_client.chat("k", [])


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


def test_openrouter_account_limit_is_recognised(monkeypatch):
    reset_ms = (time.time() + 3600) * 1000
    monkeypatch.setattr(groq_client.requests, "post", lambda *a, **k: FakeResponse(
        429, {"error": {"message": "Rate limit exceeded"}},
        CaseInsensitiveDict({"X-RateLimit-Reset": str(reset_ms)})))  # like requests' real headers
    with pytest.raises(groq_client.RateLimited) as info:
        groq_client.chat("k", [], url=groq_client.OPENROUTER_URL)
    assert info.value.account_wide and 3590 < info.value.retry_after <= 3600
    monkeypatch.setattr(groq_client.requests, "post", lambda *a, **k: FakeResponse(429, headers={"retry-after": "9"}))
    with pytest.raises(groq_client.RateLimited) as info:
        groq_client.chat("k", [])
    assert not info.value.account_wide and info.value.retry_after == 9


def test_check_openrouter_key(monkeypatch):
    seen = []
    monkeypatch.setattr(groq_client.requests, "get",
                        lambda url, headers, timeout: seen.append(url) or FakeResponse(401))
    with pytest.raises(groq_client.InvalidKey, match="OpenRouter"):
        groq_client.check_openrouter_key("bad")
    assert seen == ["https://openrouter.ai/api/v1/key"]


def test_settings_keep_the_openrouter_key():
    save_openrouter_key()
    settings = groq_client.load_settings()
    groq_client.save_settings(settings)  # every saver loads, changes and saves the whole dict
    groq_client.add_usage(5)
    assert groq_client.load_settings()["openrouter_key"] == "sk-or"


# ---- choosing a model ----
def test_openrouter_model_list(monkeypatch):
    offered = [{"id": i} for i in ("x/big:free", "google/gemma-4-31b-it:free", "a/paid-model", "n/safety-9b:free",
                                   "c/north-code:free", "nvidia/nemotron-3-super-120b-a12b:free", "b/chat:free")]
    monkeypatch.setattr(providers, "_fetch_free_models", lambda: offered)
    assert providers.openrouter_models() == ("nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-31b-it:free",
                                             "b/chat:free", "x/big:free")
    monkeypatch.setattr(providers, "_fetch_free_models", lambda: [{"id": "changed:free"}])
    assert providers.openrouter_models()[0] == "nvidia/nemotron-3-super-120b-a12b:free"  # read once per run
    providers.reset()

    def offline():
        raise providers.requests.ConnectionError("offline")

    monkeypatch.setattr(providers, "_fetch_free_models", offline)
    assert providers.openrouter_models() == providers.OPENROUTER_PREFERRED


def test_route_and_pick():
    assert providers.route(KEYS) == [(providers.GROQ, m) for m in groq_client.CHAT_MODELS]
    route = providers.route(BOTH)
    assert route[-len(providers.OPENROUTER_PREFERRED):] == [(providers.OPENROUTER, m)
                                                            for m in providers.OPENROUTER_PREFERRED]
    assert providers.pick(BOTH) == ((providers.GROQ, GROQ_FIRST), 0.0)
    providers.cool_down((providers.GROQ, GROQ_FIRST), 600)
    assert providers.pick(BOTH) == ((providers.GROQ, GROQ_SECOND), 0.0)
    for model in groq_client.CHAT_MODELS:
        providers.cool_down((providers.GROQ, model), 600)
    assert providers.pick(BOTH)[0] == (providers.OPENROUTER, providers.OPENROUTER_PREFERRED[0])
    entry, wait = providers.pick(KEYS)
    assert entry is None and 590 < wait <= 600
    for model in groq_client.CHAT_MODELS:
        providers.mark_gone((providers.GROQ, model))
    assert providers.pick(KEYS) == (None, float("inf"))
    assert providers.label((providers.OPENROUTER, "google/gemma-4-31b-it:free")) == "gemma-4-31b-it (OpenRouter)"
    assert providers.label((providers.GROQ, GROQ_FIRST)) == "gpt-oss-120b"


# ---- summary.ask: the fallback in action ----
def fake_chat_with(behaviour, used):
    def fake_chat(key, messages, max_tokens, model, url):
        used.append((model, url))
        outcome = behaviour.get(model, "ok")
        if isinstance(outcome, Exception):
            raise outcome
        return f"{outcome} from {model}"
    return fake_chat


def test_daily_limit_switches_to_the_next_model_and_remembers(monkeypatch):
    used, statuses = [], []
    monkeypatch.setattr(groq_client, "chat", fake_chat_with({GROQ_FIRST: groq_client.RateLimited(36_000, "d")}, used))
    assert ask(statuses) == f"ok from {GROQ_SECOND}"
    assert [m for m, _u in used] == [GROQ_FIRST, GROQ_SECOND]
    assert statuses == [f"gpt-oss-120b hit its free limit, switching to {GROQ_SECOND}..."]
    ask()
    assert [m for m, _u in used].count(GROQ_FIRST) == 1  # the limited model is not tried again


def test_removed_or_empty_models_are_skipped(monkeypatch):
    used = []
    behaviour = {GROQ_FIRST: groq_client.ModelUnavailable("gone"), GROQ_SECOND: groq_client.EmptyReply("empty")}
    monkeypatch.setattr(groq_client, "chat", fake_chat_with(behaviour, used))
    third = groq_client.CHAT_MODELS[2]
    assert ask() == f"ok from {third}"
    ask()
    assert [m for m, _u in used] == [GROQ_FIRST, GROQ_SECOND, third, third]


def test_groq_used_up_moves_to_openrouter(monkeypatch):
    save_openrouter_key()
    used, statuses = [], []
    behaviour = {model: groq_client.RateLimited(7200, "day") for model in groq_client.CHAT_MODELS}
    monkeypatch.setattr(groq_client, "chat", fake_chat_with(behaviour, used))
    first_free = providers.OPENROUTER_PREFERRED[0]
    assert ask(statuses) == f"ok from {first_free}"
    assert used[-1] == (first_free, groq_client.OPENROUTER_URL)
    assert statuses[-1].endswith("switching to nemotron-3-super-120b-a12b (OpenRouter)...")


def test_openrouter_daily_limit_benches_every_openrouter_model(monkeypatch):
    save_openrouter_key()
    used = []
    behaviour = {model: groq_client.RateLimited(7200, "day") for model in groq_client.CHAT_MODELS}
    behaviour[providers.OPENROUTER_PREFERRED[0]] = groq_client.RateLimited(3600, "account", account_wide=True)
    monkeypatch.setattr(groq_client, "chat", fake_chat_with(behaviour, used))
    with pytest.raises(groq_client.GroqError, match="used up on every model. Try again in about 60 min.$"):
        ask()
    assert [u for _m, u in used].count(groq_client.OPENROUTER_URL) == 1  # one 429 benched all of OpenRouter


def test_bad_openrouter_key_does_not_stop_groq(monkeypatch):
    save_openrouter_key()
    used = []
    behaviour = {GROQ_FIRST: groq_client.RateLimited(7200, "day")}
    for model in groq_client.CHAT_MODELS[1:]:
        behaviour[model] = groq_client.ModelUnavailable("gone")
    behaviour[providers.OPENROUTER_PREFERRED[0]] = groq_client.InvalidKey("OpenRouter rejected the API key.")
    monkeypatch.setattr(groq_client, "chat", fake_chat_with(behaviour, used))
    with pytest.raises(groq_client.GroqError, match="used up on every model"):
        ask()
    assert [u for _m, u in used].count(groq_client.OPENROUTER_URL) == 1


def test_bad_groq_key_still_stops_the_job(monkeypatch):
    monkeypatch.setattr(groq_client, "chat", fake_chat_with({GROQ_FIRST: groq_client.InvalidKey("bad")}, []))
    with pytest.raises(groq_client.InvalidKey):
        ask()


def test_used_up_without_openrouter_suggests_adding_it(monkeypatch):
    behaviour = {model: groq_client.RateLimited(7200, "day") for model in groq_client.CHAT_MODELS}
    monkeypatch.setattr(groq_client, "chat", fake_chat_with(behaviour, []))
    with pytest.raises(groq_client.GroqError, match="Add a free OpenRouter key in Settings"):
        ask()


def test_short_limits_on_every_model_wait_then_retry(monkeypatch):
    used, statuses = [], []
    limited = set(groq_client.CHAT_MODELS)

    def fake_chat(key, messages, max_tokens, model, url):
        used.append(model)
        if model in limited:
            limited.discard(model)
            raise groq_client.RateLimited(0.01, "minute")
        return model

    monkeypatch.setattr(groq_client, "chat", fake_chat)
    monkeypatch.setattr(providers, "cool_down", lambda entry, seconds: providers._cooldown.__setitem__(
        entry, time.monotonic() + seconds))  # keep the tiny wait instead of the 1 s minimum
    assert ask(statuses) in groq_client.CHAT_MODELS and len(used) == len(groq_client.CHAT_MODELS) + 1
    assert any(s.startswith("Waiting") for s in statuses)


def test_no_model_left(monkeypatch):
    for model in groq_client.CHAT_MODELS:
        providers.mark_gone((providers.GROQ, model))
    with pytest.raises(groq_client.GroqError, match="No AI model"):
        ask()
