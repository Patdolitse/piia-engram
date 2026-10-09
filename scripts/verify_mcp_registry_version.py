"""Verify a published MCP Registry version and its latest package metadata."""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

DEFAULT_API = "https://registry.modelcontextprotocol.io/v0.1/servers"
OFFICIAL_META = "io.modelcontextprotocol.registry/official"


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _shape(message: str) -> ValueError:
    return ValueError("Unexpected MCP Registry response shape: " + message)


def find_registry_version(*, name: str, version: str, api: str = DEFAULT_API,
                          fetch: Callable[[str], dict[str, Any]] = fetch_json,
                          max_pages: int = 10) -> dict[str, Any] | None:
    """Search the v0.1 server envelope, following metadata.nextCursor."""
    cursor = None
    seen = set()
    for _ in range(max_pages):
        params = {"search": name}
        if cursor:
            params["cursor"] = cursor
        data = fetch(f"{api}?{urllib.parse.urlencode(params)}")
        if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
            raise _shape("servers must be an array")
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            raise _shape("metadata must be an object")
        for entry in data["servers"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("server"), dict):
                raise _shape("each entry needs a server object")
            server = entry["server"]
            if not isinstance(server.get("name"), str) or not isinstance(server.get("version"), str):
                raise _shape("server name and version must be strings")
            if server["name"] != name or server["version"] != version:
                continue
            packages = server.get("packages")
            if not isinstance(packages, list) or not packages or any(
                not isinstance(p, dict) or not isinstance(p.get("version"), str) for p in packages
            ):
                raise _shape("packages must contain versioned objects")
            if any(p["version"] != version for p in packages):
                raise ValueError(f"MCP Registry package version mismatch: expected {version}")
            meta = entry.get("_meta")
            official = meta.get(OFFICIAL_META) if isinstance(meta, dict) else None
            if not isinstance(official, dict) or type(official.get("isLatest")) is not bool:
                raise _shape("official metadata needs boolean isLatest")
            if not official["isLatest"]:
                raise ValueError(f"MCP Registry version {version} is not latest")
            return entry
        cursor = metadata.get("nextCursor")
        if cursor is None or cursor == "":
            return None
        if not isinstance(cursor, str) or cursor in seen:
            raise _shape("nextCursor must be a new string")
        seen.add(cursor)
    raise ValueError("MCP Registry pagination limit reached before verification")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="io.github.Patdolitse/piia-engram")
    parser.add_argument("--version", required=True)
    parser.add_argument("--api", default=DEFAULT_API)
    args = parser.parse_args(argv)
    try:
        found = find_registry_version(name=args.name, version=args.version, api=args.api)
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        reason = "timeout" if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError) else "HTTP request failed"
        print(f"::error::MCP Registry {reason}; retry verification when the registry is available")
        return 1
    except (ValueError, TypeError) as exc:
        print(f"::error::{exc}")
        return 1
    if found is None:
        print(f"::error::MCP Registry version not found: {args.name} {args.version}")
        return 1
    print(f"[OK] MCP Registry {args.name} version={args.version} package={args.version} isLatest=True")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
