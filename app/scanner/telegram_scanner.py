import os
import re

from ..config import SUBTITLE_EXTENSIONS, VIDEO_EXTENSIONS
from ..models import ScannedVideo
from . import match_subtitle

_HASHTAG = re.compile(r"#(\w+)")


def section_from_caption(caption: str | None) -> str:
    match = _HASHTAG.search(caption or "")
    return match.group(1).replace("_", " ") if match else ""


def title_from_message(caption: str | None, file_name: str | None, msg_id: int) -> str:
    for line in (caption or "").splitlines():
        line = _HASHTAG.sub("", line).strip()
        if line:
            return line[:120]
    if file_name:
        return os.path.splitext(file_name)[0]
    return f"Video {msg_id}"


async def scan_channel(client, chat_id: int) -> list[ScannedVideo]:
    """Oldest message first; section = first #hashtag in the caption."""
    found: list[tuple[str, ScannedVideo]] = []
    subtitles: dict[str, str] = {}
    async for msg in client.iter_messages(chat_id, reverse=True):
        file = msg.file
        if file is None or msg.gif or msg.sticker:
            continue
        name = file.name or ""
        stem, ext = os.path.splitext(name)
        ext = ext.lower()
        if ext in SUBTITLE_EXTENSIONS:
            subtitles.setdefault(stem.lower(), f"{msg.id}{ext}")
            continue
        mime = file.mime_type or ""
        if not (msg.video or mime.startswith("video/") or ext in VIDEO_EXTENSIONS):
            continue
        caption = msg.message or ""
        found.append((stem, ScannedVideo(
            section=section_from_caption(caption),
            title=title_from_message(caption, name, msg.id),
            ref=str(msg.id),
            duration_ms=int((file.duration or 0) * 1000),
            size=file.size or 0,
        )))

    for stem, video in found:
        if stem:
            video.subtitle_ref = match_subtitle(stem, subtitles)
    return [video for _, video in found]
