"""Serve the static site and the contract HTTP API from one origin.

Requests under /api/ go unchanged to EnvironmentHttpApp. Everything else is a read-only
static file from the site directory (GET/HEAD only, no directory listing, no path escape).
Same origin means the browser sandbox needs no CORS and no extra API surface.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import unquote

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class SiteWithApi:
    def __init__(self, api: Callable[..., Any], site_dir: str | Path):
        self.api = api
        self.root = Path(site_dir).resolve()
        if not (self.root / "index.html").is_file():
            raise ValueError(f"{self.root} has no index.html")

    def __call__(self, environ: Mapping[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        path = environ.get("PATH_INFO", "") or "/"
        if path == "/api" or path.startswith("/api/"):
            return self.api(environ, start_response)
        method = environ.get("REQUEST_METHOD", "GET").upper()
        if method not in ("GET", "HEAD"):
            return self._plain(start_response, "405 Method Not Allowed", b"Method not allowed.")
        target = self._resolve(path)
        if target is None:
            return self._plain(start_response, "404 Not Found", b"Not found.")
        body = target.read_bytes()
        ctype = TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        start_response("200 OK", [("Content-Type", ctype), ("Content-Length", str(len(body))),
                                  ("Cache-Control", "no-cache"), ("X-Content-Type-Options", "nosniff")])
        return [b"" if method == "HEAD" else body]

    def _resolve(self, path: str) -> Path | None:
        rel = unquote(path).lstrip("/")
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        if "\x00" in rel or "\\" in rel:
            return None
        candidate = (self.root / rel).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None  # path escape
        return candidate if candidate.is_file() else None

    @staticmethod
    def _plain(start_response: Callable[..., Any], status: str, body: bytes) -> list[bytes]:
        start_response(status, [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))])
        return [body]
