"""Export a course's notes as one Markdown file (lecture by lecture, with timestamps)."""
from datetime import date

from .config import display_title, format_ms


def notes_markdown(course_name: str, items, lecture_numbers: dict[int, int]) -> str:
    """items: (Video, Note) pairs in course order; lecture_numbers: video id -> 1-based lecture number."""
    lines = [f"# {course_name}", "", f"_Notes exported from CourseHub on {date.today().isoformat()}_", ""]
    section = video_id = None
    for video, note in items:
        if video.section != section:
            section = video.section
            if section:
                lines += [f"## {section}", ""]
        if video.id != video_id:
            video_id = video.id
            number = lecture_numbers.get(video.id)
            prefix = f"{number}. " if number else ""
            lines += [f"### {prefix}{display_title(video.title)}", ""]
        lines += [f"**[{format_ms(note.timestamp_ms)}]**", "", note.text.rstrip(), ""]
    if video_id is None:
        lines += ["_No notes yet._", ""]
    return "\n".join(lines)


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def revision_questions(steps_data: dict, open_quiz: dict | None) -> list[tuple[str, str]]:
    """(question, answer) pairs of a lecture: its study-step checks, else its own-words questions."""
    pairs = [(_one_line(s["check"]["q"]), _one_line(s["check"].get("a", "")))
             for s in steps_data.get("steps") or [] if s.get("check")]
    if not pairs:
        pairs = [(_one_line(q["q"]), _one_line(q.get("a", ""))) for q in (open_quiz or {}).get("open") or []]
    return [(q, a) for q, a in pairs if q]


def revision_markdown(number: int, title: str, pairs: list[tuple[str, str]], mistakes: list[dict],
                      one_line: str) -> str:
    """One lecture in the 4-section format review.py parses (Q1: / A1: lines with blank lines between)."""
    lines = [f"# Lecture {number} — {display_title(title)}", "",
             f"📅 {date.today().isoformat()} | 🔁 Revise: performance-based", "", "## 1️⃣ QUESTIONS", ""]
    for index, (question, _answer) in enumerate(pairs, 1):
        lines += [f"Q{index}: {question}", ""]
    lines += ["## 2️⃣ ANSWERS", ""]
    for index, (_question, answer) in enumerate(pairs, 1):
        lines += [f"A{index}: {answer or '(lecture me dekho)'}", ""]
    lines += ["## 3️⃣ MERI GALTI", ""]
    if mistakes:
        lines += [f"- {_one_line(m['question'])} → sahi: {_one_line(m.get('correct')) or '-'} "
                  f"(Baar {m.get('wrong_count', 1)})" for m in mistakes]
    else:
        lines.append("Abhi koi nahi")
    lines += ["", "## 4️⃣ EK LINE ME", "", _one_line(one_line) or "-", ""]
    return "\n".join(lines)


def revision_file_name(number: int, title: str) -> str:
    safe = "".join(c for c in display_title(title) if c not in '<>:"/\\|?*').strip()[:60] or "lecture"
    return f"lecture{number:02d} - {safe}.md"
