"""Scan built release artifacts for maintainer-private terms.

This complements ``release_sanitize_check.py``. The normal sanitizer scans
the tracked repository before build; this scanner extracts wheel/sdist files
after build and scans the actual publishable artifacts. That matters because
sdists can include generated metadata, README copies, and tests.

Private terms are intentionally loaded from gitignored local files instead
of being hardcoded here:

- ``.sanitizeignore`` lines, supporting ``high:<regex>`` / ``warn:<regex>``
- ``~/.engram-release-sensitive.txt`` literal terms (warn by default)

Run from repo root after ``python -m build``:

    python scripts/check_release_artifact_private_terms.py dist --strict
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tarfile
import tempfile
import warnings
import zipfile
from pathlib import Path

_LOCAL_PATTERNS_FILE = ".sanitizeignore"
_CUSTOM_TERMS_FILE = ".engram-release-sensitive.txt"
_TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".rst",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def _compile_local_patterns(root: Path) -> list[tuple[str, re.Pattern[str], str]]:
    path = root / _LOCAL_PATTERNS_FILE
    if not path.is_file():
        return []

    patterns: list[tuple[str, re.Pattern[str], str]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        severity = "warn"
        match = re.match(r"^(high|warn)\s*:\s*(.+)$", line, re.IGNORECASE)
        if match:
            severity = match.group(1).lower()
            line = match.group(2).strip()
            if not line:
                continue
        try:
            patterns.append((f"{_LOCAL_PATTERNS_FILE}#{line_no}", re.compile(line), severity))
        except re.error as exc:
            print(f"[warn] {_LOCAL_PATTERNS_FILE}:{line_no} invalid regex skipped: {exc}",
                  file=sys.stderr)
    return patterns


def _compile_custom_terms() -> list[tuple[str, re.Pattern[str], str]]:
    path = Path.home() / _CUSTOM_TERMS_FILE
    if not path.is_file():
        return []

    patterns: list[tuple[str, re.Pattern[str], str]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        term = raw.strip()
        if not term or term.startswith("#"):
            continue
        patterns.append((
            f"~/{_CUSTOM_TERMS_FILE}#{line_no}",
            re.compile(re.escape(term), re.IGNORECASE),
            "warn",
        ))
    return patterns


def _artifact_files(dist_dir: Path) -> list[Path]:
    if not dist_dir.is_dir():
        raise FileNotFoundError(f"artifact directory not found: {dist_dir}")
    return [
        p for p in sorted(dist_dir.iterdir())
        if p.is_file() and (p.name.endswith(".whl") or p.name.endswith(".tar.gz"))
    ]


def _extract_artifact(artifact: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    if artifact.name.endswith(".whl"):
        with zipfile.ZipFile(artifact) as zf:
            for member in zf.infolist():
                target = (dest / member.filename).resolve()
                try:
                    target.relative_to(dest_resolved)
                except ValueError as exc:
                    raise ValueError(f"unsafe zip member path: {member.filename}") from exc
            zf.extractall(dest)
        return
    if artifact.name.endswith(".tar.gz"):
        with tarfile.open(artifact, "r:gz") as tf:
            for member in tf.getmembers():
                target = (dest / member.name).resolve()
                try:
                    target.relative_to(dest_resolved)
                except ValueError as exc:
                    raise ValueError(f"unsafe tar member path: {member.name}") from exc
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    category=DeprecationWarning,
                    message=".*filter extracted tar archives.*",
                )
                tf.extractall(dest)
        return
    raise ValueError(f"unsupported artifact: {artifact}")


def _iter_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in _TEXT_SUFFIXES:
            yield path


def _scan_text(
    rel: str,
    text: str,
    patterns: list[tuple[str, re.Pattern[str], str]],
) -> list[tuple[str, str, int, str, str]]:
    hits: list[tuple[str, str, int, str, str]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for label, pattern, severity in patterns:
            if pattern.search(line):
                hits.append((label, severity, line_no, rel, line.strip()[:180]))
    return hits


def scan_artifacts(
    dist_dir: Path,
    root: Path,
) -> list[tuple[str, str, int, str, str]]:
    artifacts = _artifact_files(dist_dir)
    if not artifacts:
        raise FileNotFoundError(f"no .whl or .tar.gz artifacts found in {dist_dir}")

    patterns = _compile_local_patterns(root) + _compile_custom_terms()
    if not patterns:
        print("[info] no local private term sources found; artifact private-term scan is a no-op")
        return []

    hits: list[tuple[str, str, int, str, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="piia-engram-artifact-private-scan-"))
    try:
        for artifact in artifacts:
            dest = tmp / artifact.name.replace("/", "_").replace("\\", "_")
            dest.mkdir(parents=True)
            _extract_artifact(artifact, dest)
            for path in _iter_text_files(dest):
                rel = f"{artifact.name}!/{path.relative_to(dest).as_posix()}"
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                hits.extend(_scan_text(rel, text, patterns))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dist_dir", nargs="?", default="dist")
    parser.add_argument("--root", default=".")
    parser.add_argument("--strict", action="store_true",
                        help="Treat warn-level private-term hits as blocking too.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    dist_dir = Path(args.dist_dir).resolve()
    try:
        hits = scan_artifacts(dist_dir, root)
    except Exception as exc:
        print(f"[error] artifact private-term scan failed: {exc}", file=sys.stderr)
        return 2

    high = 0
    warn = 0
    for label, severity, line_no, rel, line in hits:
        marker = "[HIGH]" if severity == "high" else "[warn]"
        print(f"  {marker} {rel}:{line_no} {label}: {line}")
        if severity == "high":
            high += 1
        else:
            warn += 1

    print(f"\n== Artifact private-term summary ==  high={high}  warn={warn}")
    if high:
        print("\n[FAIL] private terms found in release artifacts.")
        return 1
    if args.strict and warn:
        print("\n[FAIL] --strict mode: warn-level private terms block release.")
        return 1
    print("\n[OK] no blocking private terms in release artifacts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
