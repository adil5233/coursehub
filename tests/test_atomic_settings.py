import threading

from app.config import write_text_atomic
from app.transcribe import groq_client


def test_write_text_atomic_replaces_and_leaves_no_temp(tmp_path):
    target = tmp_path / "sub" / "settings.json"
    write_text_atomic(target, "one")
    write_text_atomic(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert [p.name for p in target.parent.iterdir()] == ["settings.json"]


def test_add_usage_keeps_the_key_and_counts_every_call(tmp_path, monkeypatch):
    monkeypatch.setattr(groq_client, "GROQ_CONFIG", tmp_path / "groq.json")
    groq_client.save_settings({"api_key": "secret", "auto": True, "usage": {}})
    threads = [threading.Thread(target=groq_client.add_usage, args=(1.0,)) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    settings = groq_client.load_settings()
    assert settings["api_key"] == "secret" and settings["auto"]
    assert groq_client.audio_seconds_today(settings) == 20.0
