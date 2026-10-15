from dataclasses import dataclass


@dataclass
class ScannedVideo:
    section: str
    title: str
    ref: str  # local relative path / Drive file id / Telegram message id
    duration_ms: int = 0
    size: int = 0
    subtitle_ref: str | None = None


@dataclass
class Course:
    id: int
    name: str
    source: str
    root: str
    created_at: str
    last_video_id: int | None


@dataclass
class CourseStats:
    total: int
    completed: int
    total_ms: int
    watched_ms: int

    @property
    def percent(self) -> int:
        return self.completed * 100 // self.total if self.total else 0


@dataclass
class Video:
    id: int
    course_id: int
    section: str
    title: str
    ref: str
    sort_order: int
    duration_ms: int
    size: int
    subtitle_ref: str | None
    last_position_ms: int
    completed: bool


@dataclass
class Note:
    id: int
    video_id: int
    timestamp_ms: int
    text: str
