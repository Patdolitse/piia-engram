"""Dock GUI ASGI app factory (Starlette). Unit-testable via TestClient without
binding a real socket. Security gate first; route content/UI come in later slices.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .contracts import (
    archive_entry,
    dock_archived_list_payload,
    dock_memory_list_payload,
    dock_resume_payload,
    restore_entry,
    update_entry,
)
from .security import (
    CSRF_HEADER,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    SessionStore,
    TokenStore,
)


def _no_store(resp: Response) -> Response:
    # Never let a browser/proxy cache memory-store responses; no CORS ever.
    resp.headers["Cache-Control"] = "no-store"
    return resp


class _HostGuard(BaseHTTPMiddleware):
    """DNS-rebinding defense: serve only loopback Host headers. Runs before any
    auth/route, so a foreign page (even one rebinding its name to 127.0.0.1, which
    still sends its OWN Host) can't reach the store or attempt the token exchange."""

    def __init__(self, app, *, allowed: set[str]) -> None:
        super().__init__(app)
        self._allowed = allowed

    async def dispatch(self, request: Request, call_next):
        if request.headers.get("host", "") not in self._allowed:
            return _no_store(JSONResponse({"ok": False, "error": "bad host"}, status_code=403))
        return await call_next(request)


# The launcher opens /auth#t=<token>. The token lives ONLY in the URL fragment, so
# it never reaches the server in the request line, server logs, or any Referer. This
# page reads it client-side, exchanges it for a session, stashes the CSRF token for
# the SPA, then strips the token from history.
_AUTH_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="referrer" content="no-referrer">
<title>Engram Dock</title></head>
<body><p>Connecting to your Engram Dock…</p>
<script>
(async function () {
  var t = (location.hash || "").replace(/^#t=/, "");
  if (t) {
    try {
      var r = await fetch("/auth/exchange", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({token: t})
      });
      var j = await r.json();
      if (j && j.csrf) sessionStorage.setItem("engram_dock_csrf", j.csrf);
    } catch (e) {}
    history.replaceState(null, "", "/");
  }
  location.replace("/");
})();
</script></body></html>"""


_REAUTH_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="referrer" content="no-referrer"><title>Engram Dock</title></head>
<body style="font-family:system-ui,'Segoe UI',sans-serif;max-width:520px;margin:80px auto;padding:0 20px;color:#1a1d24">
<h1>会话已结束</h1>
<p>这个 Dock 会话已失效（可能服务器重启了，或这个标签页太旧）。出于安全，网页里无法自动重连。</p>
<p>请回到终端重新运行：</p>
<pre style="background:#f6f7f9;padding:12px 14px;border-radius:8px">engram serve --ui</pre>
<p style="color:#8a93a3">它会用一次性令牌开一个新的安全会话。</p>
</body></html>"""


