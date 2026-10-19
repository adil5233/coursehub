import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def fresh_groq_models():
    """Model cooldowns are process-wide; every test starts with all models available."""
    from app.transcribe import groq_client

    groq_client.reset_models()
    yield
    groq_client.reset_models()
