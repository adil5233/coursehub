from app.db import Database
from app.models import ScannedVideo
from app.notes_export import notes_markdown


def test_course_notes_markdown(tmp_path):
    db = Database(tmp_path / "data.db")
    course_id = db.add_course("Python Basics", "local", str(tmp_path), [
        ScannedVideo("Week 1", "01 Intro.mp4", "a", 0),
        ScannedVideo("Week 1", "02 Loops.mp4", "b", 0),
        ScannedVideo("Week 2", "03 Decorators.mp4", "c", 0),
    ])
    intro, loops, decorators = db.get_videos(course_id)
    db.add_note(decorators.id, 65_000, "Decorator wrap karta hai\n```\n@timer\ndef f(): ...\n```")
    db.add_note(intro.id, 125_000, "Setup video")
    db.add_note(intro.id, 5_000, "Course 7 weeks ka hai")
    other = db.add_course("Other", "local", str(tmp_path), [ScannedVideo("", "x.mp4", "x", 0)])
    db.add_note(db.get_videos(other)[0].id, 0, "not exported")

    items = db.course_notes(course_id)
    assert [n.text.split("\n")[0] for _v, n in items] == [
        "Course 7 weeks ka hai", "Setup video", "Decorator wrap karta hai"]

    numbers = {v.id: i for i, v in enumerate(db.get_videos(course_id), 1)}
    text = notes_markdown("Python Basics", items, numbers)
    assert text.startswith("# Python Basics\n")
    order = [text.index(s) for s in ("## Week 1", "### 1. 01 Intro", "**[0:05]**", "**[2:05]**", "## Week 2",
                                     "### 3. 03 Decorators", "**[1:05]**", "```\n@timer")]
    assert order == sorted(order)
    assert "02 Loops" not in text and "not exported" not in text  # lectures without notes are skipped
    assert "_No notes yet._" in notes_markdown("Empty", [], {})
    db.close()
