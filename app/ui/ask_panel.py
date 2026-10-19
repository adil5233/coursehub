import html
import re

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout

from ..config import format_ms
from ..transcribe import groq_client, load_transcript
from ..transcribe.ask import LectureAsker, Passage, passages, time_passage

_CITATION = re.compile(r"\[C(\d+)\]")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


class AskPanel(QFrame):
    """Ask questions about the lecture (or the whole course); answers cite clickable moments."""

    jump_requested = Signal(int, int)  # video_id, time_ms
    typing_started = Signal()  # the learner started a question: the player pauses

    def __init__(self, db=None, time_provider=None, step_provider=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AskPanel")
        self.db = db
        self.time_provider = time_provider  # where the player is now (ms); None: no current-moment context
        self.step_provider = step_provider or (lambda _ms: "")  # study step at a time, for context
        self._typing = False
        self._asked_at = 0
        self._video = None
        self._lectures: list = []  # (video, lecture number) of the open course
        self._html: list[str] = []
        self.asker = LectureAsker(self)
        self.asker.progress.connect(lambda text: self.status.setText(text))
        self.asker.finished.connect(self._on_answer)
        self.asker.failed.connect(self._on_failed)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask about this lecture... (e.g. weights kaise update hote hain?)")
        self.input.setClearButtonEnabled(True)
        self.input.returnPressed.connect(self._ask)
        self.input.textEdited.connect(self._on_typed)
        self.ask_button = QPushButton("Ask")
        self.ask_button.setObjectName("Primary")
        self.ask_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ask_button.clicked.connect(self._ask)
        self.explain_button = QPushButton("Abhi wala samjhao")
        self.explain_button.setToolTip("Didn't get what was just said? Explains the last ~90 seconds simply")
        self.explain_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.explain_button.clicked.connect(self.explain_now)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.ask_button)
        row.addWidget(self.explain_button)
        self.scope = QCheckBox("Search the whole course")
        self.scope.setToolTip("Use the transcripts of every lecture in this course, not only the open one")
        self.status = QLabel("Answers come only from the lecture's transcript; click a time to jump there.")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        self.answers = QTextBrowser()
        self.answers.setObjectName("AskAnswers")
        self.answers.setOpenLinks(False)
        self.answers.anchorClicked.connect(self._on_link)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(row)
        layout.addWidget(self.scope)
        layout.addWidget(self.status)
        layout.addWidget(self.answers, 1)

    def set_lecture(self, video, lectures: list) -> None:
        """lectures: (video, number) for every lecture of the open course."""
        changed = getattr(video, "id", None) != getattr(self._video, "id", None)
        self._video, self._lectures = video, lectures
        if changed:
            self._load_history()

    def _load_history(self) -> None:
        """Doubts asked about this lecture earlier, so they are not lost when it is opened again."""
        self._html = []
        if self.db is not None and self._video is not None:
            for doubt in self.db.list_doubts(self._video.id):
                found = [Passage(s["video_id"], s["start_ms"], "", s.get("label", "")) for s in doubt["sources"]]
                self._html.append(self.render(doubt["question"], doubt["answer"], found))
        self.answers.setHtml("<hr>".join(self._html))
        self.answers.verticalScrollBar().setValue(self.answers.verticalScrollBar().maximum())

    def _on_typed(self, text: str) -> None:
        if text and not self._typing:
            self._typing = True
            self.typing_started.emit()
        elif not text:
            self._typing = False

    def _now_ms(self) -> int:
        return int(self.time_provider()) if self.time_provider is not None else 0

    def _now_passage(self, video_id: int, segments) -> Passage | None:
        if self.time_provider is None:
            return None
        return time_passage(video_id, segments, self._now_ms())

    def _numbers(self) -> dict[int, int]:
        return {video.id: number for video, number in self._lectures}

    def _items(self):
        targets = [video for video, _n in self._lectures] if self.scope.isChecked() else [self._video]
        numbers = self._numbers()
        items = []
        for video in targets:
            segments = load_transcript(video.id) if video is not None else None
            if segments:
                label = f"Lecture {numbers.get(video.id, '?')}" if self.scope.isChecked() else ""
                items.extend(passages(video.id, segments, label))
        return items

    def _ask(self) -> None:
        question = self.input.text().strip()
        if not question or self._video is None or self.asker.busy:
            return
        if not groq_client.load_settings()["api_key"]:
            self.status.setText("Paste your Groq API key in Settings \u2192 Transcripts first.")
            return
        items = self._items()
        if not items:
            self.status.setText("No transcript yet. Make it first: Transcript tab \u2192 Generate transcript."
                                if not self.scope.isChecked() else "No lecture of this course has a transcript yet.")
            return
        segments = load_transcript(self._video.id) or []
        now = self._now_passage(self._video.id, segments)
        if now is not None and self.scope.isChecked():
            numbers = self._numbers()
            now.label = f"Lecture {numbers.get(self._video.id, '?')}"
        self._start(question, items, now, explain=False)

    def explain_now(self) -> None:
        """"Abhi wala samjhao": explain what was said in the last ~90 seconds (a typed question is added)."""
        if self._video is None or self.asker.busy:
            return
        if not groq_client.load_settings()["api_key"]:
            self.status.setText("Paste your Groq API key in Settings \u2192 Transcripts first.")
            return
        now = self._now_passage(self._video.id, load_transcript(self._video.id) or [])
        if now is None:
            self.status.setText("No transcript here yet. Make it first: Transcript tab \u2192 Generate transcript.")
            return
        self.typing_started.emit()
        self._start(self.input.text().strip(), [], now, explain=True)

    def _start(self, question: str, items: list, now, explain: bool) -> None:
        self._asked_at = self._now_ms()
        self.ask_button.setEnabled(False)
        self.explain_button.setEnabled(False)
        self.status.setText("Finding the right parts of the transcript..." if not explain else "Thinking...")
        self.asker.start(question, items, format_ms, now, explain, self.step_provider(self._asked_at))

    def _on_failed(self, message: str) -> None:
        self.ask_button.setEnabled(True)
        self.explain_button.setEnabled(True)
        self.status.setText(message)

    def _on_answer(self, question: str, reply: str, found: list) -> None:
        self.ask_button.setEnabled(True)
        self.explain_button.setEnabled(True)
        self._typing = False
        question = question or f"Abhi wala samjhao ({format_ms(self._asked_at)})"
        if self.db is not None and self._video is not None:
            sources = [{"video_id": p.video_id, "start_ms": p.start_ms, "label": p.label} for p in found]
            self.db.add_doubt(self._video.id, self._asked_at, question, reply, sources)
        self.input.clear()
        self.status.setText("Click a time to jump there.")
        self._html.append(self.render(question, reply, found))
        self.answers.setHtml("<hr>".join(self._html))
        self.answers.verticalScrollBar().setValue(self.answers.verticalScrollBar().maximum())

    def render(self, question: str, reply: str, found: list) -> str:
        """Answer as HTML; [C2] citations become links to that moment."""
        numbers = self._numbers()

        def link(passage) -> str:
            lecture = f"L{numbers.get(passage.video_id, '?')} " if passage.label else ""
            return (f'<a href="jump:{passage.video_id}:{passage.start_ms}">'
                    f"{lecture}{format_ms(passage.start_ms)}</a>")

        def cite(match) -> str:
            index = int(match.group(1)) - 1
            return f"[{link(found[index])}]" if 0 <= index < len(found) else ""

        body = _CITATION.sub(cite, html.escape(reply.replace("\u0964", ".")))
        body = _BOLD.sub(r"<b>\1</b>", body).replace("\n", "<br>")
        sources = "  ".join(link(p) for p in found)
        return (f"<p><b>Q: {html.escape(question)}</b></p><p>{body}</p>"
                + (f'<p style="color:#aaaaaa">Sources: {sources}</p>' if found else ""))

    def _on_link(self, url: QUrl) -> None:
        parts = url.toString().split(":")
        if len(parts) == 3 and parts[0] == "jump":
            self.jump_requested.emit(int(parts[1]), int(parts[2]))
