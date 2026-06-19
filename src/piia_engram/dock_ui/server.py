"""Dock GUI launcher: `engram serve --ui` -> loopback-only uvicorn + open browser.

Generates a one-time token, builds the app with it, opens the browser to the /auth
bootstrap (token in the URL fragment), and serves on 127.0.0.1 ONLY. The blocking
side effects (uvicorn.run, webbrowser.open) are injected so the wiring is testable.
"""

from __future__ import annotations

import secrets
from typing import Any, Callable

from .app import create_app


def make_token() -> str:
    """A fresh high-entropy single-use startup token."""
    return secrets.token_urlsafe(32)


def launch_url(port: int, token: str) -> str:
    """The bootstrap URL — token lives ONLY in the fragment (never sent to server)."""
    return f"http://127.0.0.1:{port}/auth#t={token}"


def serve_ui(
    *,
    engram: Any = None,
    port: int = 7333,
    open_browser: bool = True,
    _runner: Callable | None = None,
    _opener: Callable | None = None,
) -> None:
    """Launch the Dock GUI on 127.0.0.1:<port> and open the browser to /auth."""
    if engram is None:
        from piia_engram.core import Engram

        # Reads use a zero-write handle; writes open a writable Engram per-request
        # after the owner gate (see dock_ui.app).
        engram = Engram(read_only=True)

    token = make_token()
    app = create_app(engram, auth_token=token, port=port)

    if open_browser:
        opener = _opener
        if opener is None:
            import webbrowser

            opener = webbrowser.open
        try:
            opener(launch_url(port, token))
        except Exception:
            pass  # a headless/locked-down env shouldn't stop the server starting

    runner = _runner
    if runner is None:
        import uvicorn

        runner = uvicorn.run
    runner(app, host="127.0.0.1", port=port, log_level="warning")
