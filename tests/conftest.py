import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolated_ai(tmp_path, monkeypatch):
    """Every test starts with all models available, never reads the real settings (keys) and never asks
    OpenRouter for its model list over the network."""
    from app.transcribe import groq_client, providers

    monkeypatch.setattr(groq_client, "GROQ_CONFIG", tmp_path / "groq-settings.json")
    monkeypatch.setattr(providers, "_fetch_free_models", lambda: [])
    providers.reset()
    yield
    providers.reset()
