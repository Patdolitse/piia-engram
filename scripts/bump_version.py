"""Synchronize release version strings across public package surfaces.

Usage:
    python scripts/bump_version.py 4.11.0
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path


SEMVER = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
SEMVER_RE = re.compile(rf"^{SEMVER}$")


class BumpError(Exception):
    """Version sync setup or replacement failure."""


class BumpResult:
    def __init__(
        self,
        entries: list[dict],
        verify_returncode: int | None,
        verify_output: str = "",
    ) -> None:
        self.entries = entries
        self.verify_returncode = verify_returncode
        self.verify_output = verify_output

    @property
    def changed_files(self) -> list[str]:
        return [entry["file"] for entry in self.entries if entry["changed"]]


def validate_version(version: str) -> None:
    if not SEMVER_RE.match(version):
        raise ValueError(
            f"version must be semver X.Y.Z without a leading 'v': {version!r}"
        )


def _read(path: Path) -> str:
    if not path.is_file():
        raise BumpError(f"required file missing: {path}")
    return path.read_text(encoding="utf-8")


def _write_if_changed(path: Path, old_text: str, new_text: str) -> bool:
    if new_text == old_text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def _text_entry(
    root: Path,
    rel: str,
    pattern: str,
    new_version: str,
    *,
    replacement_count: int = 1,
    today: str | None = None,
    date_pattern: str | None = None,
    date_replacement: str | None = None,
) -> dict:
    path = root / rel
    text = _read(path)
    matches = list(re.finditer(pattern, text, flags=re.MULTILINE))
    if len(matches) != replacement_count:
        raise BumpError(
            f"{rel}: expected {replacement_count} version match(es), found {len(matches)}"
        )
    old_versions = [match.group("version") for match in matches]

    def repl(match: re.Match[str]) -> str:
        piece = f"{match.group('prefix')}{new_version}{match.group('suffix')}"
        if today and date_pattern and date_replacement:
            piece = re.sub(date_pattern, date_replacement, piece, count=1)
        return piece

    new_text = re.sub(pattern, repl, text, count=replacement_count, flags=re.MULTILINE)
    changed = _write_if_changed(path, text, new_text)
    return _entry(rel, old_versions, new_version, changed)


def _entry(rel: str, old_versions: list[str], new_version: str, changed: bool) -> dict:
    old = ", ".join(dict.fromkeys(old_versions))
    return {"file": rel, "old": old, "new": new_version, "changed": changed}


def _dump_json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _json_plugin(root: Path, new_version: str) -> dict:
    rel = ".claude-plugin/plugin.json"
    path = root / rel
    old_text = _read(path)
    data = json.loads(old_text)
    old_version = data.get("version")
    if not isinstance(old_version, str):
        raise BumpError(f"{rel}: missing string field 'version'")
    if old_version == new_version:
        return _entry(rel, [old_version], new_version, False)
    data["version"] = new_version
    changed = _write_if_changed(path, old_text, _dump_json(data))
    return _entry(rel, [old_version], new_version, changed)


def _json_public_facts(root: Path, new_version: str, today: str) -> dict:
    rel = "docs/public-facts.json"
    path = root / rel
    old_text = _read(path)
    data = json.loads(old_text)
    old_version = data.get("local_dev_version")
    old_date = data.get("last_verified_date")
    if not isinstance(old_version, str):
        raise BumpError(f"{rel}: missing string field 'local_dev_version'")
    if not isinstance(old_date, str):
        raise BumpError(f"{rel}: missing string field 'last_verified_date'")
    if old_version == new_version and old_date == today:
        entry = _entry(rel, [old_version], new_version, False)
        entry["detail"] = f"last_verified_date {old_date} -> {today}"
        return entry
    data["local_dev_version"] = new_version
    data["last_verified_date"] = today
    changed = _write_if_changed(path, old_text, _dump_json(data))
    entry = _entry(rel, [old_version], new_version, changed)
    entry["detail"] = f"last_verified_date {old_date} -> {today}"
    return entry


def _json_mcp_server(root: Path, new_version: str) -> dict:
    rel = ".mcp/server.json"
    path = root / rel
    old_text = _read(path)
    data = json.loads(old_text)
    old_versions: list[str] = []

    top_version = data.get("version")
    if not isinstance(top_version, str):
        raise BumpError(f"{rel}: missing string field 'version'")
    old_versions.append(top_version)
    data["version"] = new_version

    packages = data.get("packages")
    if not isinstance(packages, list) or not packages:
        raise BumpError(f"{rel}: missing packages[0]")
    package = packages[0]
    if not isinstance(package, dict):
        raise BumpError(f"{rel}: packages[0] must be an object")

    package_version = package.get("version")
    if not isinstance(package_version, str):
        raise BumpError(f"{rel}: missing packages[0].version")
    old_versions.append(package_version)
    package["version"] = new_version

    runtime_arguments = package.get("runtimeArguments")
    if not isinstance(runtime_arguments, list):
        raise BumpError(f"{rel}: packages[0].runtimeArguments must be a list")
    from_arg = next(
        (
            arg
            for arg in runtime_arguments
            if isinstance(arg, dict) and arg.get("name") == "--from"
        ),
        None,
    )
    if from_arg is None:
        raise BumpError(f"{rel}: missing runtimeArguments entry named --from")
    from_value = from_arg.get("value")
    if not isinstance(from_value, str):
        raise BumpError(f"{rel}: --from runtime argument must have a string value")
    match = re.search(rf"piia-engram==(?P<version>{SEMVER})", from_value)
    if not match:
        raise BumpError(f"{rel}: --from value must contain piia-engram==X.Y.Z")
    old_versions.append(match.group("version"))
    if (
        top_version == new_version
        and package_version == new_version
        and match.group("version") == new_version
    ):
        entry = _entry(rel, old_versions, new_version, False)
        entry["detail"] = "top-level, packages[0], runtimeArguments --from"
        return entry
    from_arg["value"] = re.sub(
        rf"piia-engram=={SEMVER}", f"piia-engram=={new_version}", from_value, count=1
    )

    changed = _write_if_changed(path, old_text, _dump_json(data))
    entry = _entry(rel, old_versions, new_version, changed)
    entry["detail"] = "top-level, packages[0], runtimeArguments --from"
    return entry


def _run_public_fact_guard(root: Path) -> tuple[int, str] | None:
    script = root / "scripts" / "check_public_fact_sync.py"
    if not script.is_file():
        return None
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode, output


def bump_version(
    root: str | Path,
    new_version: str,
    *,
    today: str | None = None,
    verify: bool = True,
) -> BumpResult:
    validate_version(new_version)
    root_path = Path(root).resolve()
    today_value = today or date.today().isoformat()

    entries = [
        _text_entry(
            root_path,
            "pyproject.toml",
            rf'(?P<prefix>^\s*version\s*=\s*")(?P<version>{SEMVER})(?P<suffix>")',
            new_version,
        ),
        _text_entry(
            root_path,
            "src/piia_engram/__init__.py",
            rf'(?P<prefix>^__version__\s*=\s*")(?P<version>{SEMVER})(?P<suffix>")',
            new_version,
        ),
        _json_mcp_server(root_path, new_version),
        _json_plugin(root_path, new_version),
        _text_entry(
            root_path,
            "glama.yaml",
            rf"(?P<prefix>^\s*version:\s*)(?P<version>{SEMVER})(?P<suffix>\s*)$",
            new_version,
        ),
        _json_public_facts(root_path, new_version, today_value),
        _text_entry(
            root_path,
            "README.md",
            rf"(?P<prefix>^\| Version frame \| \*\*v)(?P<version>{SEMVER})(?P<suffix>\*\*.*)$",
            new_version,
            today=today_value,
            date_pattern=r"verified [0-9]{4}-[0-9]{2}-[0-9]{2}",
            date_replacement=f"verified {today_value}",
        ),
        _text_entry(
            root_path,
            "README.zh-CN.md",
            rf"(?P<prefix>^\| 版本口径 \| \*\*v)(?P<version>{SEMVER})(?P<suffix>\*\*.*)$",
            new_version,
            today=today_value,
            date_pattern=r"[0-9]{4}-[0-9]{2}-[0-9]{2} 已核验",
            date_replacement=f"{today_value} 已核验",
        ),
    ]

    verify_result = _run_public_fact_guard(root_path) if verify else None
    verify_returncode = verify_result[0] if verify_result is not None else None
    verify_output = verify_result[1] if verify_result is not None else ""
    return BumpResult(entries, verify_returncode, verify_output)


def _print_summary(result: BumpResult) -> None:
    for entry in result.entries:
        state = "changed" if entry["changed"] else "skip"
        detail = f" ({entry['detail']})" if entry.get("detail") else ""
        print(
            f"[{state}] {entry['file']}: {entry['old']} -> {entry['new']}{detail}"
        )
    if result.verify_returncode is None:
        print("[verify] scripts/check_public_fact_sync.py not found; skipped")
    elif result.verify_returncode == 0:
        print("[verify] scripts/check_public_fact_sync.py passed")
    else:
        print(
            "[verify] scripts/check_public_fact_sync.py failed "
            f"(exit {result.verify_returncode})"
        )
        if result.verify_output:
            print(result.verify_output)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(".")
    if len(args) == 3 and args[0] == "--root":
        root = Path(args[1])
        version = args[2]
    elif len(args) == 1:
        version = args[0]
    else:
        print("usage: python scripts/bump_version.py [--root PATH] X.Y.Z", file=sys.stderr)
        return 2

    try:
        result = bump_version(root, version)
    except (BumpError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    _print_summary(result)
    return result.verify_returncode or 0


if __name__ == "__main__":
    sys.exit(main())
