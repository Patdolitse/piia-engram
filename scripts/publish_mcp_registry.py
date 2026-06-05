"""Publish MCP Registry with one safe auth-refresh retry.

``mcp-publisher publish`` can fail with a stale Registry JWT even after a prior
warm preflight. This wrapper treats a 401/expired-token response as retryable:
it refreshes auth via ``gh auth token`` + ``mcp-publisher login github -token``
and retries publish once. Token values are never printed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_MCP_PUBLISHER_CANDIDATES = (
    r"E:\Temp\mcp-publisher.exe",
    r"E:\Temp\mcp-publisher-v1.7.9-windows-amd64\mcp-publisher.exe",
)


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", "executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"


def resolve_mcp_publisher(which=shutil.which, candidates=None) -> str | None:
    found = which("mcp-publisher")
    if found:
        return found
    for raw in candidates or DEFAULT_MCP_PUBLISHER_CANDIDATES:
        if Path(raw).is_file():
            return raw
    return None


def is_retryable_auth_failure(rc: int, stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    return rc != 0 and (
        "401" in text
        or "unauthorized" in text
        or "expired" in text
        or "invalid or expired registry jwt" in text
    )


def publish_with_retry(
    server_json: str,
    *,
    publisher: str,
    run=_run,
) -> tuple[bool, list[dict[str, object]]]:
    """Publish, refreshing GitHub auth once on stale Registry JWT."""
    events: list[dict[str, object]] = []

    publish_cmd = [publisher, "publish", server_json]
    rc, out, err = run(publish_cmd, timeout=90)
    events.append({"step": "publish", "rc": rc, "retry": False})
    if rc == 0:
        return True, events
    if not is_retryable_auth_failure(rc, out, err):
        return False, events

    rc_token, token, _err_token = run(["gh", "auth", "token"], timeout=30)
    events.append({"step": "gh_token", "rc": rc_token})
    token = token.strip()
    if rc_token != 0 or not token:
        return False, events

    rc_login, _out_login, _err_login = run(
        [publisher, "login", "github", "-token", token],
        timeout=60,
    )
    events.append({"step": "login", "rc": rc_login})
    if rc_login != 0:
        return False, events

    rc_retry, _out_retry, _err_retry = run(publish_cmd, timeout=90)
    events.append({"step": "publish", "rc": rc_retry, "retry": True})
    return rc_retry == 0, events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("server_json", nargs="?", default=".mcp/server.json")
    parser.add_argument("--publisher", default=None)
    args = parser.parse_args(argv)

    publisher = args.publisher or resolve_mcp_publisher()
    if not publisher:
        print("::error::mcp-publisher not found on PATH or known fallback paths", file=sys.stderr)
        return 2

    ok, events = publish_with_retry(args.server_json, publisher=publisher)
    for event in events:
        retry = " retry" if event.get("retry") else ""
        print(f"[mcp-publish]{retry} {event['step']} rc={event['rc']}")
    if ok:
        print("[OK] MCP Registry publish completed.")
        return 0
    print("::error::MCP Registry publish failed after auth-refresh retry.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
