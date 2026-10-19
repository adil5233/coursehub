import json
import sqlite3
from datetime import date
from pathlib import Path

from .models import Course, CourseStats, Note, ScannedVideo, Video

SOURCES = ("local", "drive", "telegram")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    root TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_video_id INTEGER
);
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    section TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    ref TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    size INTEGER NOT NULL DEFAULT 0,
    subtitle_ref TEXT,
    last_position_ms INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    missing INTEGER NOT NULL DEFAULT 0,
    UNIQUE (course_id, ref)
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    timestamp_ms INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS quizzes (
    video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    questions TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reviews (
    video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    due_date TEXT NOT NULL,
    interval_days REAL NOT NULL DEFAULT 1,
    last_score REAL,
    attempts INTEGER NOT NULL DEFAULT 0
);
"""

_VIDEO_COLUMNS = (
    "id, course_id, section, title, ref, sort_order, duration_ms, size, "
    "subtitle_ref, last_position_ms, completed"
)


def _course(row: sqlite3.Row) -> Course:
    return Course(row["id"], row["name"], row["source"], row["root"], row["created_at"], row["last_video_id"])


def _video(row: sqlite3.Row) -> Video:
    return Video(
        row["id"], row["course_id"], row["section"], row["title"], row["ref"], row["sort_order"],
        row["duration_ms"], row["size"], row["subtitle_ref"], row["last_position_ms"], bool(row["completed"]),
    )


class Database:
    def __init__(self, path: str | Path) -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(courses)")}
        if "last_watched_at" not in columns:
            with self.conn:
                self.conn.execute("ALTER TABLE courses ADD COLUMN last_watched_at TEXT")

    def close(self) -> None:
        self.conn.close()

    # ---- courses ----
    def add_course(self, name: str, source: str, root: str, videos: list[ScannedVideo]) -> int:
        if source not in SOURCES:
            raise ValueError(f"Unknown source: {source}")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO courses (name, source, root) VALUES (?, ?, ?)", (name, source, root)
            )
            course_id = cur.lastrowid
            self._sync(course_id, videos)
        return course_id

    def sync_videos(self, course_id: int, videos: list[ScannedVideo]) -> tuple[int, int]:
        """Merge a fresh scan into the course; returns (added, removed). Progress is kept by ref."""
        with self.conn:
            return self._sync(course_id, videos)

    def _sync(self, course_id: int, videos: list[ScannedVideo]) -> tuple[int, int]:
        rows = self.conn.execute(
            "SELECT id, ref, missing FROM videos WHERE course_id = ?", (course_id,)
        ).fetchall()
        existing = {row["ref"]: row for row in rows}
        seen: set[str] = set()
        added = 0
        for order, v in enumerate(videos):
            if v.ref in seen:
                continue
            seen.add(v.ref)
            row = existing.get(v.ref)
            if row is not None:
                self.conn.execute(
                    """UPDATE videos SET section = ?, title = ?, sort_order = ?, size = ?, subtitle_ref = ?,
                       missing = 0, duration_ms = CASE WHEN ? > 0 THEN ? ELSE duration_ms END
                       WHERE id = ?""",
                    (v.section, v.title, order, v.size, v.subtitle_ref, v.duration_ms, v.duration_ms, row["id"]),
                )
            else:
                self.conn.execute(
                    """INSERT INTO videos (course_id, section, title, ref, sort_order, duration_ms, size, subtitle_ref)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (course_id, v.section, v.title, v.ref, order, v.duration_ms, v.size, v.subtitle_ref),
                )
                added += 1
        removed = [(row["id"],) for ref, row in existing.items() if ref not in seen and not row["missing"]]
        self.conn.executemany("UPDATE videos SET missing = 1 WHERE id = ?", removed)
        return added, len(removed)

    def list_courses(self) -> list[Course]:
        return [_course(r) for r in self.conn.execute("SELECT * FROM courses ORDER BY id DESC")]

    def get_course(self, course_id: int) -> Course | None:
        row = self.conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        return _course(row) if row else None

    def rename_course(self, course_id: int, name: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE courses SET name = ? WHERE id = ?", (name, course_id))

    def delete_course(self, course_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))

    def reset_progress(self, course_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE videos SET completed = 0, completed_at = NULL, last_position_ms = 0 WHERE course_id = ?",
                (course_id,),
            )
            self.conn.execute("UPDATE courses SET last_video_id = NULL WHERE id = ?", (course_id,))

    def set_last_video(self, course_id: int, video_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE courses SET last_video_id = ?, last_watched_at = CURRENT_TIMESTAMP WHERE id = ?",
                (video_id, course_id),
            )

    def continue_watching(self, limit: int = 8) -> list[tuple[Course, Video]]:
        """Recently watched courses with the lecture to resume (the next one if the last was finished)."""
        rows = self.conn.execute(
            """SELECT * FROM courses WHERE last_video_id IS NOT NULL
               ORDER BY last_watched_at IS NULL, last_watched_at DESC, id DESC"""
        ).fetchall()
        result: list[tuple[Course, Video]] = []
        for row in rows:
            course = _course(row)
            videos = self.get_videos(course.id)
            index = next((i for i, v in enumerate(videos) if v.id == course.last_video_id), None)
            if index is None:
                continue
            video = videos[index]
            if video.completed:
                video = next((v for v in videos[index + 1:] + videos[:index] if not v.completed), None)
            if video is not None:
                result.append((course, video))
            if len(result) >= limit:
                break
        return result

    def course_stats(self, course_id: int) -> CourseStats:
        row = self.conn.execute(
            """SELECT COUNT(*) AS total,
                      COALESCE(SUM(completed), 0) AS done,
                      COALESCE(SUM(duration_ms), 0) AS total_ms,
                      COALESCE(SUM(CASE WHEN completed THEN duration_ms
                                        ELSE MIN(last_position_ms, duration_ms) END), 0) AS watched_ms
               FROM videos WHERE course_id = ? AND missing = 0""",
            (course_id,),
        ).fetchone()
        return CourseStats(row["total"], row["done"], row["total_ms"], row["watched_ms"])

    # ---- videos ----
    def get_videos(self, course_id: int) -> list[Video]:
        rows = self.conn.execute(
            f"SELECT {_VIDEO_COLUMNS} FROM videos WHERE course_id = ? AND missing = 0 ORDER BY sort_order",
            (course_id,),
        )
        return [_video(r) for r in rows]

    def get_video(self, video_id: int) -> Video | None:
        row = self.conn.execute(f"SELECT {_VIDEO_COLUMNS} FROM videos WHERE id = ?", (video_id,)).fetchone()
        return _video(row) if row else None

    def save_position(self, video_id: int, position_ms: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE videos SET last_position_ms = ? WHERE id = ?", (int(position_ms), video_id))
            self.conn.execute(
                "UPDATE courses SET last_watched_at = CURRENT_TIMESTAMP "
                "WHERE id = (SELECT course_id FROM videos WHERE id = ?)",
                (video_id,),
            )

    def set_duration(self, video_id: int, duration_ms: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE videos SET duration_ms = ? WHERE id = ?", (int(duration_ms), video_id))

    def set_completed(self, video_id: int, completed: bool) -> None:
        with self.conn:
            if completed:
                self.conn.execute(
                    """UPDATE videos SET completed = 1, completed_at = CURRENT_TIMESTAMP, last_position_ms = 0
                       WHERE id = ?""",
                    (video_id,),
                )
            else:
                self.conn.execute("UPDATE videos SET completed = 0, completed_at = NULL WHERE id = ?", (video_id,))

    # ---- notes ----
    def add_note(self, video_id: int, timestamp_ms: int, text: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO notes (video_id, timestamp_ms, text) VALUES (?, ?, ?)",
                (video_id, int(timestamp_ms), text),
            )
        return cur.lastrowid

    def list_notes(self, video_id: int) -> list[Note]:
        rows = self.conn.execute(
            "SELECT id, video_id, timestamp_ms, text FROM notes WHERE video_id = ? ORDER BY timestamp_ms, id",
            (video_id,),
        )
        return [Note(r["id"], r["video_id"], r["timestamp_ms"], r["text"]) for r in rows]

    def update_note(self, note_id: int, text: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE notes SET text = ? WHERE id = ?", (text, note_id))

    def delete_note(self, note_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    def course_notes(self, course_id: int) -> list[tuple[Video, Note]]:
        """Every note of a course, in lecture order then time order."""
        rows = self.conn.execute(
            f"""SELECT {', '.join('v.' + c.strip() for c in _VIDEO_COLUMNS.split(','))},
                       n.id AS note_id, n.timestamp_ms, n.text
                FROM notes n JOIN videos v ON v.id = n.video_id
                WHERE v.course_id = ? AND v.missing = 0
                ORDER BY v.sort_order, n.timestamp_ms, n.id""",
            (course_id,),
        )
        return [(_video(r), Note(r["note_id"], r["id"], r["timestamp_ms"], r["text"])) for r in rows]

    def lecture_index(self) -> dict[int, tuple[Course, Video, int]]:
        """video id -> (course, video, 1-based lecture number) for every lecture in the library."""
        index: dict[int, tuple[Course, Video, int]] = {}
        for course in self.list_courses():
            for number, video in enumerate(self.get_videos(course.id), 1):
                index[video.id] = (course, video, number)
        return index

    def all_notes(self) -> list[Note]:
        rows = self.conn.execute("SELECT id, video_id, timestamp_ms, text FROM notes ORDER BY video_id, timestamp_ms")
        return [Note(r["id"], r["video_id"], r["timestamp_ms"], r["text"]) for r in rows]

    # ---- quizzes and revision ----
    def get_quiz(self, video_id: int) -> list[dict] | None:
        row = self.conn.execute("SELECT questions FROM quizzes WHERE video_id = ?", (video_id,)).fetchone()
        return json.loads(row["questions"]) if row else None

    def save_quiz(self, video_id: int, questions: list[dict]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO quizzes (video_id, questions) VALUES (?, ?) ON CONFLICT(video_id) DO UPDATE SET "
                "questions = excluded.questions, created_at = CURRENT_TIMESTAMP",
                (video_id, json.dumps(questions, ensure_ascii=False)),
            )

    def get_review(self, video_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM reviews WHERE video_id = ?", (video_id,)).fetchone()
        return dict(row) if row else None

    def ensure_review(self, video_id: int, due: date) -> None:
        """First revision for a finished lecture (an existing schedule is kept)."""
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO reviews (video_id, due_date, interval_days) VALUES (?, ?, 1)",
                (video_id, due.isoformat()),
            )

    def record_review(self, video_id: int, score: float, interval: float, due: date) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO reviews (video_id, due_date, interval_days, last_score, attempts)
                   VALUES (?, ?, ?, ?, 1)
                   ON CONFLICT(video_id) DO UPDATE SET due_date = excluded.due_date,
                   interval_days = excluded.interval_days, last_score = excluded.last_score,
                   attempts = reviews.attempts + 1""",
                (video_id, due.isoformat(), interval, score),
            )

    def due_reviews(self, today: date) -> list[tuple[Course, Video, dict]]:
        """Lectures whose revision is due (oldest first)."""
        rows = self.conn.execute(
            """SELECT r.* FROM reviews r JOIN videos v ON v.id = r.video_id
               WHERE r.due_date <= ? AND v.missing = 0 ORDER BY r.due_date, v.course_id, v.sort_order""",
            (today.isoformat(),),
        ).fetchall()
        result = []
        for row in rows:
            video = self.get_video(row["video_id"])
            course = self.get_course(video.course_id) if video else None
            if course is not None:
                result.append((course, video, dict(row)))
        return result

    # ---- app state ----
    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str | None) -> None:
        with self.conn:
            if value is None:
                self.conn.execute("DELETE FROM app_state WHERE key = ?", (key,))
            else:
                self.conn.execute(
                    "INSERT INTO app_state (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
