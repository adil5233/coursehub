from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from ..config import display_title, format_ms
from ..transcribe import load_transcript
from ..transcribe.grade import BackgroundCall, context_for, reexplain

REEXPLAIN_AT = 3  # after this many wrong answers the usual explanation clearly is not working


class MistakeDialog(QDialog):
    """One logged mistake: the right answer next to mine, a fresh explanation, and a way back to the lecture."""

    jump_requested = Signal(int, int, int)  # course_id, video_id, time_ms

    def __init__(self, db, mistake: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Meri galti")
        self.resize(640, 560)
        self.db, self.mistake = db, mistake
        self.explanation_text = ""
        count = mistake["wrong_count"]

        kicker = QLabel(f"BAAR {count} GALAT")
        kicker.setObjectName("MistakeKicker" if count >= REEXPLAIN_AT else "CheckpointKicker")
        where = QLabel(f"{mistake['course_name']}  •  {display_title(mistake['video_title'])}  •  "
                       f"{format_ms(mistake['time_ms'])}")
        where.setObjectName("Muted")
        where.setWordWrap(True)
        question = QLabel(mistake["question"])
        question.setObjectName("QuizQuestion")
        question.setWordWrap(True)
        correct = QLabel(f"Sahi jawab: {mistake['correct'] or '-'}")
        correct.setObjectName("CheckpointAnswer")
        correct.setWordWrap(True)
        mine = QLabel(f"Tumhara jawab: {mistake['my_answer'] or '(khaali)'}")
        mine.setObjectName("Muted")
        mine.setWordWrap(True)
        for label in (question, correct, mine):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hint = QLabel(f"{count} baar galat ho gaya: ab naye tareeke se samjho \U0001f447")
        self.hint.setVisible(count >= REEXPLAIN_AT)

        self.explain_button = QPushButton("Naye tareeke se samjhao")
        if count >= REEXPLAIN_AT:
            self.explain_button.setObjectName("Primary")
        self.explain_button.setToolTip("A fresh ELI10 explanation with a new example (Groq)")
        self.explain_button.clicked.connect(self._explain)
        watch = QPushButton(f"▶  Lecture me wahan jao ({format_ms(mistake['time_ms'])})")
        watch.clicked.connect(self._watch)
        actions = QHBoxLayout()
        actions.addWidget(self.explain_button)
        actions.addWidget(watch)
        actions.addStretch(1)

        self.status = QLabel()
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        self.explanation = QTextBrowser()
        self.explanation.setObjectName("AskAnswers")
        self.explanation.hide()
        self.save_button = QPushButton("Notes me save karo")
        self.save_button.setToolTip("Add this explanation as a note at that moment of the lecture")
        self.save_button.clicked.connect(self._save_note)
        self.save_button.hide()

        resolved = QPushButton("Yaad ho gaya ✓")
        resolved.setToolTip("Remove it from Meri galtiyan")
        resolved.clicked.connect(self._resolve)
        close = QPushButton("Close")
        close.setObjectName("Primary")
        close.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.save_button)
        bottom.addStretch(1)
        bottom.addWidget(resolved)
        bottom.addWidget(close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(8)
        for widget in (kicker, where, question, correct, mine, self.hint):
            layout.addWidget(widget)
        layout.addLayout(actions)
        layout.addWidget(self.status)
        layout.addWidget(self.explanation, 1)
        layout.addStretch(0)
        layout.addLayout(bottom)

        self.caller = BackgroundCall(self)
        self.caller.progress.connect(self.status.setText)
        self.caller.finished.connect(self._on_explained)
        self.caller.failed.connect(self._on_failed)

    def _explain(self) -> None:
        m = self.mistake
        context = context_for(load_transcript(m["video_id"]), max(0, m["time_ms"] - 30_000))
        self.explain_button.setEnabled(False)
        self.status.setText("Naye tareeke se samjha raha hun...")
        self.caller.start(reexplain, m["question"], m["correct"], m["my_answer"], m["wrong_count"], context)

    def _on_explained(self, text: str) -> None:
        self.explanation_text = text
        self.status.clear()
        self.explain_button.setEnabled(True)
        self.explain_button.setText("Aur ek baar, alag tareeke se")
        self.explanation.setPlainText(text)
        self.explanation.show()
        self.save_button.show()
        self.save_button.setEnabled(True)
        self.save_button.setText("Notes me save karo")

    def _on_failed(self, message: str) -> None:
        self.explain_button.setEnabled(True)
        self.status.setText(message)

    def _save_note(self) -> None:
        m = self.mistake
        self.db.add_note(m["video_id"], m["time_ms"], f"Galti (Baar {m['wrong_count']}): {m['question']}\n\n"
                                                      f"{self.explanation_text}")
        self.save_button.setEnabled(False)
        self.save_button.setText("Saved ✓")

    def _watch(self) -> None:
        self.jump_requested.emit(self.mistake["course_id"], self.mistake["video_id"], self.mistake["time_ms"])
        self.accept()

    def _resolve(self) -> None:
        self.db.resolve_mistake(self.mistake["id"])
        self.accept()

    def done(self, result: int) -> None:
        self.caller.cancel()
        super().done(result)
