"""Local release/distribution readiness report (Phase 12) — read-only, no publish.

Aggregates the *existing* release gates into one local, read-only status so an
owner can see "is this ready to ship?" without running the publish workflow. It
**performs no public or remote action** — no build, no tag, no upload. It only
reads the working tree and reports.

What it reports:
- the ``pyproject`` version;
- presence of the required public files (README + zh README, LICENSE, CHANGELOG,
  pyproject);
- English-first invariant for release notes (English README/CHANGELOG exist as
  the primary, with the ``.zh-CN`` translations alongside);
- release-evidence completeness for the current version (reuses
  ``scripts/check_release_gate.check_release_gate``);
- whether a publish allowlist (``.publishallow``) exists;
- a scan of human-facing **public docs** for reverse-disclosure signals — the
  always-on, committed half uses only *generic, non-revealing* patterns
  (personal absolute filesystem paths and the maintainer's private drive), so
  this guard itself never names the private workspace; the precise private terms
  stay in gitignored local files.

This complements — does not replace — the gitignored-file private-term scanners
(``scripts/release_sanitize_check.py``, ``check_release_artifact_private_terms``).
The committed guard catches the structural disclosure shapes that should never
appear in public docs regardless of the local term list; when the gitignored
private-term files are present (e.g. ``.sanitizeignore``), their literal terms
are folded in too. Crucially, this module hardcodes **no** private workspace
names — doing so would itself leak them, since ``src/**`` is published.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Required public files at the repo root.
_REQUIRED_FILES = (
    "README.md",
    "README.zh-CN.md",
    "LICENSE",
    "CHANGELOG.md",
    "pyproject.toml",
)

# English-first pairs: (english_primary, chinese_translation).
_ENGLISH_FIRST_PAIRS = (
    ("README.md", "README.zh-CN.md"),
    ("CHANGELOG.md", "CHANGELOG.zh-CN.md"),
)

# Human-facing public docs to scan for private-mechanism leakage. Curated (not a
# recursive sweep) so the guard is precise; internal/_drafts/superpowers are
# intentionally excluded (they are not part of the public surface).
_PUBLIC_DOC_CANDIDATES = (
    "README.md", "README.zh-CN.md",
    "CHANGELOG.md", "CHANGELOG.zh-CN.md",
    "SECURITY.md", "PRIVACY.md", "CONTRIBUTING.md", "NOTICE",
    "docs/architecture.md", "docs/vision.md", "docs/trust.md",
    "docs/comparison.md", "docs/messaging.md", "docs/listing-copy.md",
    "docs/governance.md", "docs/hybrid-search.md",
    "docs/cross-tool-guide.md", "docs/cross-tool-continuity-demo.md",
)

# Always-on, committed reverse-disclosure patterns. These are deliberately
# GENERIC and non-revealing — they describe *shapes* (personal absolute paths,
# the maintainer's private Windows drive) that should never appear in public
# docs, without naming any private workspace. Hardcoding the actual private
# names here would itself be a leak (this file ships under ``src/**``).
_GENERIC_DISCLOSURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("windows_user_path", re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+", re.IGNORECASE)),
    ("private_d_drive", re.compile(r"\bD:[\\/]", re.IGNORECASE)),
    ("posix_home_path", re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+")),
)

# Gitignored local sources of precise private terms (NOT published). Mirrors the
# convention used by ``scripts/check_release_artifact_private_terms.py``.
_LOCAL_PATTERNS_FILE = ".sanitizeignore"
_CUSTOM_TERMS_FILE = ".engram-release-sensitive.txt"


def _load_local_private_terms(root: Path) -> list[tuple[str, re.Pattern[str]]]:
    """Load precise private terms from gitignored local files, if present.

    Returns ``(label, compiled_regex)`` pairs. Absent files → empty (the generic
    patterns above remain the always-on guard). Never raises.
    """
    out: list[tuple[str, re.Pattern[str]]] = []
    local = root / _LOCAL_PATTERNS_FILE
    if local.is_file():
        try:
            for n, raw in enumerate(local.read_text(encoding="utf-8").splitlines(), 1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"^(?:high|warn)\s*:\s*(.+)$", line, re.IGNORECASE)
                if m:
                    line = m.group(1).strip()
                if not line:
                    continue
                try:
                    out.append((f"{_LOCAL_PATTERNS_FILE}#{n}", re.compile(line)))
                except re.error:
                    continue
        except OSError:
            pass
    custom = Path.home() / _CUSTOM_TERMS_FILE
    if custom.is_file():
        try:
            for n, raw in enumerate(custom.read_text(encoding="utf-8").splitlines(), 1):
                term = raw.strip()
                if not term or term.startswith("#"):
                    continue
                out.append((f"~/{_CUSTOM_TERMS_FILE}#{n}", re.compile(re.escape(term), re.IGNORECASE)))
        except OSError:
            pass
    return out


def _version(root: Path) -> str:
    path = root / "pyproject.toml"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("version") and "=" in stripped:
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def scan_public_docs_for_private_terms(root: Path) -> list[dict[str, Any]]:
    """Scan curated public docs for reverse-disclosure signals. Returns hit list.

    Uses the always-on generic patterns plus any gitignored local private terms.
    Each hit is ``{file, label, match}`` (match truncated, never the whole line).
    """
    patterns = list(_GENERIC_DISCLOSURE_PATTERNS) + _load_local_private_terms(Path(root))
    hits: list[dict[str, Any]] = []
    for rel in _PUBLIC_DOC_CANDIDATES:
        path = Path(root) / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in patterns:
            m = pattern.search(text)
            if m:
                hits.append({"file": rel, "label": label, "match": m.group(0)[:60]})
    return hits


def build_release_readiness(root: str | Path) -> dict[str, Any]:
    """Build a read-only release readiness report for the working tree."""
    root_path = Path(root).expanduser().resolve()
    version = _version(root_path)

    files = {name: (root_path / name).is_file() for name in _REQUIRED_FILES}
    missing_files = [name for name, present in files.items() if not present]

    english_first = []
    for english, chinese in _ENGLISH_FIRST_PAIRS:
        english_first.append({
            "english": english,
            "english_present": (root_path / english).is_file(),
            "chinese": chinese,
            "chinese_present": (root_path / chinese).is_file(),
        })
    english_first_ok = all(p["english_present"] for p in english_first)

    # Reuse the deterministic release gate for evidence completeness.
    evidence_ok = False
    evidence_problems: list[str] = []
    try:
        import importlib.util

        gate_path = root_path / "scripts" / "check_release_gate.py"
        if gate_path.is_file() and version:
            spec = importlib.util.spec_from_file_location("_engram_release_gate", gate_path)
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            evidence_ok, evidence_problems = module.check_release_gate(version, root_path)
        else:
            evidence_problems = ["release gate script or version unavailable"]
    except Exception as exc:  # never let the report crash on gate issues
        evidence_problems = [f"gate check error: {type(exc).__name__}: {exc}"]

    allowlist_present = (root_path / ".publishallow").is_file()
    private_term_hits = scan_public_docs_for_private_terms(root_path)

    ready = bool(
        version
        and not missing_files
        and english_first_ok
        and allowlist_present
        and not private_term_hits
        and evidence_ok
    )

    return {
        "root": str(root_path),
        "version": version,
        "required_files": files,
        "missing_files": missing_files,
        "english_first": english_first,
        "english_first_ok": english_first_ok,
        "release_evidence_ok": evidence_ok,
        "release_evidence_problems": evidence_problems,
        "publish_allowlist_present": allowlist_present,
        "private_term_hits": private_term_hits,
        "ready": ready,
        "note": "read-only readiness report — no build/tag/publish performed",
    }


def render_release_readiness_text(report: dict[str, Any]) -> str:
    """Render the readiness report as an owner-facing checklist."""
    def mark(ok: bool) -> str:
        return "OK  " if ok else "MISS"

    lines = [
        f"Release readiness (read-only): v{report.get('version', '?')}  "
        f"=> {'READY' if report.get('ready') else 'NOT READY'}",
        f"  [{mark(not report['missing_files'])}] required files"
        + (f" (missing: {', '.join(report['missing_files'])})" if report["missing_files"] else ""),
        f"  [{mark(report['english_first_ok'])}] English-first release notes",
        f"  [{mark(report['publish_allowlist_present'])}] publish allowlist (.publishallow)",
        f"  [{mark(not report['private_term_hits'])}] no reverse-disclosure signals in public docs"
        + (f" ({len(report['private_term_hits'])} hit(s)!)" if report["private_term_hits"] else ""),
        f"  [{mark(report['release_evidence_ok'])}] release-evidence complete for this version",
    ]
    for hit in report["private_term_hits"]:
        lines.append(f"    ! {hit['file']}: [{hit['label']}] {hit['match']}")
    for problem in report.get("release_evidence_problems", []):
        if not report["release_evidence_ok"]:
            lines.append(f"    - evidence: {problem}")
    lines.append("  (no build/tag/publish performed — run scripts/check_release_gate.py before shipping)")
    return "\n".join(lines)
