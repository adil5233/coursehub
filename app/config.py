import os
import re
from pathlib import Path

APP_NAME = "CourseHub"
DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "data.db"
DRIVE_CREDENTIALS = DATA_DIR / "credentials.json"
DRIVE_TOKEN = DATA_DIR / "token.json"
TELEGRAM_CONFIG = DATA_DIR / "telegram.json"
TELEGRAM_SESSION = DATA_DIR / "telegram"  # Telethon appends ".session"
GROQ_CONFIG = DATA_DIR / "groq.json"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"  # not under cache: "Clear cache" must not delete them

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".m4v", ".mkv", ".avi", ".mov", ".qt", ".wmv", ".asf", ".flv", ".f4v",
    ".webm", ".mpg", ".mpeg", ".mpe", ".m2v", ".3gp", ".3g2", ".ts", ".mts", ".m2ts",
    ".vob", ".ogv", ".ogm", ".rm", ".rmvb", ".divx", ".dv", ".mxf", ".h264", ".hevc",
})
SUBTITLE_EXTENSIONS = frozenset({".srt", ".vtt", ".ass", ".ssa", ".sub"})

SAVE_INTERVAL_MS = 5000
AUTOPLAY_DELAY_S = 3
UP_NEXT_LEAD_MS = 10_000
COMPLETE_TOLERANCE_MS = 1000
SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)


def write_text_atomic(path: Path, text: str) -> None:
    """Write via a temp file + rename, so a crash mid-write never leaves a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def ensure_dirs() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)


def natural_sort_key(text: str) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def display_title(title: str) -> str:
    stem, ext = os.path.splitext(title)
    return stem if ext.lower() in VIDEO_EXTENSIONS and stem else title


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def format_ms(ms: int) -> str:
    seconds = max(0, int(ms)) // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"
