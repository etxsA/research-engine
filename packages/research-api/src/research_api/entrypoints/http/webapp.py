"""The built web app, served from the API's own origin.

``apps/web`` is built into plain files (``pnpm build``). When the settings name a ``web_dir``, the
files are answered here, as the router's fallback after every API route, so the browser talks to
one origin and the API needs no CORS. The folder may be a symlink: it is resolved on each request,
so pointing the link at a new build takes effect without a restart.

Rules:

* only ``GET`` and ``HEAD`` (``405`` with ``Allow`` otherwise);
* a path under ``/api`` that no route matched stays a ``404`` problem, never the home page;
* a path that names a file is that file; a missing file with an extension is a ``404`` (a page
  from an old release asking for a script that no longer exists must fail, not get HTML);
* any other path is a route of the single-page app and gets ``index.html``;
* a path that leaves the folder (``..``, a symlink pointing out, a NUL) is a ``404``;
* every answer carries a strict Content-Security-Policy and ``nosniff``; hashed files under
  ``/assets/`` are cached for a year, everything else must be revalidated.
"""

from __future__ import annotations

from email.utils import parsedate_to_datetime
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocketClose

from ...domain.errors import NotFoundError, UnavailableError
from .problems import problem

ALLOWED_METHODS = ("GET", "HEAD")
INDEX = "index.html"
API_PREFIX = "/api"
ASSET_PREFIX = "/assets/"

# Scripts and connections only from this origin. KaTeX and the UI libraries set style attributes,
# which `style-src 'self'` would block, hence 'unsafe-inline' for styles only.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"

# Explicit types, so a minimal server image without a mime database serves scripts correctly.
MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def _unchanged(request: Request, response: Response) -> bool:
    """Whether the browser already holds this file (``If-None-Match``, else ``If-Modified-Since``).

    Starlette's ``FileResponse`` sets ``ETag`` and ``Last-Modified`` but never answers ``304``.
    """
    etag = response.headers["etag"]
    wanted = request.headers.get("if-none-match")
    if wanted is not None:
        tags = {t.strip().removeprefix("W/") for t in wanted.split(",")}
        return "*" in tags or etag in tags
    since = request.headers.get("if-modified-since")
    if since is None:
        return False
    try:
        return parsedate_to_datetime(response.headers["last-modified"]) <= parsedate_to_datetime(
            since
        )
    except (TypeError, ValueError):
        return False


def _file_answer(request: Request, target: Path, *, immutable: bool) -> Response:
    try:
        stat = target.stat()  # also sets ETag and Last-Modified on the response
    except OSError:  # replaced between the check and now
        return problem(request, status=404, slug=NotFoundError.slug, detail="No such file.")
    response = FileResponse(
        target,
        media_type=MEDIA_TYPES.get(target.suffix.lower()),
        headers={"Cache-Control": IMMUTABLE if immutable else REVALIDATE},
        stat_result=stat,
    )
    if _unchanged(request, response):
        keep = ("cache-control", "etag", "last-modified")
        return Response(status_code=304, headers={k: response.headers[k] for k in keep})
    return response


class WebApp:
    """Pure ASGI application that is the router's fallback: it answers what no API route matched."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # the API has no WebSocket routes
            await WebSocketClose()(scope, receive, send)
            return
        request = Request(scope, receive)
        response = self._answer(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        await response(scope, receive, send)

    def _answer(self, request: Request) -> Response:
        path = request.url.path
        if path == API_PREFIX or path.startswith(API_PREFIX + "/"):
            return problem(request, status=404, slug=NotFoundError.slug, detail="No such path.")
        if request.method not in ALLOWED_METHODS:
            return problem(
                request,
                status=405,
                slug="method-not-allowed",
                detail="This path answers GET and HEAD only.",
                headers={"Allow": ", ".join(ALLOWED_METHODS)},
            )
        root = self._root()
        if root is None:
            return problem(
                request,
                status=503,
                slug=UnavailableError.slug,
                detail="The web app is not installed on this server.",
            )
        target = self._file_for(root, path)
        if target is None:
            return problem(request, status=404, slug=NotFoundError.slug, detail="No such file.")
        return _file_answer(request, target, immutable=path.startswith(ASSET_PREFIX))

    def _root(self) -> Path | None:
        """The folder with its links resolved, if it holds a home page."""
        try:
            root = self.directory.resolve(strict=True)
        except OSError:
            return None
        return root if (root / INDEX).is_file() else None

    @staticmethod
    def _file_for(root: Path, path: str) -> Path | None:
        if "\x00" in path or "\\" in path:
            return None
        relative = path.lstrip("/")
        if relative:
            try:
                candidate = (root / relative).resolve()
            except (OSError, RuntimeError, ValueError):  # a symlink loop is RuntimeError on 3.12
                return None
            if not candidate.is_relative_to(root):
                return None
            if candidate.is_file():
                return candidate
            if "." in candidate.name:  # looks like a file, and there is none
                return None
        return root / INDEX
