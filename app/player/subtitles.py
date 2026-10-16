"""Minimal SRT / WebVTT reader so the player can draw subtitles itself (and move them around the controls)."""
import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

OVERLAY_FORMATS = frozenset({".srt", ".vtt"})
_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})")
_TAGS = re.compile(r"<[^>]*>|\{[^}]*\}")  # <i>, <font ...>, {\an8} ...


@dataclass
class Cue:
    start: int  # ms
    end: int
    text: str


def _ms(text: str) -> int | None:
    match = _TIME.search(text)
    if match is None:
        return None
    hours, minutes, seconds, fraction = match.groups()
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(fraction.ljust(3, "0"))


def parse_subtitles(path: str) -> list[Cue]:
    content = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    cues: list[Cue] = []
    for block in re.split(r"\r?\n\s*\r?\n", content):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            if "-->" not in line:
                continue
            left, right = line.split("-->", 1)
            start, end = _ms(left), _ms(right)
            body = _TAGS.sub("", "\n".join(l.strip() for l in lines[index + 1:] if l.strip())).strip()
            if start is not None and end is not None and body:
                cues.append(Cue(start, end, body))
            break
    cues.sort(key=lambda cue: cue.start)
    return cues


class CueTrack:
    def __init__(self, cues: list[Cue]) -> None:
        self.cues = cues
        self._starts = [cue.start for cue in cues]

    def text_at(self, position_ms: int) -> str:
        index = bisect_right(self._starts, position_ms) - 1
        if index >= 0 and position_ms < self.cues[index].end:
            return self.cues[index].text
        return ""