def create_app(engram: Any, *, auth_token: str, port: int) -> Starlette:
    tokens = TokenStore(auth_token)
    sessions = SessionStore()
    expected_origin = f"http://127.0.0.1:{port}"
    static_dir = Path(__file__).parent / "static"

    def _current_session(request: Request) -> dict | None:
        return sessions.get(request.cookies.get(SESSION_COOKIE, ""))

    async def auth_page(request: Request) -> Response:
        resp = HTMLResponse(_AUTH_PAGE)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    async def spa_root(request: Request) -> Response:
        # Session-gated SPA. No session -> a friendly, readable re-auth page (not a
        # blank 401): a browser page can't silently re-auth (that needs the one-time
        # launch token), so we tell the owner how to recover.
        if _current_session(request) is None:
            resp: Response = HTMLResponse(_REAUTH_PAGE)
        else:
            resp = HTMLResponse((static_dir / "index.html").read_text(encoding="utf-8"))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    async def auth_exchange(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        token = str((body or {}).get("token") or "")
        if not tokens.consume(token):
            return _no_store(
                JSONResponse({"ok": False, "error": "invalid or spent token"}, status_code=401)
            )
        sid, csrf = sessions.create()
        resp = _no_store(JSONResponse({"ok": True, "csrf": csrf}))
        resp.set_cookie(
            SESSION_COOKIE, sid,
            max_age=SESSION_MAX_AGE, path="/",
            httponly=True, samesite="Strict",
        )
        return resp

    async def dock_status(request: Request) -> Response:
        if _current_session(request) is None:
            return _no_store(
                JSONResponse({"ok": False, "error": "unauthenticated"}, status_code=401)
            )
        return _no_store(JSONResponse({"ok": True, "status": {}}))

    async def dock_memory(request: Request) -> Response:
        if _current_session(request) is None:
            return _no_store(
                JSONResponse({"ok": False, "error": "unauthenticated"}, status_code=401)
            )
        from piia_engram.core import Engram as _Engram

        # Reads open a zero-write view on the server's store root (never ENGRAM_DIR
        # env), so a concurrent request can't misread another store and nothing
        # is written on a read (the 4.7.0 discipline).
        payload = dock_memory_list_payload(_Engram(root=engram.root, read_only=True))
        return _no_store(JSONResponse(payload))

    async def dock_resume(request: Request) -> Response:
        # 接续: the paste-ready cross-tool context. Read-only (zero-write).
        if _current_session(request) is None:
            return _no_store(
                JSONResponse({"ok": False, "error": "unauthenticated"}, status_code=401)
            )
        project = request.query_params.get("project", "") or ""
        from piia_engram.core import Engram as _Engram

        payload = dock_resume_payload(_Engram(root=engram.root, read_only=True), project=project)
        return _no_store(JSONResponse(payload))

    async def dock_archived(request: Request) -> Response:
        # 回收站: a zero-write list of archived entries (the inverse of dock-memory),
        # so the GUI can offer one-click restore. Read-only, session-gated.
        if _current_session(request) is None:
            return _no_store(
                JSONResponse({"ok": False, "error": "unauthenticated"}, status_code=401)
            )
        from piia_engram.core import Engram as _Engram

        payload = dock_archived_list_payload(_Engram(root=engram.root, read_only=True))
        return _no_store(JSONResponse(payload))

    def _require_write_auth(request: Request) -> Response | None:
        # Owner gate for unsafe methods (Codex Option A): session + exact loopback
        # Origin + CSRF. Returns an error response so the caller bails BEFORE opening
        # any writable Engram (zero side-effect), or None for a genuine owner action.
        if _current_session(request) is None:
            return _no_store(JSONResponse({"ok": False, "error": "unauthenticated"}, status_code=401))
        if request.headers.get("origin", "") != expected_origin:
            return _no_store(JSONResponse({"ok": False, "error": "bad origin"}, status_code=403))
        sid = request.cookies.get(SESSION_COOKIE, "")
        if not sessions.validate_csrf(sid, request.headers.get(CSRF_HEADER, "")):
            return _no_store(JSONResponse({"ok": False, "error": "bad csrf"}, status_code=403))
        return None

    async def dock_archive(request: Request) -> Response:
        err = _require_write_auth(request)
        if err is not None:
            return err  # zero side-effect: no writable Engram opened on a refused write
        try:
            body = await request.json()
        except Exception:
            body = {}
        item_id = str((body or {}).get("id") or "")
        from piia_engram.core import Engram as _Engram

        receipt = archive_entry(_Engram(root=engram.root), item_id)
        return _no_store(JSONResponse(receipt, status_code=200 if receipt.get("ok") else 400))

    async def dock_update(request: Request) -> Response:
        err = _require_write_auth(request)
        if err is not None:
            return err  # zero side-effect: no writable Engram opened on a refused write
        try:
            body = await request.json()
        except Exception:
            body = {}
        item_id = str((body or {}).get("id") or "")
        updates = (body or {}).get("updates") or {}
        from piia_engram.core import Engram as _Engram

        receipt = update_entry(_Engram(root=engram.root), item_id, updates)
        return _no_store(JSONResponse(receipt, status_code=200 if receipt.get("ok") else 400))

    async def dock_restore(request: Request) -> Response:
        err = _require_write_auth(request)
        if err is not None:
            return err  # zero side-effect: no writable Engram opened on a refused write
        try:
            body = await request.json()
        except Exception:
            body = {}
        item_id = str((body or {}).get("id") or "")
        from piia_engram.core import Engram as _Engram

        receipt = restore_entry(_Engram(root=engram.root), item_id)
        return _no_store(JSONResponse(receipt, status_code=200 if receipt.get("ok") else 400))

    routes = [
        Route("/", spa_root, methods=["GET"]),
        Route("/auth", auth_page, methods=["GET"]),
        Route("/auth/exchange", auth_exchange, methods=["POST"]),
        Route("/api/dock-status", dock_status, methods=["GET"]),
        Route("/api/dock-memory", dock_memory, methods=["GET"]),
        Route("/api/dock-resume", dock_resume, methods=["GET"]),
        Route("/api/dock-archived", dock_archived, methods=["GET"]),
        Route("/api/dock-archive", dock_archive, methods=["POST"]),
        Route("/api/dock-restore", dock_restore, methods=["POST"]),
        Route("/api/dock-update", dock_update, methods=["POST"]),
        Mount("/static", StaticFiles(directory=str(static_dir)), name="static"),
    ]
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    middleware = [Middleware(_HostGuard, allowed=allowed_hosts)]
    return Starlette(routes=routes, middleware=middleware)
