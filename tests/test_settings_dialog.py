import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.transcribe import groq_client  # noqa: E402


class FakeServices:
    def cache_size(self):
        return 0


def test_openrouter_key_is_saved_and_tested(monkeypatch):
    from app.ui import settings_dialog

    QApplication.instance() or QApplication([])
    groq_client.save_settings({"api_key": "gsk", "auto": True, "usage": {}, "openrouter_key": ""})
    dialog = settings_dialog.SettingsDialog(FakeServices())
    assert dialog.key_edit.text() == "gsk" and dialog.openrouter_edit.text() == ""
    dialog.openrouter_edit.setText("  sk-or-123 ")
    dialog.openrouter_edit.editingFinished.emit()
    settings = groq_client.load_settings()
    assert settings["openrouter_key"] == "sk-or-123" and settings["api_key"] == "gsk" and settings["auto"]

    checked, shown = [], []
    monkeypatch.setattr(settings_dialog, "run_with_progress", lambda parent, text, fn, key: checked.append((fn, key)))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    dialog._test_openrouter_key()
    assert checked == [(groq_client.check_openrouter_key, "sk-or-123")] and "Key works" in shown[-1]
    dialog.key_edit.setText("gsk-new")
    dialog._save_groq()  # saving the Groq key keeps the OpenRouter one
    assert groq_client.load_settings()["openrouter_key"] == "sk-or-123"
