"""Dock GUI security primitives — one-time token, server-side sessions, CSRF.

All state is in-memory and per-process: sessions die on restart (intentional).
No third-party crypto deps (no itsdangerous); ``secrets`` only.
"""

from __future__ import annotations

import secrets

SESSION_COOKIE = "engram_dock_session"
CSRF_HEADER = "x-engram-csrf"
SESSION_MAX_AGE = 28800  # 8h


class TokenStore:
    """The single-use startup token exchanged for a session on first load."""

    def __init__(self, token: str) -> None:
        self._token = token or ""
        self._used = False

    def consume(self, token: str) -> bool:
        """Return True exactly once for the correct, unused token."""
        if self._used or not token or not self._token:
            return False
        if not secrets.compare_digest(str(token), self._token):
            return False
        self._used = True
        return True


class SessionStore:
    """In-memory sessions keyed by an opaque 256-bit id; each carries a CSRF token."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}

    def create(self) -> tuple[str, str]:
        sid = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        self._sessions[sid] = {"csrf": csrf}
        return sid, csrf

    def get(self, sid: str) -> dict | None:
        return self._sessions.get(sid) if sid else None

    def validate_csrf(self, sid: str, csrf: str) -> bool:
        sess = self.get(sid)
        if not sess or not csrf:
            return False
        return secrets.compare_digest(str(csrf), str(sess.get("csrf", "")))
