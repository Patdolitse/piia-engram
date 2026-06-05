"""Verify a published MCP Registry version through paginated search."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from typing import Callable, Any


DEFAULT_API = "https://registry.modelcontextprotocol.io/v0/servers"


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed registry URL
        return json.loads(response.read().decode("utf-8"))


def find_registry_version(
    *,
    name: str,
    version: str,
    api: str = DEFAULT_API,
    fetch: Callable[[str], dict[str, Any]] = fetch_json,
    max_pages: int = 10,
) -> dict[str, Any] | None:
    """Return the matching server entry, following ``metadata.nextCursor``."""
    cursor: str | None = None
    for _ in range(max_pages):
        params = {"search": name}
        if cursor:
            params["cursor"] = cursor
        url = f"{api}?{urllib.parse.urlencode(params)}"
        data = fetch(url)
        for entry in data.get("servers", []):
            server = entry.get("server", {})
            if server.get("name") == name and str(server.get("version")) == version:
                return entry
        cursor = data.get("metadata", {}).get("nextCursor")
        if not cursor:
            break
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", default="io.github.Patdolitse/piia-engram")
    parser.add_argument("--version", required=True)
    parser.add_argument("--api", default=DEFAULT_API)
    args = parser.parse_args(argv)

    found = find_registry_version(name=args.name, version=args.version, api=args.api)
    if not found:
        print(f"::error::MCP Registry version not found: {args.name} {args.version}")
        return 1
    server = found["server"]
    meta = found.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
    package_version = server.get("packages", [{}])[0].get("version")
    print(
        f"[OK] MCP Registry {server.get('name')} version={server.get('version')} "
        f"package={package_version} status={meta.get('status')} "
        f"isLatest={meta.get('isLatest')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
