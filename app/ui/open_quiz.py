from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from ..config import format_ms
from ..transcribe.grade import BackgroundCall, grade
from .recorder import VoiceAnswerButton
from .steps_panel import grade_text


class OpenQuizPage(QWidget):
    """Questions answered in your own words (typed or spoken), each checked by the AI with a 0-10 score."""

    finished = Signal(float, list)  # score 0..1, [(question dict, answer, Grade)] for answers below the pass mark
    jump_requested = Signal(int)  # time_ms
    answered = Signal(dict, str, object)  # question, answer, Grade

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.questions: list[dict] = []
        self.interview = False
        self.context_provider = lambda _time_ms: ""
        self._index = 0
        self._results: list[tuple[dict, str, object]] = []
        self.grader = BackgroundCall(self)
        self.grader.progress.connect(lambda text: self.status.setText(text))
        self.grader.finished.connect(self._on_graded)
        self.grader.failed.connect(self._on_failed)

        self.counter = QLabel()
        self.counter.setObjectName("Muted")
        self.question = QLabel()
        self.question.setObjectName("QuizQuestion")
        self.question.setWordWrap(True)
        self.answer_input = QPlainTextEdit()
        self.answer_input.setObjectName("NoteInput")
        self.answer_input.setMinimumHeight(110)
        self.voice_button = VoiceAnswerButton()
        self.voice_button.text_ready.connect(self._append_spoken)
        self.voice_button.failed.connect(lambda message: self.status.setText(message))
        self.status = QLabel()
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        self.result = QLabel()
        self.result.setObjectName("CheckpointGrade")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.reference = QLabel()
        self.reference.setObjectName("QuizExplain")
        self.reference.setWordWrap(True)
        self.watch_button = QPushButton()
        self.watch_button.setObjectName("Ghost")
        self.watch_button.setToolTip("Close the quiz and play this part of the lecture")
        self.watch_button.clicked.connect(lambda: self.jump_requested.emit(int(self._current().get("time_ms", 0))))
        self.next_button = QPushButton("Check")
        self.next_button.setObjectName("Primary")
        self.next_button.clicked.connect(self._next)

        tools = QHBoxLayout()
        tools.addWidget(self.voice_button)
        tools.addWidget(self.status, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.watch_button)
        buttons.addStretch(1)
        buttons.addWidget(self.next_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.counter)
        layout.addWidget(self.question)
        layout.addWidget(self.answer_input)
        layout.addLayout(tools)
        layout.addWidget(self.result)
        layout.addWidget(self.reference)
        layout.addStretch(1)
        layout.addLayout(buttons)

    def start(self, questions: list[dict], interview: bool) -> None:
        self.questions, self.interview = questions, interview
        self.answer_input.setPlaceholderText(
            "Answer in simple English, as you would in an interview (type, or \U0001f3a4 speak)..." if interview
            else "Apne shabdon me likho ya \U0001f3a4 bolo...")
        self._index, self._results = 0, []
        self._show_question()

    def _current(self) -> dict:
        return self.questions[self._index]

    def _show_question(self) -> None:
        question = self._current()
        kind = "Interview question" if self.interview else "Apne shabdon me"
        self.counter.setText(f"{kind}  •  {self._index + 1} of {len(self.questions)}")
        prefix = "Chalane se pehle batao kya hoga: " if question.get("kind") == "predict" else ""
        self.question.setText(prefix + question["q"])
        self.answer_input.clear()
        self.answer_input.setReadOnly(False)
        self.status.clear()
        self.result.hide()
        self.reference.hide()
        self.watch_button.setText(f"▶  Watch this part ({format_ms(question.get('time_ms', 0))})")
        self.watch_button.hide()
        self.next_button.setText("Check")
        self.next_button.setEnabled(True)
        self.answer_input.setFocus()

    def _append_spoken(self, text: str) -> None:
        current = self.answer_input.toPlainText().rstrip()
        self.answer_input.setPlainText(f"{current} {text}".strip())
        self.status.clear()

    def _next(self) -> None:
        if self.next_button.text() == "Check":
            self._check()
        elif self._index + 1 < len(self.questions):
            self._index += 1
            self._show_question()
        else:
            total = 10 * len(self._results)
            score = sum(result.score for _q, _a, result in self._results) / total if total else 0.0
            self.finished.emit(score, [item for item in self._results if not item[2].passed])

    def _check(self) -> None:
        answer = self.answer_input.toPlainText().strip()
        if not answer:
            self.status.setText("Write or speak an answer first." if self.interview
                                else "Pehle apna jawab likho ya bolo.")
            return
        question = self._current()
        context = self.context_provider(int(question.get("time_ms", 0)))
        self.next_button.setEnabled(False)
        self.answer_input.setReadOnly(True)
        self.status.setText("Checking your answer...")
        self.grader.start(grade, question["q"], question.get("a", ""), answer, context, interview=self.interview)

    def _on_failed(self, message: str) -> None:
        self.status.setText(message)
        self.answer_input.setReadOnly(False)
        self.next_button.setEnabled(True)

    def _on_graded(self, result) -> None:
        question = self._current()
        self._results.append((question, self.answer_input.toPlainText().strip(), result))
        self.answered.emit(question, self._results[-1][1], result)
        self.status.clear()
        self.result.setText(grade_text(result))
        self.result.show()
        if question.get("a"):
            self.reference.setText(("Reference: " if self.interview else "Sahi jawab: ") + question["a"])
            self.reference.show()
        self.watch_button.show()
        last = self._index + 1 >= len(self.questions)
        self.next_button.setText("See result" if last else "Next")
        self.next_button.setEnabled(True)
