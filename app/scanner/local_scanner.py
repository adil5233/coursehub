import os
from pathlib import Path

from ..config import SUBTITLE_EXTENSIONS, VIDEO_EXTENSIONS, natural_sort_key
from ..models import ScannedVideo
from . import match_subtitle


def scan_local(root: str) -> list[ScannedVideo]:
    root_path = Path(root)
    if not root_path.is_dir():
        raise FileNotFoundError(f"Folder not found: {root}")

    results: list[ScannedVideo] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        # Sorting in place controls the order os.walk visits subfolders.
        dirnames[:] = sorted((d for d in dirnames if not d.startswith(".")), key=natural_sort_key)
        rel_dir = Path(dirpath).relative_to(root_path)
        section = "" if rel_dir == Path(".") else " / ".join(rel_dir.parts)

        subtitles = {
            Path(name).stem.lower(): (rel_dir / name).as_posix()
            for name in filenames
            if Path(name).suffix.lower() in SUBTITLE_EXTENSIONS
        }
        for name in sorted(filenames, key=natural_sort_key):
            path = Path(name)
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                size = (Path(dirpath) / name).stat().st_size
            except OSError:
                size = 0
            results.append(ScannedVideo(
                section=section,
                title=path.stem,
                ref=(rel_dir / name).as_posix(),
                size=size,
                subtitle_ref=match_subtitle(path.stem, subtitles),
            ))
    return results
