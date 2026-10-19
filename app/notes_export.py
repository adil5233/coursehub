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
