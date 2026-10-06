"""Public trust-claim guard for security/privacy prose.

Numeric public-fact drift is already handled by ``check_public_fact_sync.py``
and ``check_public_claim_drift.py``. This guard covers the prose that external
reviewers actually worry about: telemetry/network boundaries, plaintext-at-rest
disclosure, optional encryption wording, and telemetry endpoint consistency.

It is deterministic and offline. It reads public docs plus the local telemetry
source constants, then fails if a public trust surface omits required boundary
language, carries an absolute overclaim, or documents an endpoint that no longer
matches the client code.

Run from repo root:

    python scripts/check_public_trust_claims.py
    python scripts/check_public_trust_claims.py --json

Exit codes:
- 0  public trust claims are present and consistent
- 1  missing / contradictory / drifted public trust claim
- 2  setup error (required file missing or telemetry constants unavailable)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple


class SetupError(Exception):
    """Repo layout problem distinct from a trust-claim finding."""


class RequiredClaim(NamedTuple):
    file: str
    claim: str
    pattern: re.Pattern[str]


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


REQUIRED_CLAIMS: tuple[RequiredClaim, ...] = (
    RequiredClaim("README.md", "usage_ping_disclosed", _rx(r"one anonymous usage ping a day")),
    RequiredClaim(
        "README.md",
        "remote_optin",
        _rx(r"remote telemetry and feedback require separate explicit opt-in"),
    ),
    RequiredClaim(
        "README.md",
        "plaintext_default",
        _rx(r"(plain JSON|local JSON files you own|All data lives in `~/.engram/`)"),
    ),
    RequiredClaim("README.zh-CN.md", "usage_ping_disclosed", re.compile(r"每天发送一次匿名使用信号", re.DOTALL)),
    RequiredClaim(
        "README.zh-CN.md",
        "remote_optin",
        re.compile(r"远程 telemetry 和每周反馈报告必须单独显式开启", re.DOTALL),
    ),
    RequiredClaim(
        "README.zh-CN.md",
        "plaintext_default",
        re.compile(r"(明文 JSON|本地 JSON|本地明文)", re.DOTALL),
    ),
    RequiredClaim("SECURITY.md", "usage_ping_disclosed", _rx(r"one anonymous usage ping a day")),
    RequiredClaim(
        "SECURITY.md",
        "remote_optin",
        _rx(r"Remote telemetry[^\n]{0,160}separate opt-in"),
    ),
    RequiredClaim(
        "SECURITY.md",
        "never_collected",
        _rx(r"Never collected[^\n]{0,240}(identity content|lesson/decision/playbook bodies)"),
    ),
    RequiredClaim(
        "SECURITY.md",
        "optional_web_reads",
        _rx(r"Optional web reads[^\n]{0,160}explicitly invoked|Optional web reads[^\n]{0,160}explicitly provide"),
    ),
    RequiredClaim(
        "SECURITY.md",
        "optional_field_encryption",
        _rx(r"Optional field-level encryption.*(ENGRAM_SECRET|piia-engram\[secure\])"),
    ),
    RequiredClaim(
        "PRIVACY.md",
        "plaintext_default",
        _rx(r"stored as plain JSON files|All files are plain JSON"),
    ),
    RequiredClaim(
        "PRIVACY.md",
        "remote_optin",
        _rx(r"Remote telemetry and weekly feedback reports are separate opt-ins"),
    ),
    RequiredClaim(
        "PRIVACY.md",
        "plaintext_without_secret",
        _rx(r"Without `?ENGRAM_SECRET`?[^\n]{0,120}plaintext"),
    ),
    RequiredClaim("docs/telemetry-privacy.md", "usage_ping_disclosed", _rx(r"one anonymous usage ping a day")),
    RequiredClaim(
        "docs/telemetry-privacy.md",
        "remote_optin",
        _rx(r"Remote sending is a separate opt-in"),
    ),
    RequiredClaim(
        "docs/telemetry-privacy.md",
        "no_content",
        _rx(r"No lesson / decision / playbook content"),
    ),
    RequiredClaim("docs/telemetry-privacy.md", "install_id_disclosed", _rx(r"random install ID")),
    RequiredClaim(
        "docs/trust.md",
        "plaintext_default",
        _rx(r"plain JSON or Markdown unless[^\n]{0,160}optional field-level encryption"),
    ),
    RequiredClaim(
        "docs/trust.md",
        "remote_optin",
        _rx(r"remote telemetry and weekly feedback reports require separate explicit opt-in"),
    ),
    # Audit log is a LOCAL file that is ON BY DEFAULT (opt out with
    # ENGRAM_AUDIT=0). This is unrelated to network telemetry. These claims
    # lock the default-on behaviour so docs can't silently drift back to the
    # old "opt-in / off by default" wording.
    RequiredClaim(
        "README.md",
        "audit_default_on",
        _rx(r"audit log[^\n]{0,30}on by default"),
    ),
    RequiredClaim(
        "README.zh-CN.md",
        "audit_default_on",
        re.compile(r"审计.{0,6}默认开启", re.DOTALL),
    ),
    RequiredClaim(
        "SECURITY.md",
        "audit_default_on",
        _rx(r"Audit logging \(on by default\)"),
    ),
    # Audit log is plain JSON-lines, NOT the hash-chained ledger (that is the
    # separate governance disclosure ledger). Locks the T3 correction.
    RequiredClaim(
        "SECURITY.md",
        "audit_plain_jsonl",
        _rx(r"audit\.log[^\n]{0,40}plain JSON-lines"),
    ),
    RequiredClaim(
        "PRIVACY.md",
        "audit_default_on",
        _rx(r"audit logging is[^\n]{0,12}on by default"),
    ),
    RequiredClaim(
        "docs/trust.md",
        "audit_default_on",
        _rx(r"audit logging is on by default"),
    ),
    RequiredClaim("README.md", "usage_ping_optout", _rx(r"DO_NOT_TRACK=1")),
    RequiredClaim("README.zh-CN.md", "usage_ping_optout", re.compile(r"DO_NOT_TRACK=1", re.DOTALL)),
    RequiredClaim("PRIVACY.md", "usage_ping_disclosed", _rx(r"one anonymous usage ping a day")),
)


FORBIDDEN_CLAIMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("absolute_no_network", _rx(r"\bEngram\s+(?:makes|performs|uses|does)\s+no\s+network\s+requests\b")),
    ("absolute_no_network", _rx(r"\bno\s+network\s+requests\s+are\s+made\b")),
    # The daily usage ping is on by default (2026-10); old wording must not survive.
    # Only sentence-initial, unqualified phrasings ("Telemetry is off ...") are
    # stale; qualified ones ("Detailed telemetry is off by default") stay true.
    ("stale_telemetry_off_default", re.compile(r"(?:^|(?<=[.;:!?|—-]))\s*\**telemetry\**\s+is\s+\**(?:off|opt-in)\b", re.I | re.M)),
    ("stale_usage_stats_off_default", re.compile(r"(?:^|(?<=[.;:!?|—-]))\s*\**usage statistics\**\s+are\s+\**off\b", re.I | re.M)),
    ("stale_zero_network_row", _rx(r"Network calls by default\s*\|\s*\**0\**\s")),
    ("stale_nothing_leaves", _rx(r"\bNothing leaves the machine\b")),
    ("zh_stale_telemetry_off", re.compile(r"(?:遥测|telemetry)\s*\**默认关闭")),
    ("stale_no_stable_id", _rx(r"No stable cross-day user ID")),
    ("zh_stale_zero_network", re.compile(r"默认零网络|默认 0 次网络请求|身份与知识工具默认 \*\*0\*\*")),
    ("remote_enabled_default", _rx(r"\bremote telemetry\b[^\n.]{0,120}\benabled by default\b")),
    ("feedback_enabled_default", _rx(r"\bfeedback\b[^\n.]{0,120}\benabled by default\b")),
    ("encrypted_by_default", _rx(r"\bencrypted by default\b")),
    ("whole_store_encrypted", _rx(r"\b(?:all data|everything|whole store|the whole store)\b[^\n.]{0,120}\bencrypted\b")),
    ("zh_absolute_no_network", re.compile(r"永远不会发出任何网络请求|完全不会发出网络请求")),
    ("zh_encrypted_default", re.compile(r"默认加密|默认已加密")),
    # Audit log is on by default now; catch any regression to opt-in wording.
    ("audit_off_default", _rx(r"audit log(?:ging)?[^\n]{0,40}off by default")),
    ("audit_optin_label", _rx(r"Audit logging \(opt-in\)")),
    ("zh_audit_off_default", re.compile(r"审计[^\n]{0,20}默认关闭", re.DOTALL)),
)

# Public surfaces that carry no required claim but must not repeat a forbidden
# one. A missing file here is skipped, not a setup error.
FORBIDDEN_ONLY_SURFACES: tuple[str, ...] = (
    "docs/comparison.md",
    "docs/user-guide.md",
    "docs/user-guide.zh-CN.md",
    "docs/trust-evidence.md",
    "skills/engram/SKILL.md",
    "skills/engram/references/privacy.md",
    ".cursor-plugin/README.md",
    "CONTRIBUTING.md",
)

_NEGATION_CONTEXT = re.compile(
    r"\b(?:not|never|isn't|is not|aren't|are not|no longer)\b",
    re.IGNORECASE,
)


def _read(root: Path, rel: str) -> str:
    path = root / rel
    if not path.is_file():
        raise SetupError(f"required public trust surface missing: {rel}")
    return path.read_text(encoding="utf-8", errors="replace")


def _telemetry_endpoints(root: Path) -> dict[str, str]:
    text = _read(root, "src/piia_engram/telemetry.py")
    found: dict[str, str] = {}
    for name, key in (
        ("_DEFAULT_ENDPOINT", "telemetry"),
        ("_DEFAULT_FEEDBACK_ENDPOINT", "feedback"),
    ):
        m = re.search(rf'{name}\s*=\s*"([^"]*)"', text)
        if not m:
            raise SetupError(f"{name} not found in src/piia_engram/telemetry.py")
        found[key] = m.group(1)
    ping = root / "src/piia_engram/usage_ping.py"
    if ping.is_file():
        m = re.search(r'^ENDPOINT\s*=\s*"([^"]*)"', ping.read_text(encoding="utf-8"), re.MULTILINE)
        if m:
            found["usage_ping"] = m.group(1)
    return found


def _is_negated(text: str, match: re.Match[str]) -> bool:
    """Return True when a forbidden-looking phrase is explicitly negated."""
    start = max(0, match.start() - 24)
    context = text[start:match.end()]
    return bool(_NEGATION_CONTEXT.search(context))


def scan(root: Path | str = ".") -> dict:
    """Return a structured report for public trust-claim consistency."""
    root = Path(root).resolve()
    problems: list[dict] = []
    scanned_files = sorted({claim.file for claim in REQUIRED_CLAIMS})
    texts = {rel: _read(root, rel) for rel in scanned_files}
    forbidden_texts = dict(texts)
    for rel in FORBIDDEN_ONLY_SURFACES:
        if rel in forbidden_texts:
            continue
        path = root / rel
        if path.is_file():
            forbidden_texts[rel] = path.read_text(encoding="utf-8", errors="replace")
    endpoints = _telemetry_endpoints(root)

    for claim in REQUIRED_CLAIMS:
        if not claim.pattern.search(texts[claim.file]):
            problems.append({
                "file": claim.file,
                "kind": "missing_required_claim",
                "claim": claim.claim,
            })

    for rel, text in forbidden_texts.items():
        for label, pattern in FORBIDDEN_CLAIMS:
            for m in pattern.finditer(text):
                if _is_negated(text, m):
                    continue
                problems.append({
                    "file": rel,
                    "kind": "forbidden_claim",
                    "claim": label,
                    "match": m.group(0),
                })

    security = texts["SECURITY.md"]
    for endpoint_name, endpoint in endpoints.items():
        # Empty default = no built-in telemetry destination shipped in the
        # open-source core (operators opt in via ENGRAM_TELEMETRY_URL /
        # ENGRAM_FEEDBACK_URL). Nothing concrete to drift-check against.
        if not endpoint:
            continue
        if endpoint not in security:
            problems.append({
                "file": "SECURITY.md",
                "kind": "endpoint_drift",
                "endpoint": endpoint_name,
                "expected": endpoint,
            })

    # De-duplicate while preserving order.
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for problem in problems:
        sig = tuple(sorted(problem.items()))
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(problem)

    return {
        "ok": not deduped,
        "scanned": sorted(forbidden_texts)
        + ["src/piia_engram/telemetry.py"]
        + (["src/piia_engram/usage_ping.py"] if (root / "src/piia_engram/usage_ping.py").is_file() else []),
        "endpoints": endpoints,
        "problems": deduped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--root", default=".", help="Repo root (default: cwd).")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    try:
        result = scan(args.root)
    except SetupError as exc:
        if args.json:
            print(json.dumps({"ok": False, "setup_error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"[error] {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    print(f"Public trust-claim guard — scanned {len(result['scanned'])} surface(s).")
    if result["ok"]:
        print("[OK] security/privacy trust claims are present and consistent.")
        return 0

    for problem in result["problems"]:
        if problem["kind"] == "missing_required_claim":
            print(f"::error::{problem['file']}: missing required trust claim '{problem['claim']}'")
        elif problem["kind"] == "endpoint_drift":
            print(
                f"::error::{problem['file']}: {problem['endpoint']} endpoint drift; "
                f"expected {problem['expected']}"
            )
        else:
            print(
                f"::error::{problem['file']}: forbidden trust claim "
                f"'{problem['claim']}' matched {problem['match']!r}"
            )
    print(f"[FAIL] {len(result['problems'])} public trust-claim problem(s).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
