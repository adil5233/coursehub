from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..config import format_ms

STEP_INDEX = Qt.ItemDataRole.UserRole
STATUS_MARK = {"done": "✅", "partial": "\U0001f7e1", "again": "\U0001f501"}  # ✅ 🟡 🔁
TODO_MARK = "⬜"  # ⬜


class CheckpointCard(QFrame):
    """One step's check: gist, new words, a question to answer first, then the answer and a self-mark."""

    marked = Signal(int, str, str)  # step index, status, the learner's answer
    replay_requested = Signal(int)  # step index
    closed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Checkpoint")
        self.step_index = -1
        self.kicker = QLabel()
        self.kicker.setObjectName("CheckpointKicker")
        self.title = QLabel()
        self.title.setObjectName("CardTitle")
        self.title.setWordWrap(True)
        self.gist = QLabel()
        self.gist.setWordWrap(True)
        self.words = QLabel()
        self.words.setObjectName("Muted")
        self.words.setWordWrap(True)
        self.question = QLabel()
        self.question.setObjectName("CheckpointQuestion")
        self.question.setWordWrap(True)
        self.answer_input = QPlainTextEdit()
        self.answer_input.setObjectName("NoteInput")
        self.answer_input.setPlaceholderText("Pehle khud likho, phir answer dekho...")
        self.answer_input.setFixedHeight(72)
        self.reveal_button = QPushButton("Answer dekho")
        self.reveal_button.setObjectName("Primary")
        self.reveal_button.clicked.connect(self.reveal)
        self.answer = QLabel()
        self.answer.setObjectName("CheckpointAnswer")
        self.answer.setWordWrap(True)
        self.answer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.mark_buttons: dict[str, QPushButton] = {}
        marks = QHBoxLayout()
        marks.setSpacing(6)
        for status, text in (("done", "Samajh gaya ✅"), ("partial", "Thoda \U0001f7e1"), ("again", "Nahi \U0001f501")):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, status=status: self._mark(status))
            self.mark_buttons[status] = button
            marks.addWidget(button)
        self.marks_row = QWidget()
        self.marks_row.setLayout(marks)
        marks.setContentsMargins(0, 0, 0, 0)

        self.replay_button = QPushButton("Ye step dobara")
        self.replay_button.clicked.connect(lambda: self.replay_requested.emit(self.step_index))
        skip = QPushButton("Skip ›")
        skip.setObjectName("Ghost")
        skip.setToolTip("Close the checkpoint and keep watching")
        skip.clicked.connect(self.closed)
        bottom = QHBoxLayout()
        bottom.addWidget(self.replay_button)
        bottom.addStretch(1)
        bottom.addWidget(skip)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        for widget in (self.kicker, self.title, self.gist, self.words, self.question, self.answer_input,
                       self.reveal_button, self.answer, self.marks_row):
            layout.addWidget(widget)
        layout.addLayout(bottom)

    def show_step(self, index: int, total: int, step: dict) -> None:
        self.step_index = index
        self.kicker.setText(f"CHECKPOINT  •  Step {index + 1} of {total}")
        self.title.setText(step["title"])
        self.gist.setText(f"Ek line me: {step['ek_line']}" if step.get("ek_line") else "")
        self.gist.setVisible(bool(step.get("ek_line")))
        words = step.get("words") or []
        self.words.setText("\n".join(f"Naya shabd: {w['word']} — {w['meaning']}" for w in words))
        self.words.setVisible(bool(words))
        check = step.get("check")
        if check:
            prefix = "Chalane se pehle batao kya hoga:" if check["kind"] == "predict" else "Apne shabdon me:"
            self.question.setText(f"{prefix} {check['q']}")
            self.answer.setText(f"Answer: {check['a']}" if check.get("a") else "Answer: (transcript se dekh lo)")
        self.question.setVisible(check is not None)
        self.answer_input.setVisible(check is not None)
        self.answer_input.clear()
        self.reveal_button.setVisible(check is not None)
        self.answer.hide()
        self.marks_row.setVisible(check is None)  # without a question the learner just self-marks
        self.replay_button.hide()
        self.show()

    def reveal(self) -> None:
        self.reveal_button.hide()
        self.answer.show()
        self.marks_row.show()

    def _mark(self, status: str) -> None:
        self.marked.emit(self.step_index, status, self.answer_input.toPlainText().strip())
        self.replay_button.setVisible(status == "again")
        if status != "again":
            self.closed.emit()


