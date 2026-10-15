import sqlite3
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
            self.conn.execute("UPDATE courses SET last_video_id = ? WHERE id = ?", (video_id, course_id))

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
