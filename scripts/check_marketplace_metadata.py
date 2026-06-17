"""Offline marketplace metadata guard for public MCP listings.

This guard keeps local listing metadata explicit enough for third-party
marketplaces such as LobeHub, Glama, and MCP registries to avoid fallback titles
like "MCP Server Manifest Plugin". It performs no network calls and never
refreshes external listings.

Run from the repository root:

    python scripts/check_marketplace_metadata.py
    python scripts/check_marketplace_metadata.py --json

Exit codes:
- 0  local marketplace metadata is internally consistent
- 1  a required marketplace-facing fact is missing or stale
- 2  setup error (missing/invalid local files)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_PUBLIC_FACTS = "docs/public-facts.json"
PUBLISHER_META_KEY = "io.modelcontextprotocol.registry/publisher-provided"
EXPECTED_DISPLAY_NAME = "Piia Engram"
EXPECTED_SLUG = "patdolitse-piia-engram"
EXPECTED_REPOSITORY = "https://github.com/Patdolitse/piia-engram"
BAD_FALLBACK_TITLE = "MCP Server Manifest Plugin"


class SetupError(Exception):
    """Local repo layout or JSON parsing error."""


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise SetupError(f"missing file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SetupError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SetupError(f"{path} top level must be a JSON object")
    return data


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise SetupError(f"missing file: {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def _pyproject_version(root: Path) -> str | None:
    text = _read_text(root / "pyproject.toml")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def _require(condition: bool, problems: list[str], message: str) -> None:
    if not condition:
        problems.append(message)


def check(root: Path, public_facts_rel: str = DEFAULT_PUBLIC_FACTS) -> dict:
    """Return a structured report for local marketplace-facing metadata."""
    problems: list[str] = []
    facts_doc = _read_json(root / public_facts_rel)
    facts = facts_doc.get("facts")
    if not isinstance(facts, dict):
        raise SetupError(f"{public_facts_rel} missing facts object")

    version = _pyproject_version(root)
    if not version:
        raise SetupError("pyproject.toml version not found")

    server = _read_json(root / ".mcp" / "server.json")
    pyproject = _read_text(root / "pyproject.toml")
    readme = _read_text(root / "README.md")
    readme_zh = _read_text(root / "README.zh-CN.md")
    skill = _read_text(root / "skills" / "engram" / "SKILL.md")
    glama = _read_text(root / "glama.yaml")

    _require(server.get("title") == EXPECTED_DISPLAY_NAME, problems,
             ".mcp/server.json title must be 'Piia Engram'")
    _require(server.get("name") == "io.github.Patdolitse/piia-engram", problems,
             ".mcp/server.json name must stay on the canonical MCP id")
    _require(server.get("version") == version, problems,
             ".mcp/server.json version must match pyproject.toml")
    _require(server.get("websiteUrl") == EXPECTED_REPOSITORY, problems,
             ".mcp/server.json websiteUrl must point to the GitHub repo")

    repository = server.get("repository") or {}
    _require(repository.get("url") == EXPECTED_REPOSITORY, problems,
             ".mcp/server.json repository.url must point to the GitHub repo")
    _require(repository.get("source") == "github", problems,
             ".mcp/server.json repository.source must be github")

    packages = server.get("packages")
    package = packages[0] if isinstance(packages, list) and packages else {}
    _require(package.get("registryType") == "pypi", problems,
             ".mcp/server.json package registryType must be pypi")
    _require(package.get("registryBaseUrl") == "https://pypi.org", problems,
             ".mcp/server.json package registryBaseUrl must be https://pypi.org")
    _require(package.get("identifier") == "piia-engram", problems,
             ".mcp/server.json package identifier must be piia-engram")
    _require(package.get("version") == version, problems,
             ".mcp/server.json package version must match pyproject.toml")
    _require(package.get("runtimeHint") == "uvx", problems,
             ".mcp/server.json package runtimeHint must be uvx")
    _require((package.get("transport") or {}).get("type") == "stdio", problems,
             ".mcp/server.json package transport must be stdio")

    runtime_args = package.get("runtimeArguments") or []
    runtime_values = [
        str(arg.get("value", ""))
        for arg in runtime_args
        if isinstance(arg, dict)
    ]
    _require(f"piia-engram=={version}" in runtime_values, problems,
             ".mcp/server.json runtimeArguments must pin the PyPI version")
    _require("piia-engram-mcp" in runtime_values, problems,
             ".mcp/server.json runtimeArguments must run piia-engram-mcp")

    package_args = package.get("packageArguments") or []
    transport_args = [
        arg
        for arg in package_args
        if isinstance(arg, dict) and arg.get("name") == "--transport"
    ]
    _require(any(arg.get("value") == "stdio" for arg in transport_args), problems,
             ".mcp/server.json packageArguments must force --transport stdio")

    env_defaults = {
        item.get("name"): item.get("default")
        for item in (package.get("environmentVariables") or [])
        if isinstance(item, dict)
    }
    _require(env_defaults.get("ENGRAM_MCP_STARTUP_SYNC") == "off", problems,
             ".mcp/server.json should default marketplace startup sync to off")
    _require(env_defaults.get("ENGRAM_TOOLS") == "core", problems,
             ".mcp/server.json should default marketplace tools to core")
    _require(env_defaults.get("PYTHONIOENCODING") == "utf-8", problems,
             ".mcp/server.json should force UTF-8 stdio for marketplaces")

    meta = (server.get("_meta") or {}).get(PUBLISHER_META_KEY)
    _require(isinstance(meta, dict), problems,
             ".mcp/server.json must include publisher-provided marketplace metadata")
    if isinstance(meta, dict):
        _require(meta.get("displayName") == EXPECTED_DISPLAY_NAME, problems,
                 "publisher metadata displayName must be Piia Engram")
        _require(meta.get("slug") == EXPECTED_SLUG, problems,
                 "publisher metadata slug must match the LobeHub slug")
        _require(meta.get("homepage") == EXPECTED_REPOSITORY, problems,
                 "publisher metadata homepage must point to the GitHub repo")
        _require(meta.get("license") == "AGPL-3.0-or-later", problems,
                 "publisher metadata license must be AGPL-3.0-or-later")

        capabilities = meta.get("capabilities") or {}
        for meta_key, fact_key in (
            ("mcp_tools_total", "mcp_tools_total"),
            ("mcp_tools_core", "mcp_tools_core"),
            ("mcp_tools_advanced", "mcp_tools_advanced"),
        ):
            _require(capabilities.get(meta_key) == facts.get(fact_key), problems,
                     f"publisher metadata {meta_key} must match public facts")
        _require(capabilities.get("prompts") == 0, problems,
                 "publisher metadata prompts count must be explicit")
        _require(capabilities.get("resources") == 0, problems,
                 "publisher metadata resources count must be explicit")

        install = meta.get("install") or {}
        _require("pip install piia-engram" in str(install.get("pip", "")), problems,
                 "publisher metadata must include pip install guidance")
        _require("piia-engram-mcp" in str(install.get("uvx", "")), problems,
                 "publisher metadata must include uvx/piia-engram-mcp guidance")

    # LobeHub badge intentionally pulled from the READMEs while the LobeHub
    # listing still shows "unvalidated"; re-add the badge + this check once the
    # listing is validated. The publisher slug/metadata above stays in sync so
    # the listing itself remains correct.

    _require("name: piia-engram" in glama, problems,
             "glama.yaml must expose the canonical package name")
    _require("54 MCP tools" in glama, problems,
             "glama.yaml must expose the current tool count")
    _require("name: engram" in skill, problems,
             "skills/engram/SKILL.md frontmatter must expose the skill name")
    _require("Local-first personal AI identity and memory layer" in skill, problems,
             "skills/engram/SKILL.md must describe Engram as identity/memory")

    public_surfaces = {
        ".mcp/server.json": json.dumps(server, ensure_ascii=False),
        "pyproject.toml": pyproject,
        "README.md": readme,
        "README.zh-CN.md": readme_zh,
        "glama.yaml": glama,
        "skills/engram/SKILL.md": skill,
    }
    for rel, text in public_surfaces.items():
        _require(BAD_FALLBACK_TITLE not in text, problems,
                 f"{rel} must not contain the LobeHub fallback title")

    return {
        "ok": not problems,
        "version": version,
        "displayName": EXPECTED_DISPLAY_NAME,
        "slug": EXPECTED_SLUG,
        "repository": EXPECTED_REPOSITORY,
        "facts": {
            "mcp_tools_total": facts.get("mcp_tools_total"),
            "mcp_tools_core": facts.get("mcp_tools_core"),
            "mcp_tools_advanced": facts.get("mcp_tools_advanced"),
        },
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="Repo root (default: cwd).")
    parser.add_argument("--public-facts", default=DEFAULT_PUBLIC_FACTS,
                        help=f"Public facts manifest (default: {DEFAULT_PUBLIC_FACTS}).")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        report = check(root, args.public_facts)
    except SetupError as exc:
        if args.json:
            print(json.dumps({"ok": False, "setup_error": str(exc)}, ensure_ascii=False))
        else:
            print(f"[error] {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if report["ok"]:
        print(
            "[OK] marketplace metadata is explicit "
            f"({report['displayName']} / v{report['version']} / {report['slug']})."
        )
        return 0

    print("::error::marketplace metadata drift detected:")
    for problem in report["problems"]:
        print(f"  - {problem}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
