"""Groq's hosted Whisper and chat models (OpenAI-compatible API) plus the user's Groq settings."""
import json
import mimetypes
import threading
from datetime import date

import requests

from ..config import GROQ_CONFIG, write_text_atomic
from . import Segment, split_segments

API_URL = "https://api.groq.com/openai/v1"
MODEL = "whisper-large-v3"
LANGUAGE = "en"
# Whisper follows the prompt's style: Hinglish speech comes out as Indian English in Roman script.
PROMPT = "A programming lecture in Hinglish (Hindi and English mixed), transcribed in Indian English, Roman script."
_SETTINGS_LOCK = threading.RLock()  # the transcript thread adds usage while Settings may save the key
CHAT_MODEL = "openai/gpt-oss-120b"  # llama-3.3-70b-versatile is no longer offered on Groq


class GroqError(Exception):
    pass


class InvalidKey(GroqError):
    pass


class RateLimited(GroqError):
    def __init__(self, retry_after: float, message: str) -> None:
        super().__init__(message)
        self.retry_after = retry_after


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
        raise RateLimited(retry_after, _error_message(resp))
    if resp.status_code >= 400:
        raise GroqError(f"Groq error {resp.status_code}: {_error_message(resp)}")


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
         timeout: float = 120) -> str:
    body = {"model": CHAT_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if CHAT_MODEL.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"  # its hidden thinking also counts against the free token limits
    resp = requests.post(
        f"{API_URL}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        timeout=timeout,
    )
    _raise_for_status(resp)
    try:
        return str(resp.json()["choices"][0]["message"]["content"]).strip()
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise GroqError("Groq sent an unexpected reply.") from exc

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
            "usage": data.get("usage") or {}}


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
