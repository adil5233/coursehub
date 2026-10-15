import os
import re

from ..config import SUBTITLE_EXTENSIONS, VIDEO_EXTENSIONS, natural_sort_key
from ..models import ScannedVideo
from . import match_subtitle

FOLDER_MIME = "application/vnd.google-apps.folder"
SHARED_WITH_ME = "shared"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_FIELDS = "nextPageToken, files(id, name, mimeType, size, videoMediaMetadata(durationMillis))"


def is_valid_id(file_id: str) -> bool:
    return file_id == "root" or bool(_ID_RE.match(file_id))


def parse_folder_id(text: str) -> str | None:
    text = text.strip()
    match = re.search(r"/folders/([A-Za-z0-9_-]+)", text) or re.search(r"[?&]id=([A-Za-z0-9_-]+)", text)
    candidate = match.group(1) if match else text
    return candidate if is_valid_id(candidate) else None


def _list(service, query: str) -> list[dict]:
    files: list[dict] = []
    token = None
    while True:
        resp = service.files().list(
            q=query, fields=_FIELDS, pageSize=1000, pageToken=token,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            return files


def list_children(service, folder_id: str, folders_only: bool = False) -> list[dict]:
    if folder_id == SHARED_WITH_ME:
        query = "sharedWithMe = true and trashed = false"
    elif is_valid_id(folder_id):
        # Id is validated above, so it is safe to embed in the query string.
        query = f"'{folder_id}' in parents and trashed = false"
    else:
        raise ValueError(f"Invalid Google Drive folder id: {folder_id}")
    if folders_only:
        query += f" and mimeType = '{FOLDER_MIME}'"
    return sorted(_list(service, query), key=lambda f: natural_sort_key(f["name"]))


def list_subfolders(service, folder_id: str) -> list[tuple[str, str]]:
    return [(f["id"], f["name"]) for f in list_children(service, folder_id, folders_only=True)]


def get_folder_name(service, folder_id: str) -> str:
    if not is_valid_id(folder_id):
        raise ValueError(f"Invalid Google Drive folder id: {folder_id}")
    meta = service.files().get(fileId=folder_id, fields="name, mimeType", supportsAllDrives=True).execute()
    if meta.get("mimeType") != FOLDER_MIME:
        raise ValueError("This link points to a file, not a folder.")
    return meta["name"]


def scan_drive(service, folder_id: str, section: str = "") -> list[ScannedVideo]:
    children = list_children(service, folder_id)
    subtitles: dict[str, str] = {}
    for f in children:
        stem, ext = os.path.splitext(f["name"])
        if ext.lower() in SUBTITLE_EXTENSIONS:
            subtitles.setdefault(stem.lower(), f"{f['id']}{ext.lower()}")

    results: list[ScannedVideo] = []
    folders: list[dict] = []
    for f in children:
        if f["mimeType"] == FOLDER_MIME:
            folders.append(f)
            continue
        stem, ext = os.path.splitext(f["name"])
        if not (f["mimeType"].startswith("video/") or ext.lower() in VIDEO_EXTENSIONS):
            continue
        duration = int((f.get("videoMediaMetadata") or {}).get("durationMillis") or 0)
        results.append(ScannedVideo(
            section=section,
            title=stem,
            ref=f["id"],
            duration_ms=duration,
            size=int(f.get("size") or 0),
            subtitle_ref=match_subtitle(stem, subtitles),
        ))
    for folder in folders:
        child_section = f"{section} / {folder['name']}" if section else folder["name"]
        results.extend(scan_drive(service, folder["id"], child_section))
    return results
