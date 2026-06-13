"""Pre-release sanitization scanner.

Walks the git-tracked working tree + recent commit messages, looking for
patterns that should not ship in a public release:

- API keys / tokens (OpenAI, GitHub, HuggingFace, PyPI, generic 32+ hex)
- Private key headers (PEM / OpenSSH)
- Hardcoded local paths (Windows ``C:\\Users\\...`` / POSIX ``/home/...``)
- Optional: custom sensitive-term list from ``~/.engram-release-sensitive.txt``
  (one term per line; not committed to the repo — each maintainer keeps
  their own).

Run from the repo root:

    python scripts/release_sanitize_check.py
    python scripts/release_sanitize_check.py --commit-messages   # also scan git log
    python scripts/release_sanitize_check.py --strict            # exit 1 on any hit

Exit code:
- 0 — no hits (or only informational hits in non-strict mode)
- 1 — at least one hit was found in --strict mode
- 2 — scanner setup error (not in a git repo, etc.)

This is a *sanity net*, not a guarantee. Real secrets get past regex
scanners all the time. Always pair this with the four-layer manual
checklist in ``docs/internal/release-playbook.md``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# (label, regex, severity)  severity: "high" / "warn"
_BUILT_IN_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("OpenAI key",     re.compile(r"sk-[A-Za-z0-9]{20,}"),                "high"),
    ("GitHub token",   re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),         "high"),
    ("PyPI token",     re.compile(r"pypi-[A-Za-z0-9_-]{20,}"),            "high"),
    ("HuggingFace",    re.compile(r"hf_[A-Za-z0-9]{20,}"),                "high"),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}"),                   "high"),
    ("Slack token",    re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"),       "high"),
    ("PEM private",    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),           "high"),
    # Match one-or-more backslashes so BOTH the source-escaped form
    # (``C:\\Users\\name`` in .py) and the literal single-backslash form
    # (``C:\Users\name`` in markdown / plain text) are caught. The old
    # ``\\\\`` (exactly two) regex missed single-backslash paths in docs.
    ("Windows path",   re.compile(r"C:\\+Users\\+[A-Za-z0-9_.-]+",
                                  re.IGNORECASE),                         "warn"),
    ("POSIX home",     re.compile(r"/home/[a-z][a-z0-9_-]+(?:/|$)"),      "warn"),
    ("password=",      re.compile(r"(?<![a-z_])password\s*[:=]\s*['\"][^'\"]+",
                                  re.IGNORECASE),                         "warn"),
]

# ---------------------------------------------------------------------------
# Live-credential scan (2026-06-14 incident hardening)
#
# A REAL DeepSeek key once sat in tests/test_core.py as a redaction-test
# "sample". The built-in OpenAI pattern above used ``sk-[A-Za-z0-9]{20,}``
# (which misses ``sk-proj-…``) AND ``tests/`` is fixture-exempt from the
# built-ins — so the real key was never flagged. This scan closes both gaps:
# it mirrors the audited ``piia_engram.sensitivity._SECRET_VALUE_RE`` vendor
# shapes and runs on EVERY tracked file, fixtures included. Intentional fake
# fixtures are enumerated in _ALLOWLISTED_TEST_SECRETS; any credential-shaped
# token NOT in that set is a hard HIGH hit. A real key would never be added to
# the allowlist — it would be removed and the key rotated.
# ---------------------------------------------------------------------------
_LIVE_CREDENTIAL_RE = re.compile(
    r"sk-[A-Za-z0-9_\-]{16,}"                 # OpenAI / DeepSeek (incl. sk-proj-)
    r"|sk_(?:live|test)_[A-Za-z0-9]{10,}"     # Stripe secret key
    r"|rk_(?:live|test)_[A-Za-z0-9]{10,}"     # Stripe restricted key
    r"|gh[pousr]_[A-Za-z0-9]{20,}"            # GitHub PAT / OAuth
    r"|github_pat_[A-Za-z0-9_]{20,}"          # GitHub fine-grained PAT
    r"|glpat-[A-Za-z0-9_\-]{20,}"             # GitLab PAT
    r"|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}"     # AWS access key id (+ temp)
    r"|AIza[0-9A-Za-z_\-]{35,}"               # Google API key
    r"|ya29\.[0-9A-Za-z_\-]{20,}"             # Google OAuth access token
    r"|xox[baprs]-[0-9A-Za-z\-]{10,}"         # Slack token
    r"|xapp-[0-9A-Za-z\-]{10,}"               # Slack app-level token
    r"|hf_[A-Za-z0-9]{20,}"                   # HuggingFace
    r"|pypi-[A-Za-z0-9_\-]{20,}"              # PyPI upload token
)

# Intentional FAKE credentials that legitimately live in tracked fixtures /
# docstrings. Each entry is a conscious "this is fake" sign-off. Keep this in
# sync when a new fixture key is added; the scan FAILS on anything not listed.
_ALLOWLISTED_TEST_SECRETS = frozenset({
    "sk-test_1234567890abcdef1234567890abcdef",
    "sk-proj-abc123DEFghi456JKLmno789PQRstu0",
    "sk-SUPERSECRET-do-not-print",
    "sk-SUPERSECRETTOKENVALUE-do-not-print",
    "sk-proj-ABCD1234EFGH5678IJKL9012MNOP3456",
    "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "sk-proj-abcdefghijklmnop1234",
    "sk-abcdefghijklmnop1234567890",
    "sk-skip-this-not-a-real-key",
    "sk-side-effect-free",
    "sk-1234567890abcdef1234567890abcdef",
    "sk-abcdef1234567890abcdef",
    "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1234",
    "ghp_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBB5678",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "ghp_0123456789abcdefghij0123456789",
    "gho_0123456789abcdefghij0123456789",
    "AKIAIOSFODNN7EXAMPLE",
    "ASIAIOSFODNN7EXAMPLE",
    "AIzaSyA1234567890abcdefghijklmnopqrstuvw",
    "ya29.a0AbCdEfGhIjKlMnOpQrStUvWxYz",
    "xoxb-1234567890-abcdefghijkl",
    "sk_live_abcdefghij1234567890",
    "rk_test_abcdefghij1234567890",
    "github_pat_0123456789abcdefghij0123",
    "glpat-1234567890abcdefghij",
    "xapp-1-A1234567890-abcdefghij",
})


def _scan_live_credentials(
    path: Path, text: str | None = None
) -> list[tuple[str, str, int, str]]:
    """Flag real credential VALUE shapes in any tracked file (fixtures too).

    Returns (label, "high", line_no, redacted_preview) for every
    credential-shaped token that is not an allowlisted fake fixture. The raw
    secret is never returned — only a short, non-reconstructable prefix.
    """
    hits: list[tuple[str, str, int, str]] = []
    if text is None:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            return hits
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in _LIVE_CREDENTIAL_RE.finditer(line):
            token = m.group(0)
            if token in _ALLOWLISTED_TEST_SECRETS:
                continue
            preview = (token[:6] + "***") if len(token) > 6 else "***"
            hits.append(("live credential", "high", lineno, preview))
    return hits


# v3.31 P1-2: internal-disclosure patterns. These don't leak secrets but
# leak strategy / process signal that an outside reader can use.
#
# Only GENERIC OPSEC patterns live here (any project would scan for
# these). Project-specific patterns that would themselves reveal "what
# WE hide" (internal review-process names, eval model codenames, gate
# codes) are NOT inlined — they're loaded from a gitignored
# ``.sanitizeignore`` file (see _load_internal_patterns_file). This keeps
# the public script from broadcasting our specific sensitivities, the
# same way the guard workflow externalizes paths to ``.guardignore``.
_INTERNAL_DISCLOSURE_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("review code count", re.compile(r"\b\d+\s*HIGH[\s/-]+\d+\s*MEDIUM", re.IGNORECASE), "warn"),
    ("industry-first claim", re.compile(r"industry[\s-]*first", re.IGNORECASE), "warn"),
    ("prior-art line ref", re.compile(r"prior[\s-]*art\b.*\.py:\d+", re.IGNORECASE), "warn"),
    ("internal issue id", re.compile(r"\bissue[\s_-]*id\s*[:=]\s*\d+", re.IGNORECASE), "warn"),
]

# Gitignored file holding project-specific internal-disclosure regexes,
# one per line (``#`` comments allowed). Absent on fresh clones / CI, so
# those runs enforce only the generic patterns above; the maintainer's
# local checkout + pre-commit hook carry the full set.
#
# Format:
#   regex                  -> warn
#   warn:<regex>           -> warn
#   high:<regex>           -> high
#
# The severity prefix lets maintainers keep exact private-project terms
# out of the public script while still making those local terms hard
# release blockers.
_INTERNAL_PATTERNS_FILE = ".sanitizeignore"


def _load_internal_patterns_file() -> list[tuple[str, re.Pattern[str], str]]:
    """Load project-specific internal-disclosure regexes from
    ``.sanitizeignore`` if present.

    Lines default to warn severity. Maintainers may prefix a line with
    ``high:`` to make an exact private term block release even when
    ``--strict`` is not set.
    """
    path = Path(_INTERNAL_PATTERNS_FILE)
    if not path.is_file():
        return []
    out: list[tuple[str, re.Pattern[str], str]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        severity = "warn"
        match = re.match(r"^(high|warn)\s*:\s*(.+)$", line, re.IGNORECASE)
        if match:
            severity = match.group(1).lower()
            line = match.group(2).strip()
            if not line:
                print(f"[warn] {_INTERNAL_PATTERNS_FILE}:{i} empty regex, skipped",
                      file=sys.stderr)
                continue
        try:
            out.append((f"local#{i}", re.compile(line), severity))
        except re.error:
            print(f"[warn] {_INTERNAL_PATTERNS_FILE}:{i} invalid regex, skipped",
                  file=sys.stderr)
    return out

# Files fully skipped for ALL pattern categories, even if git-tracked.
_SKIP_GLOBS = (
    ".git/",
    "scripts/release_sanitize_check.py",  # self
    "docs/internal/release-playbook.md",           # documents the patterns
    "docs/playbook-auto-extraction-design.md",  # discusses redaction examples
    "CHANGELOG.md",                       # historical, version paths OK
    "Dockerfile",                         # /home/<container-user>/ is not a host path
)

# Fixture-bearing files: their whole job is to carry FAKE secrets / sample
# local paths (``sk-...`` keys, ``C:\Users\victim\...``) so redaction +
# sanitization logic can be tested. Running the built-in + generic
# internal-disclosure patterns over them floods with intentional-fixture
# false positives, so these are scanned ONLY against the project's real
# private terms from ``.sanitizeignore`` (real usernames / internal
# codenames) — those must never appear even inside a test fixture.
#
# (v4.1.x review: ``tests/`` used to live in _SKIP_GLOBS and was fully
# skipped, which hid a real private path that had reached a test fixture.
# Demoting it to fixture-exempt keeps the fixture noise out while still
# catching a real private identifier.)
_FIXTURE_GLOBS = (
    "tests/",
    "scripts/check_generated_export_redaction.py",
)


def _git_tracked_files() -> list[Path]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], text=True, encoding="utf-8"
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"[error] git ls-files failed: {exc}", file=sys.stderr)
        sys.exit(2)
    return [Path(line) for line in out.splitlines() if line]


def _git_staged_files() -> list[Path]:
    """v3.31 P1-1: files staged for the next commit (pre-commit hook use).

    Uses ``--diff-filter=ACM`` to skip deletions/renames where the path
    no longer has content to scan.
    """
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            text=True, encoding="utf-8",
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"[error] git diff --cached failed: {exc}", file=sys.stderr)
        sys.exit(2)
    return [Path(line) for line in out.splitlines() if line]


def _should_skip(rel_path: str) -> bool:
    s = rel_path.replace("\\", "/")
    return any(s.startswith(p) or p in s for p in _SKIP_GLOBS)


def _is_fixture(rel_path: str) -> bool:
    """True for files that legitimately carry fake secret/path fixtures.

    These are scanned only against the local ``.sanitizeignore`` real-term
    patterns, not the built-in / generic internal-disclosure patterns.
    """
    s = rel_path.replace("\\", "/")
    return any(s.startswith(p) or p in s for p in _FIXTURE_GLOBS)


def _load_custom_terms() -> list[re.Pattern[str]]:
    home_list = Path.home() / ".engram-release-sensitive.txt"
    if not home_list.is_file():
        return []
    terms: list[re.Pattern[str]] = []
    for line in home_list.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(re.compile(re.escape(line), re.IGNORECASE))
    return terms


def _read_staged_blob(rel_path: str) -> str | None:
    """Read a path's CONTENT from the git index (staged blob), not the work
    tree.

    Critical for the pre-commit hook: ``--staged`` must scan exactly what is
    about to be committed. Reading the working tree instead would miss a
    secret that was ``git add``-ed and then deleted from the work tree
    without re-staging. Returns None if the blob can't be read.
    """
    try:
        out = subprocess.run(
            ["git", "show", f":{rel_path}"],
            capture_output=True, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return out.stdout.decode("utf-8", errors="ignore")


def _scan_file(
    path: Path,
    custom: list[re.Pattern[str]],
    patterns: list[tuple[str, re.Pattern[str], str]],
    text: str | None = None,
) -> list[tuple[str, str, int, str]]:
    """Return list of (label, severity, line_no, line_text).

    If ``text`` is given it is scanned directly (used for staged blobs);
    otherwise the working-tree file at ``path`` is read.
    """
    hits: list[tuple[str, str, int, str]] = []
    if text is None:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            return hits
    for lineno, line in enumerate(text.splitlines(), 1):
        for label, pat, severity in patterns:
            if pat.search(line):
                hits.append((label, severity, lineno, line.strip()[:160]))
        for i, pat in enumerate(custom):
            if pat.search(line):
                hits.append((f"custom#{i+1}", "warn", lineno, line.strip()[:160]))
    return hits


# v3.32 P1: extensions whose multi-line string content (docstrings,
# triple-quoted blocks, prose) we additionally scan as one blob, so an
# internal-disclosure phrase that wraps across a line break inside a
# docstring isn't hidden from the per-line scanner.
_MULTILINE_EXTS = (".py", ".md", ".rst", ".txt")


def _scan_file_multiline(
    path: Path,
    patterns: list[tuple[str, re.Pattern[str], str]],
    text: str | None = None,
) -> list[tuple[str, str, int, str]]:
    """v3.32: catch internal-disclosure narrative that spans more than one
    line (typically inside a docstring), which ``_scan_file`` cannot see
    because it matches per line.

    The internal patterns already use ``\\s``/``[\\s-]*`` connectors, which
    match newlines — but only when run against the whole-file text instead
    of a single line. We therefore re-run the (internal) patterns over the
    full blob and report ONLY matches whose span actually crosses a
    newline, so single-line hits stay the responsibility of ``_scan_file``
    (no duplicate reporting).

    Returns list of (label, severity, line_no, snippet)."""
    if path.suffix.lower() not in _MULTILINE_EXTS:
        return []
    hits: list[tuple[str, str, int, str]] = []
    if text is None:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            return hits
    for label, pat, severity in patterns:
        for m in pat.finditer(text):
            frag = m.group(0)
            if "\n" not in frag:
                continue  # single-line — _scan_file already covers it
            lineno = text.count("\n", 0, m.start()) + 1
            snippet = re.sub(r"\s+", " ", frag).strip()[:160]
            hits.append((f"{label} (multiline)", severity, lineno, snippet))
    return hits


def _scan_commit_messages(
    custom: list[re.Pattern[str]],
    patterns: list[tuple[str, re.Pattern[str], str]],
) -> list[tuple[str, str, str, str]]:
    """Return list of (sha, label, severity, snippet)."""
    try:
        out = subprocess.check_output(
            ["git", "log", "--all", "--format=%H%n%s%n%b%n---END---"],
            text=True, encoding="utf-8",
        )
    except subprocess.CalledProcessError:
        return []
    hits: list[tuple[str, str, str, str]] = []
    current_sha = ""
    current_body: list[str] = []
    for line in out.splitlines():
        if line == "---END---":
            text = "\n".join(current_body)
            for label, pat, severity in patterns:
                m = pat.search(text)
                if m:
                    hits.append((current_sha[:10], label, severity, m.group(0)[:80]))
            for i, pat in enumerate(custom):
                m = pat.search(text)
                if m:
                    hits.append((current_sha[:10], f"custom#{i+1}", "warn", m.group(0)[:80]))
            current_sha = ""
            current_body = []
        elif not current_sha:
            current_sha = line
        else:
            current_body.append(line)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--commit-messages", action="store_true",
                    help="Also scan git commit messages (slower)")
    ap.add_argument("--strict", action="store_true",
                    help="Exit code 1 if any hit is found (including warn-level)")
    ap.add_argument("--staged", action="store_true",
                    help="Scan only files staged for commit (pre-commit hook use)")
    ap.add_argument("--internal", action="store_true",
                    help="Also scan for internal-disclosure patterns "
                         "(review codes, multi-way review, industry-first, "
                         "model codenames - see release-playbook.md section 1.4)")
    args = ap.parse_args()

    # Assemble the active pattern set.
    patterns = list(_BUILT_IN_PATTERNS)
    internal_patterns: list[tuple[str, re.Pattern[str], str]] = []
    local_patterns: list[tuple[str, re.Pattern[str], str]] = []
    if args.internal:
        local_patterns = _load_internal_patterns_file()
        internal_patterns = _INTERNAL_DISCLOSURE_PATTERNS + local_patterns
        patterns += internal_patterns
        extra = len(_INTERNAL_DISCLOSURE_PATTERNS) + len(local_patterns)
        suffix = (f" ({len(local_patterns)} from {_INTERNAL_PATTERNS_FILE})"
                  if local_patterns else f" (no {_INTERNAL_PATTERNS_FILE})")
        print(f"[info] internal-disclosure scanning ON ({extra} patterns{suffix})")

    custom = _load_custom_terms()
    if custom:
        print(f"[info] loaded {len(custom)} custom term(s) from ~/.engram-release-sensitive.txt")
    else:
        print("[info] no ~/.engram-release-sensitive.txt - only built-in patterns")

    total_high = 0
    total_warn = 0

    if args.staged:
        scan_label = "staged files"
        files = _git_staged_files()
        if not files:
            print("\n[OK] no staged files to scan.")
            return 0
    else:
        scan_label = "working tree"
        files = _git_tracked_files()

    print(f"\n== Scanning {scan_label} ==")
    for path in files:
        rel = str(path).replace("\\", "/")
        if _should_skip(rel):
            continue
        # In --staged mode, scan the staged blob (what will actually be
        # committed), not the working-tree file.
        staged_text = _read_staged_blob(rel) if args.staged else None
        if args.staged and staged_text is None:
            continue  # deleted/unreadable in index — nothing to scan
        # Live-credential scan runs on EVERY tracked file, fixtures included —
        # a REAL key must never sit in the repo even as a "sample" (this is the
        # exact gap that let a real DeepSeek key live in a test fixture).
        hits = _scan_live_credentials(path, text=staged_text)
        if _is_fixture(rel):
            # Intentional fake fixtures live here — scanning the built-in /
            # generic-internal patterns would only flag the fixtures. Scan
            # ONLY for the project's real private terms (.sanitizeignore),
            # which must never appear even in a test fixture. Without
            # --internal there are no such terms loaded, so skip those.
            if local_patterns:
                hits += _scan_file(path, [], local_patterns, text=staged_text)
                hits += _scan_file_multiline(path, local_patterns, text=staged_text)
        else:
            hits += _scan_file(path, custom, patterns, text=staged_text)
            if args.internal:
                hits += _scan_file_multiline(path, internal_patterns, text=staged_text)
        for label, severity, lineno, line_text in hits:
            marker = "[HIGH]" if severity == "high" else "[warn]"
            print(f"  {marker} {rel}:{lineno}  {label}: {line_text}")
            if severity == "high":
                total_high += 1
            else:
                total_warn += 1

    if args.commit_messages:
        print("\n== Scanning commit messages ==")
        for sha, label, severity, snippet in _scan_commit_messages(custom, patterns):
            marker = "[HIGH]" if severity == "high" else "[warn]"
            print(f"  {marker} {sha} {label}: {snippet}")
            if severity == "high":
                total_high += 1
            else:
                total_warn += 1

    print(f"\n== Summary ==  high={total_high}  warn={total_warn}")

    if total_high > 0:
        print("\n[FAIL] HIGH-severity hits found. Fix before releasing.")
        return 1
    if args.strict and total_warn > 0:
        print("\n[FAIL] --strict mode: warn-level hits also block release.")
        return 1
    print("\n[OK] no high-severity hits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