class StepsPanel(QFrame):
    """The lecture as 3-5 minute study steps; a checkpoint card asks about a step when it ends."""

    seek_requested = Signal(int)
    make_requested = Signal()
    marked = Signal(int, str, str)  # step index, status, answer
    replay_requested = Signal(int)  # step index
    checkpoint_closed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StepsPanel")
        self.steps: list[dict] = []
        self.progress: dict[int, str] = {}

        self.status = QLabel()
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        self.make_button = QPushButton("Make steps")
        self.make_button.setToolTip("Cut the lecture into 3-5 minute steps with a check question each (Groq)")
        self.make_button.clicked.connect(self.make_requested)
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(self.make_button)
        self.mantra = QLabel()
        self.mantra.setObjectName("Mantra")
        self.mantra.setWordWrap(True)
        self.list = QListWidget()
        self.list.setObjectName("StepList")
        self.list.setWordWrap(True)
        self.list.itemClicked.connect(self._on_item)
        self.list.itemDoubleClicked.connect(lambda item: self.show_checkpoint(item.data(STEP_INDEX)))
        self.card = CheckpointCard()
        self.card.marked.connect(self._on_marked)
        self.card.replay_requested.connect(self.replay_requested)
        self.card.closed.connect(self.close_checkpoint)
        self.card.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(row)
        layout.addWidget(self.mantra)
        layout.addWidget(self.card)
        layout.addWidget(self.list, 1)
        self.set_steps([], "", {})

    def set_steps(self, steps: list[dict], mantra: str, progress: dict[int, str]) -> None:
        self.steps, self.progress = steps, dict(progress)
        self.mantra.setText(f"Mantra: {mantra}" if mantra else "")
        self.mantra.setVisible(bool(mantra))
        self.make_button.setText("Remake steps" if steps else "Make steps")
        self.card.hide()
        self._fill()
        self.set_busy(None)

    def _fill(self) -> None:
        self.list.clear()
        for index, step in enumerate(self.steps):
            mark = STATUS_MARK.get(self.progress.get(index, ""), TODO_MARK)
            item = QListWidgetItem(f"{mark}  {format_ms(step['start_ms'])}   {step['title']}")
            item.setData(STEP_INDEX, index)
            item.setToolTip(step.get("ek_line", "") + "\nDouble-click: checkpoint for this step")
            self.list.addItem(item)

    def set_busy(self, text: str | None) -> None:
        self.make_button.setEnabled(text is None)
        if text is not None:
            self.status.setText(text)
        elif self.steps:
            done = sum(1 for status in self.progress.values() if status == "done")
            self.status.setText(f"{done} of {len(self.steps)} steps samajh aaye. Click: jump, double-click: check.")
        else:
            self.status.setText("No steps yet. They need the transcript first.")

    def set_can_make(self, can_make: bool) -> None:
        self.make_button.setEnabled(can_make)

    def highlight(self, index: int) -> None:
        if 0 <= index < self.list.count() and self.list.currentRow() != index:
            self.list.setCurrentRow(index)

    def show_checkpoint(self, index: int) -> None:
        if 0 <= index < len(self.steps):
            self.card.show_step(index, len(self.steps), self.steps[index])
            self.highlight(index)

    def checkpoint_open(self) -> bool:
        return not self.card.isHidden()

    def close_checkpoint(self) -> None:
        self.card.hide()
        self.checkpoint_closed.emit()

    def _on_item(self, item: QListWidgetItem) -> None:
        index = item.data(STEP_INDEX)
        if 0 <= index < len(self.steps):
            self.seek_requested.emit(self.steps[index]["start_ms"])

    def _on_marked(self, index: int, status: str, answer: str) -> None:
        self.progress[index] = status
        self._fill()
        self.highlight(index)
        self.set_busy(None)
        self.marked.emit(index, status, answer)
