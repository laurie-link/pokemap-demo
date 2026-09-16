"""Local static server for the official-map desktop UI."""
from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

STATIC_DIR = Path(__file__).resolve().parent / "static"
ICON_DIR = STATIC_DIR / "icons"
HOST = "127.0.0.1"
PORT = 18766

_server: ThreadingHTTPServer | None = None
_lock = threading.Lock()


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        pass

    def translate_path(self, path: str) -> str:
        if path.startswith("/icons/"):
            name = Path(path).name
            candidate = ICON_DIR / name
            if candidate.is_file():
                return str(candidate)
        return super().translate_path(path)


def app_url() -> str:
    return f"http://{HOST}:{PORT}/index.html"


def ensure_server() -> None:
    global _server
    with _lock:
        if _server is not None:
            return
        STATIC_DIR.mkdir(parents=True, exist_ok=True)
        handler = partial(_Handler, directory=str(STATIC_DIR))
        httpd = ThreadingHTTPServer((HOST, PORT), handler)
        httpd.allow_reuse_address = True
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        _server = httpd
