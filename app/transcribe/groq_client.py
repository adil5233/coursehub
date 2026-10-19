"""Groq's hosted Whisper and chat models (OpenAI-compatible API) plus the user's Groq settings."""
import json
import mimetypes
import re
import threading
import time
from datetime import date

import requests

from ..config import GROQ_CONFIG, write_text_atomic
from . import Segment, split_segments

API_URL = "https://api.groq.com/openai/v1"
OPENROUTER_URL = "https://openrouter.ai/api/v1"
MODEL = "whisper-large-v3"
LANGUAGE = "en"
# Whisper follows the prompt's style: Hinglish speech comes out as Indian English in Roman script.
PROMPT = "A programming lecture in Hinglish (Hindi and English mixed), transcribed in Indian English, Roman script."
_SETTINGS_LOCK = threading.RLock()  # the transcript thread adds usage while Settings may save the key
# Free limits are per model, so when one runs out the next takes over (best first).
CHAT_MODELS = (
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "llama-3.1-8b-instant",
)
CHAT_MODEL = CHAT_MODELS[0]
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)  # reasoning some models put in the reply itself
_GONE_HINTS = ("decommissioned", "does not exist", "not found", "not supported", "no longer")


class GroqError(Exception):
    pass


class InvalidKey(GroqError):
    pass


class ModelUnavailable(GroqError):
    """The model was removed from Groq or is not available for this key."""


class EmptyReply(GroqError):
    """The model answered with no text (e.g. its hidden reasoning used up max_tokens)."""


class RateLimited(GroqError):
    def __init__(self, retry_after: float, message: str, account_wide: bool = False) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.account_wide = account_wide  # the whole account is limited (OpenRouter's daily free requests)


def _error_message(resp: requests.Response) -> str:
    try:
        return resp.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return resp.text[:300] or f"HTTP {resp.status_code}"


def _raise_for_status(resp: requests.Response) -> None:
    if resp.status_code == 401:
        raise InvalidKey("Groq rejected the API key.")
    if resp.status_code == 429:
        try:
            retry_after = float(resp.headers.get("retry-after", "60"))
        except ValueError:
            retry_after = 60.0
        reset = resp.headers.get("x-ratelimit-reset")  # OpenRouter's own limit: reset time in ms since epoch
        if reset is not None:
            try:
                retry_after = max(1.0, float(reset) / 1000 - time.time())
            except ValueError:
                pass
        raise RateLimited(retry_after, _error_message(resp), account_wide=reset is not None)
    if resp.status_code >= 400:
        message = _error_message(resp)
        if resp.status_code == 404 or (resp.status_code == 400 and "model" in message.lower()
                                       and any(hint in message.lower() for hint in _GONE_HINTS)):
            raise ModelUnavailable(message)
        raise GroqError(f"Groq error {resp.status_code}: {message}")


def transcribe(api_key: str, audio: bytes, filename: str = "audio.mp3", timeout: float = 300) -> list[Segment]:
    resp = requests.post(
        f"{API_URL}/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": (filename, audio, mimetypes.guess_type(filename)[0] or "audio/mpeg")},
        data=[
            ("model", MODEL),
            ("language", LANGUAGE),
            ("prompt", PROMPT),
            ("response_format", "verbose_json"),
            ("timestamp_granularities[]", "segment"),
            ("timestamp_granularities[]", "word"),
            ("temperature", "0"),
        ],
        timeout=timeout,
    )
    _raise_for_status(resp)
    payload = resp.json()
    segments = [
        Segment(float(s["start"]), float(s["end"]), str(s.get("text", "")).strip())
        for s in payload.get("segments") or []
    ]
    words = [
        Segment(float(w["start"]), float(w["end"]), str(w.get("word", "")).strip())
        for w in payload.get("words") or []
    ]
    return split_segments(segments, words)


def chat(api_key: str, messages: list[dict], max_tokens: int = 1500, temperature: float = 0.3,
         timeout: float = 120, model: str = CHAT_MODEL, url: str = API_URL) -> str:
    """One chat completion on Groq, or on any OpenAI-compatible API given its url (e.g. OpenRouter)."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if model.startswith("openai/gpt-oss") and url == API_URL:
        body["reasoning_effort"] = "low"  # its hidden thinking also counts against the free token limits
    resp = requests.post(
        f"{url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "X-Title": "CourseHub"},
        json=body,
        timeout=timeout,
    )
    _raise_for_status(resp)
    try:
        content = resp.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise GroqError("The AI service sent an unexpected reply.") from exc
    text = _THINK.sub("", str(content)).strip()
    if not text:
        raise EmptyReply(f"{model} sent an empty reply.")
    return text


def check_openrouter_key(api_key: str) -> None:
    """Raises InvalidKey / GroqError when OpenRouter does not accept the key."""
    resp = requests.get(f"{OPENROUTER_URL}/key", headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
    if resp.status_code == 401:
        raise InvalidKey("OpenRouter rejected the API key.")
    if resp.status_code >= 400:
        raise GroqError(f"OpenRouter error {resp.status_code}: {_error_message(resp)}")


def check_key(api_key: str) -> None:
    """Raises InvalidKey / GroqError when the key cannot be used."""
    resp = requests.get(f"{API_URL}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
    if resp.status_code == 401:
        raise InvalidKey("Groq rejected the API key.")
    if resp.status_code >= 400:
        raise GroqError(f"Groq error {resp.status_code}: {_error_message(resp)}")


# ---- settings (api key, auto mode, today's usage) ----
def load_settings() -> dict:
    try:
        data = json.loads(GROQ_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"api_key": str(data.get("api_key", "")), "auto": bool(data.get("auto", False)),
            "usage": data.get("usage") or {}, "openrouter_key": str(data.get("openrouter_key", ""))}


def save_settings(settings: dict) -> None:
    with _SETTINGS_LOCK:
        write_text_atomic(GROQ_CONFIG, json.dumps(settings))


def audio_seconds_today(settings: dict) -> float:
    usage = settings.get("usage") or {}
    return float(usage.get("seconds", 0)) if usage.get("date") == date.today().isoformat() else 0.0


def add_usage(seconds: float) -> None:
    with _SETTINGS_LOCK:
        settings = load_settings()
        settings["usage"] = {"date": date.today().isoformat(), "seconds": audio_seconds_today(settings) + seconds}
        save_settings(settings)
