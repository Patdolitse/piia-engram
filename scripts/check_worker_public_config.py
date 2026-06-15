#!/usr/bin/env python3
"""Guard public Worker config files against real Cloudflare resource IDs.

Cloudflare account, zone, D1, KV, and similar resource identifiers are not API
tokens by themselves. Still, public repo config should use placeholders so the
operator's concrete infrastructure map stays private.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")

SENSITIVE_KEYS = {
    "account_id",
    "zone_id",
    "database_id",
    "namespace_id",
    "preview_id",
}

LINE_RE = re.compile(
    r"^\s*(?P<key>account_id|zone_id|database_id|namespace_id|preview_id)"
    r"\s*=\s*['\"](?P<value>[^'\"]+)['\"]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    key: str

    def message(self) -> str:
        try:
            rel = self.path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            rel = self.path.name
        return (
            f"{rel}:{self.line}: real-looking Cloudflare {self.key} in public "
            "config; replace it with a placeholder and keep the concrete value "
            "in a gitignored private config."
        )


def _is_real_identifier(value: str) -> bool:
    clean = value.strip()
    if clean.startswith("<") and clean.endswith(">"):
        return False
    lowered = clean.lower()
    if any(word in lowered for word in ("placeholder", "example", "replace-me")):
        return False
    return bool(UUID_RE.fullmatch(clean) or HEX32_RE.fullmatch(clean))


def scan_paths(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = LINE_RE.match(line)
            if not match:
                continue
            key = match.group("key").lower()
            if key not in SENSITIVE_KEYS:
                continue
            if _is_real_identifier(match.group("value")):
                findings.append(Finding(path=path, line=lineno, key=key))
    return findings


def _tracked_worker_toml_files() -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "worker/*.toml"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except Exception:
        return sorted((ROOT / "worker").glob("*.toml"))
    return [ROOT / line.strip() for line in result.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="TOML config files to scan")
    args = parser.parse_args(argv)

    paths = args.paths or _tracked_worker_toml_files()
    findings = scan_paths([p if p.is_absolute() else ROOT / p for p in paths])
    for finding in findings:
        print(f"[HIGH] {finding.message()}")
    if findings:
        print(f"\n[FAIL] {len(findings)} public Worker config identifier(s) found.")
        return 1
    print("[OK] public Worker config files use placeholders for Cloudflare resource IDs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
