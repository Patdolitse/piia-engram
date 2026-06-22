"""Public reference-truth guard — every link/command we publish must resolve.

The repo already guards numbers (check_public_fact_sync / check_public_claim_drift),
over-claiming, and trust wording. It did NOT guard whether the files, runbooks,
and ``scripts/*.py`` commands that public docs POINT AT actually exist. They can
rot: a public-facts.json once pointed at ``docs/runbooks/public-truth-sync.md``
after that runbook had been removed — "trust evidence referencing missing trust
evidence". This guard makes that fail.

Scope (current-state public surfaces only):
- README.md, README.zh-CN.md, PRIVACY.md, SECURITY.md, CONTRIBUTING.md
- docs/**/*.md  (excluding docs/internal/**)
- docs/public-facts.json  (its string values are scanned for path refs)

Deliberately EXCLUDED: docs/internal/** (maintainer-only), CHANGELOG*.md and
release-evidence/** (historical records that legitimately name since-moved files).

Checks per surface:
1. Markdown local links ``[text](path)`` resolve to an existing file/dir.
2. ``scripts/*.py`` command references point at an existing script.
3. (JSON) path-like refs (docs/…, scripts/…) in string values exist.

No network, no writes. Exit 0 = all references resolve; exit 1 = dangling refs.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import subprocess
import sys
from pathlib import Path

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SCRIPT_RE = re.compile(r"\bscripts/[\w./-]+\.py\b")
# Repo-relative path references inside prose / JSON strings.
PATH_REF_RE = re.compile(
    r"\b(?:docs|scripts|release-evidence|assets)/[\w./-]+"
    r"\.(?:md|py|json|txt|ya?ml|toml|png|svg)\b"
)

_SCHEMES = ("http://", "https://", "mailto:", "tel:", "ftp://", "//")


def is_local_link(target: str) -> bool:
    t = target.strip()
    if not t or t.startswith("#"):
        return False
    return not t.startswith(_SCHEMES)


def link_target_path(target: str) -> str:
    """Strip a trailing ``"title"`` and any ``#anchor`` from a link target."""
    t = target.strip()
    # [text](path "title") — take the path token before whitespace
    t = t.split()[0] if t.split() else t
    t = t.split("#", 1)[0]
    return t


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def published_checker(root: Path):
    """Return ``is_published(relpath)->bool`` backed by ``git ls-files``.

    A referenced file that exists on disk but is NOT tracked (e.g. gitignored
    internal docs) is dead for anyone who clones the repo — so "published" means
    git-tracked, not merely present. Returns None outside a git repo, in which
    case callers fall back to filesystem existence.
    """
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return None
    tracked = {line for line in proc.stdout.split("\n") if line}
    dirs: set[str] = set()
    for f in tracked:
        parts = f.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))

    def is_published(relpath: str) -> bool:
        rel = relpath.strip("/")
        return rel in tracked or rel in dirs

    return is_published


def _present(root: Path, base_dir: Path, target: str, is_published) -> bool:
    if is_published is None:
        # Non-git fallback (e.g. unit tests / sdist): filesystem existence.
        return (base_dir / target).exists()
    # Normalize WITHOUT touching the filesystem so case is preserved — git is
    # case-sensitive, and Path.resolve() would canonicalize case on Windows and
    # let a wrong-case link (dead on Linux/clones) pass.
    base_rel = _rel(base_dir, root)
    joined = posixpath.normpath(posixpath.join(base_rel, target))
    if joined.startswith("..") or joined == ".":
        return False  # outside the published tree
    return is_published(joined)


def check_markdown(path: Path, root: Path, is_published=None) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = _rel(path, root)

    for raw in (m.group(1) for m in LINK_RE.finditer(text)):
        if not is_local_link(raw):
            continue
        target = link_target_path(raw)
        if not target:
            continue
        if not _present(root, path.parent, target, is_published):
            errors.append(f"{rel}: dead link -> {target}")

    for script_ref in SCRIPT_RE.findall(text):
        if not _present(root, root, script_ref, is_published):
            errors.append(f"{rel}: references missing script -> {script_ref}")

    return errors


def _iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def check_json_manifest(path: Path, root: Path, is_published=None) -> list[str]:
    errors: list[str] = []
    rel = _rel(path, root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{rel}: cannot parse JSON ({exc})"]
    seen: set[str] = set()
    for s in _iter_strings(data):
        for ref in PATH_REF_RE.findall(s):
            if ref in seen:
                continue
            seen.add(ref)
            if not _present(root, root, ref, is_published):
                errors.append(f"{rel}: references missing path -> {ref}")
    return errors


def discover_surfaces(root: Path) -> list[Path]:
    surfaces: list[Path] = []
    for name in ("README.md", "README.zh-CN.md", "PRIVACY.md", "SECURITY.md", "CONTRIBUTING.md"):
        p = root / name
        if p.is_file():
            surfaces.append(p)
    docs = root / "docs"
    if docs.is_dir():
        for md in sorted(docs.rglob("*.md")):
            if "internal" in md.relative_to(root).parts:
                continue
            surfaces.append(md)
        manifest = docs / "public-facts.json"
        if manifest.is_file():
            surfaces.append(manifest)
    return surfaces


def scan(root: str | Path) -> list[str]:
    root = Path(root)
    is_published = published_checker(root)
    errors: list[str] = []
    for surface in discover_surfaces(root):
        if surface.suffix == ".json":
            errors += check_json_manifest(surface, root, is_published)
        else:
            errors += check_markdown(surface, root, is_published)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify public docs reference only files that exist.")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    args = parser.parse_args(argv)

    errors = scan(Path(args.root))
    if not errors:
        print("[OK] every public reference resolves")
        return 0
    print("[FAIL] dangling public references:", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
