import random
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from ..config import display_title, format_ms
from ..review import due_after, next_interval
from ..transcribe import groq_client, load_transcript
from ..transcribe.quiz import QuizMaker


class _Option(QFrame):
    """A clickable answer whose text wraps (QPushButton text cannot)."""

    clicked = Signal(int)

    def __init__(self, index: int, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("QuizOption")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.index = index
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setObjectName("QuizOptionText")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.addWidget(self.label)
        self.set_state("normal")

    def set_state(self, state: str) -> None:
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.clicked.emit(self.index)
        super().mouseReleaseEvent(event)


class QuizDialog(QDialog):
    """Revision quiz for one lecture; the score sets when it comes up for revision again."""

    jump_requested = Signal(int, int, int)  # course_id, video_id, time_ms

    def __init__(self, db, course, video, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Revision quiz")
        self.resize(720, 560)
        self.db, self.course, self.video = db, course, video
        self._maker: QuizMaker | None = None
        self._questions: list[dict] = []
        self._index = 0
        self._chosen: int | None = None
        self._correct = 0
        self._wrong: list[dict] = []

        title = QLabel(display_title(video.title))
        title.setObjectName("SectionTitle")
        title.setWordWrap(True)
        subtitle = QLabel(course.name)
        subtitle.setObjectName("Muted")
        self.pages = QStackedWidget()
        self.pages.addWidget(self._loading_page())
        self.pages.addWidget(self._question_page())
        self.pages.addWidget(self._result_page())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(6)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(8)
        layout.addWidget(self.pages, 1)
        self._load()

    # ---- pages ----
    def _loading_page(self) -> QWidget:
        page = QWidget()
        self.loading_label = QLabel()
        self.loading_label.setWordWrap(True)
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setFixedHeight(4)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        layout.addWidget(self.loading_label)
        layout.addWidget(self.loading_bar)
        layout.addStretch(1)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        return page

    def _question_page(self) -> QWidget:
        page = QWidget()
        self.counter = QLabel()
        self.counter.setObjectName("Muted")
        self.question = QLabel()
        self.question.setObjectName("QuizQuestion")
        self.question.setWordWrap(True)
        self.options = [_Option(i) for i in range(4)]
        for option in self.options:
            option.clicked.connect(self._choose)
        self.explain = QLabel()
        self.explain.setWordWrap(True)
        self.explain.setObjectName("QuizExplain")
        self.watch_button = QPushButton()
        self.watch_button.setObjectName("Ghost")
        self.watch_button.setToolTip("Close the quiz and play this part of the lecture")
        self.watch_button.clicked.connect(self._watch)
        self.next_button = QPushButton("Check")
        self.next_button.setObjectName("Primary")
        self.next_button.clicked.connect(self._next)
        buttons = QHBoxLayout()
        buttons.addWidget(self.watch_button)
        buttons.addStretch(1)
        buttons.addWidget(self.next_button)
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.addWidget(self.counter)
        layout.addWidget(self.question)
        for option in self.options:
            layout.addWidget(option)
        layout.addWidget(self.explain)
        layout.addStretch(1)
        layout.addLayout(buttons)
        return page

    def _result_page(self) -> QWidget:
        page = QWidget()
        self.score_label = QLabel()
        self.score_label.setObjectName("QuizScore")
        self.next_review_label = QLabel()
        self.next_review_label.setObjectName("Muted")
        self.wrong_label = QLabel()
        self.wrong_label.setWordWrap(True)
        self.wrong_label.setTextFormat(Qt.TextFormat.PlainText)
        again = QPushButton("Try again")
        again.setToolTip("Same questions in a new order")
        again.clicked.connect(self._start)
        fresh = QPushButton("New questions")
        fresh.setToolTip("Ask Groq for a fresh set of questions")
        fresh.clicked.connect(lambda: self._generate())
        done = QPushButton("Done")
        done.setObjectName("Primary")
        done.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(again)
        buttons.addWidget(fresh)
        buttons.addStretch(1)
        buttons.addWidget(done)
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.addWidget(self.score_label)
        layout.addWidget(self.next_review_label)
        layout.addSpacing(8)
        layout.addWidget(self.wrong_label)
        layout.addStretch(1)
        layout.addLayout(buttons)
        return page

    # ---- flow ----
    def _show_message(self, text: str, busy: bool = False) -> None:
        self.loading_label.setText(text)
        self.loading_bar.setVisible(busy)
        self.pages.setCurrentIndex(0)

    def _load(self) -> None:
        saved = self.db.get_quiz(self.video.id)
        if saved:
            self._questions = saved
            self._start()
        else:
            self._generate()

    def _generate(self) -> None:
        segments = load_transcript(self.video.id)
        if not segments:
            self._show_message("The quiz is made from the lecture's transcript.\n\n"
                               "Make the transcript first: Transcript tab \u2192 Generate transcript.")
            return
        if not groq_client.load_settings()["api_key"]:
            self._show_message("Quizzes use Groq's free API. Paste your key in Settings \u2192 Transcripts.")
            return
        self._show_message("Writing questions from the transcript...", busy=True)
        self._maker = QuizMaker(self)
        self._maker.progress.connect(lambda text: self.loading_label.setText(text))
        self._maker.finished.connect(self._on_generated)
        self._maker.failed.connect(lambda message: self._show_message(message))
        self._maker.start(segments)

    def _on_generated(self, questions: list) -> None:
        self.db.save_quiz(self.video.id, questions)
        self._questions = questions
        self._start()

    def _start(self) -> None:
        self._order = random.sample(range(len(self._questions)), len(self._questions))
        self._index, self._correct, self._wrong = 0, 0, []
        self._show_question()
        self.pages.setCurrentIndex(1)

    def _current(self) -> dict:
        return self._questions[self._order[self._index]]

    def _show_question(self) -> None:
        question = self._current()
        self._shuffled = random.sample(range(4), 4)  # display slot -> original option index
        self._chosen = None
        self.counter.setText(f"Question {self._index + 1} of {len(self._questions)}")
        self.question.setText(question["q"])
        for slot, option in enumerate(self.options):
            option.label.setText(question["options"][self._shuffled[slot]])
            option.set_state("normal")
        self.explain.hide()
        self.watch_button.hide()
        self.next_button.setText("Check")
        self.next_button.setEnabled(False)

    def _choose(self, slot: int) -> None:
        if self.next_button.text() != "Check":  # already answered: options stay readable but locked
            return
        self._chosen = slot
        for option in self.options:
            option.set_state("selected" if option.index == slot else "normal")
        self.next_button.setEnabled(True)

    def _next(self) -> None:
        if self.next_button.text() == "Check":
            self._check()
        elif self._index + 1 < len(self._questions):
            self._index += 1
            self._show_question()
        else:
            self._finish()

    def _check(self) -> None:
        question = self._current()
        right_slot = self._shuffled.index(question["answer"])
        correct = self._chosen == right_slot
        self._correct += correct
        if not correct:
            self._wrong.append(question)
        for option in self.options:
            if option.index == right_slot:
                option.set_state("correct")
            elif option.index == self._chosen:
                option.set_state("wrong")
        verdict = "Sahi! " if correct else "Galat. "
        self.explain.setText(verdict + question.get("explain", ""))
        self.explain.show()
        self.watch_button.setText(f"\u25b6  Watch this part ({format_ms(question.get('time_ms', 0))})")
        self.watch_button.show()
        last = self._index + 1 >= len(self._questions)
        self.next_button.setText("See result" if last else "Next")

    def _finish(self) -> None:
        total = len(self._questions)
        score = self._correct / total if total else 0.0
        review = self.db.get_review(self.video.id)
        interval = next_interval(review["interval_days"] if review else None, score)
        due = due_after(interval)
        self.db.record_review(self.video.id, score, interval, due)
        days = (due - date.today()).days
        self.score_label.setText(f"{self._correct} / {total} correct  ({round(score * 100)}%)")
        self.next_review_label.setText(
            f"Next revision: {due.strftime('%d %b')} (in {days} day{'s' if days != 1 else ''})")
        if self._wrong:
            lines = [f"\u2022 {q['q']}\n   \u2192 {q['options'][q['answer']]}" for q in self._wrong]
            self.wrong_label.setText("Revise these:\n\n" + "\n\n".join(lines))
        else:
            self.wrong_label.setText("Sab sahi! Bahut badhiya.")
        self.pages.setCurrentIndex(2)

    def _watch(self) -> None:
        self.jump_requested.emit(self.course.id, self.video.id, int(self._current().get("time_ms", 0)))
        self.accept()

    def done(self, result: int) -> None:
        if self._maker is not None:
            self._maker.cancel()
        super().done(result)
