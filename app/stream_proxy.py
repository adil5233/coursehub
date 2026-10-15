"""Local HTTP proxy so VLC can stream Drive/Telegram files (VLC cannot send auth headers itself)."""
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

DRIVE_MEDIA_URL = "https://www.googleapis.com/drive/v3/files/{}?alt=media&supportsAllDrives=true"
_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_BUFFER = 256 * 1024
_FORWARD_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        pass  # VLC drops keep-alive sockets constantly; nothing useful to report


class StreamProxy:
    def __init__(self) -> None:
        # Random path secret blocks other local apps / DNS-rebinding pages from using the proxy.
        self.secret = secrets.token_urlsafe(16)
        self.drive_token = None  # callable(force_refresh: bool) -> str
        self.telegram = None  # TelegramService
        owner = self

        class Handler(_Handler):
            proxy = owner

        self._server = _Server(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, name="stream-proxy", daemon=True).start()

    def drive_url(self, file_id: str) -> str:
        return f"http://127.0.0.1:{self.port}/{self.secret}/drive/{file_id}"

    def telegram_url(self, chat_id: int, msg_id: int) -> str:
        return f"http://127.0.0.1:{self.port}/{self.secret}/tg/{chat_id}/{msg_id}"

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _Handler(BaseHTTPRequestHandler):
    proxy: StreamProxy
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args) -> None:
        pass

    def do_GET(self) -> None:
        self._handle(head=False)

    def do_HEAD(self) -> None:
        self._handle(head=True)

    def _handle(self, head: bool) -> None:
        self._headers_sent = False
        parts = self.path.split("?", 1)[0].strip("/").split("/")
        if len(parts) < 3 or not secrets.compare_digest(parts[0], self.proxy.secret):
            self.send_error(404)
            return
        try:
            if parts[1] == "drive" and len(parts) == 3 and _DRIVE_ID.match(parts[2]):
                self._drive(parts[2], head)
            elif parts[1] == "tg" and len(parts) == 4:
                self._telegram(int(parts[2]), int(parts[3]), head)
            else:
                self.send_error(404)
        except ConnectionError:
            pass  # VLC closes the socket whenever it seeks
        except Exception as exc:
            if not self._headers_sent:
                self.send_error(502, str(exc)[:200])
            self.close_connection = True

    def _start(self, status: int, headers: dict[str, str]) -> None:
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self._headers_sent = True

    def _drive(self, file_id: str, head: bool) -> None:
        get_token = self.proxy.drive_token
        if get_token is None:
            self.send_error(503, "Google Drive is not connected")
            return
        headers = {"Accept-Encoding": "identity"}
        if self.headers.get("Range"):
            headers["Range"] = self.headers["Range"]
        resp = None
        for force_refresh in (False, True):
            headers["Authorization"] = f"Bearer {get_token(force_refresh)}"
            resp = requests.get(DRIVE_MEDIA_URL.format(file_id), headers=headers, stream=True, timeout=30)
            if resp.status_code != 401:
                break
            resp.close()
        with resp:
            out = {name: resp.headers[name] for name in _FORWARD_HEADERS if name in resp.headers}
            out.setdefault("Accept-Ranges", "bytes")
            if "Content-Length" not in out:
                out["Connection"] = "close"
                self.close_connection = True
            self._start(resp.status_code, out)
            if head:
                return
            for chunk in resp.raw.stream(_BUFFER, decode_content=False):
                self.wfile.write(chunk)

    def _telegram(self, chat_id: int, msg_id: int, head: bool) -> None:
        tg = self.proxy.telegram
        if tg is None or not tg.is_authorized:
            self.send_error(503, "Telegram is not connected")
            return
        msg = tg.get_message(chat_id, msg_id)
        size = msg.file.size
        start, end, partial = 0, size - 1, False
        match = _RANGE.match((self.headers.get("Range") or "").strip())
        if match and (match.group(1) or match.group(2)):
            partial = True
            if match.group(1):
                start = int(match.group(1))
                end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
            else:
                start = max(0, size - int(match.group(2)))
            if start > end:
                self._start(416, {"Content-Range": f"bytes */{size}", "Content-Length": "0"})
                return
        headers = {
            "Content-Type": msg.file.mime_type or "application/octet-stream",
            "Content-Length": str(end - start + 1),
            "Accept-Ranges": "bytes",
        }
        if partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._start(206 if partial else 200, headers)
        if head:
            return
        for chunk in tg.iter_range(chat_id, msg_id, start, end):
            self.wfile.write(chunk)
