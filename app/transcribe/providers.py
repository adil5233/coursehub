"""Which AI model answers a chat call: Groq's models first, then OpenRouter's free models as a backup.

The route is a list of (provider, model). A model that hits its limit cools down and the next one takes over;
a model that was removed is skipped. OpenRouter's free limit is per account (about 50 requests a day without
credits), so when OpenRouter itself says "limit reached" every OpenRouter model cools down together.
"""
import threading
import time

import requests

from . import groq_client

GROQ, OPENROUTER = "groq", "openrouter"
URLS = {GROQ: groq_client.API_URL, OPENROUTER: groq_client.OPENROUTER_URL}
OPENROUTER_PREFERRED = (  # general chat models that follow JSON instructions well; others are added after
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
    "minimax/minimax-m3.1-flash:free",
    "thinkingmachines/inkling:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
)
OPENROUTER_MAX_MODELS = 6
_SKIP_WORDS = ("safety", "code", "-vl", "omni", "nano", "2.6b")  # specialised or very small models
EMPTY_REPLY_COOLDOWN_S = 600

Entry = tuple[str, str]  # (provider, model)

_lock = threading.Lock()
_cooldown: dict[Entry, float] = {}  # entry -> time.monotonic() when its limit resets
_gone: set[Entry] = set()
_openrouter_models: tuple[str, ...] | None = None


def _fetch_free_models() -> list[dict]:
    resp = requests.get(f"{groq_client.OPENROUTER_URL}/models", timeout=15)
    resp.raise_for_status()
    return list(resp.json().get("data") or [])


def openrouter_models() -> tuple[str, ...]:
    """OpenRouter's free models, read once per run (the list changes often); preferred ones first."""
    global _openrouter_models
    with _lock:
        if _openrouter_models is not None:
            return _openrouter_models
    try:
        offered = [m.get("id", "") for m in _fetch_free_models()]
    except (requests.RequestException, ValueError, AttributeError):
        offered = []
    free = [m for m in offered if m.endswith(":free")]
    if free:
        others = sorted(m for m in free if m not in OPENROUTER_PREFERRED and not any(w in m for w in _SKIP_WORDS))
        models = tuple(([m for m in OPENROUTER_PREFERRED if m in free] + others)[:OPENROUTER_MAX_MODELS])
    else:
        models = OPENROUTER_PREFERRED  # list not available: try the usual ones
    with _lock:
        _openrouter_models = models
    return models


def route(keys: dict[str, str]) -> list[Entry]:
    entries = [(GROQ, model) for model in groq_client.CHAT_MODELS] if keys.get(GROQ) else []
    if keys.get(OPENROUTER):
        entries += [(OPENROUTER, model) for model in openrouter_models()]
    return entries


def pick(keys: dict[str, str]) -> tuple[Entry | None, float]:
    """(first usable entry, 0) or (None, seconds until one is free again; inf when none is left)."""
    entries = route(keys)
    now = time.monotonic()
    with _lock:
        usable = [e for e in entries if e not in _gone]
        for entry in usable:
            if _cooldown.get(entry, 0) <= now:
                return entry, 0.0
        waits = [_cooldown[e] - now for e in usable]
    return None, (min(waits) if waits else float("inf"))


def cool_down(entry: Entry, seconds: float) -> None:
    with _lock:
        _cooldown[entry] = time.monotonic() + max(1.0, seconds)


def cool_provider(provider: str, seconds: float, models: tuple[str, ...]) -> None:
    for model in models:
        cool_down((provider, model), seconds)


def mark_gone(entry: Entry) -> None:
    with _lock:
        _gone.add(entry)


def reset() -> None:
    global _openrouter_models
    with _lock:
        _cooldown.clear()
        _gone.clear()
        _openrouter_models = None


def label(entry: Entry) -> str:
    provider, model = entry
    name = model.rsplit("/", 1)[-1].removesuffix(":free")
    return name if provider == GROQ else f"{name} (OpenRouter)"


def chat(entry: Entry, keys: dict[str, str], messages: list[dict], max_tokens: int) -> str:
    provider, model = entry
    return groq_client.chat(keys[provider], messages, max_tokens=max_tokens, model=model, url=URLS[provider])


def handle_failure(entry: Entry, exc: Exception) -> bool:
    """Bench a model (or a provider) after a failed call. False means the error must reach the caller."""
    provider, model = entry
    if isinstance(exc, groq_client.RateLimited):
        if exc.account_wide and provider == OPENROUTER:
            cool_provider(OPENROUTER, exc.retry_after, openrouter_models())
        else:
            cool_down(entry, exc.retry_after)
    elif isinstance(exc, groq_client.ModelUnavailable):
        mark_gone(entry)
    elif isinstance(exc, groq_client.EmptyReply):
        cool_down(entry, EMPTY_REPLY_COOLDOWN_S)
    elif isinstance(exc, groq_client.InvalidKey) and provider == OPENROUTER:
        for other in openrouter_models():  # a bad backup key must not stop the Groq work
            mark_gone((OPENROUTER, other))
    else:
        return False
    return True
