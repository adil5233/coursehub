import json
import threading
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from ..config import DRIVE_CREDENTIALS, DRIVE_TOKEN, write_text_atomic

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
LOGIN_TIMEOUT_S = 300


def _save(creds: Credentials) -> None:
    write_text_atomic(DRIVE_TOKEN, creds.to_json())


def has_client_secrets() -> bool:
    return DRIVE_CREDENTIALS.is_file()


def install_client_secrets(source: str | Path) -> None:
    data = json.loads(Path(source).read_text(encoding="utf-8"))
    if "installed" not in data:
        raise ValueError(
            "This is not a Desktop-app OAuth client file.\n"
            "In Google Cloud Console create an OAuth client of type 'Desktop app' and download its JSON."
        )
    DRIVE_CREDENTIALS.write_text(json.dumps(data), encoding="utf-8")


def load_saved() -> Credentials | None:
    if not DRIVE_TOKEN.is_file():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(DRIVE_TOKEN), SCOPES)
    except ValueError:
        return None
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            return None
        _save(creds)
        return creds
    return None


def login() -> Credentials:
    """Blocking browser login; run it off the GUI thread."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(DRIVE_CREDENTIALS), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=LOGIN_TIMEOUT_S)
    if creds is None or not creds.token:
        raise RuntimeError("Google sign-in was not completed.")
    _save(creds)
    return creds


class DriveSession:
    def __init__(self, creds: Credentials) -> None:
        self._creds = creds
        self._lock = threading.Lock()

    def token(self, force_refresh: bool = False) -> str:
        with self._lock:
            if force_refresh or not self._creds.valid:
                self._creds.refresh(Request())
                _save(self._creds)
            return self._creds.token

    def service(self):
        # googleapiclient service objects are not thread-safe, so build one per caller.
        from googleapiclient.discovery import build

        return build("drive", "v3", credentials=self._creds, cache_discovery=False)
