"""Groq's hosted Whisper (OpenAI-compatible audio API) plus the user's Groq settings."""
import json
from datetime import date

import requests

from ..config import GROQ_CONFIG
from . import Segment, split_segments

API_URL = "https://api.groq.com/openai/v1"
MODEL = "whisper-large-v3"
LANGUAGE = "en"
# Whisper follows the prompt's style: Hinglish speech comes out as Indian English in Roman script.
PROMPT = "A programming lecture in Hinglish (Hindi and English mixed), transcribed in Indian English, Roman script."


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


def transcribe(api_key: str, audio: bytes, filename: str = "audio.mp3", timeout: float = 300) -> list[Segment]:
    resp = requests.post(
        f"{API_URL}/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": (filename, audio, "audio/mpeg")},
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
    GROQ_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    GROQ_CONFIG.write_text(json.dumps(settings), encoding="utf-8")


def audio_seconds_today(settings: dict) -> float:
    usage = settings.get("usage") or {}
    return float(usage.get("seconds", 0)) if usage.get("date") == date.today().isoformat() else 0.0


def add_usage(seconds: float) -> None:
    settings = load_settings()
    settings["usage"] = {"date": date.today().isoformat(), "seconds": audio_seconds_today(settings) + seconds}
    save_settings(settings)
