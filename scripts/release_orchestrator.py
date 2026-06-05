"""Release orchestrator -- dry-run, auth-visible release checklist (local only).

WHY: releases were stalling. The local pre-publish checks are fast, but the
*publish chain* (push -> GitHub Release -> PyPI -> MCP Registry -> Glama)
interleaves steps that can silently block on an interactive auth prompt. The
highest-friction case is ``mcp-publisher publish`` with a stale token. The auth
preflight now has a ``--warm-mcp`` mode that refreshes the publisher token
non-interactively via ``gh auth token`` before any remote publish step begins.

This orchestrator makes the whole pipeline visible BEFORE anything blocks. It
emits an ordered checklist grouped into three phases --

    LOCAL   : pure local checks, no auth, safe to run anytime
    AUTH    : authorization that must be live before the publish chain starts
    REMOTE  : irreversible, user-gated actions (push / release / publish)

-- and for every step states whether auth is required, which kind (OIDC /
device-flow / token / none), the token env var (by NAME only, never a value), the
human-visible command, the stall risk, and a timeout hint. It is a *dry run*: it
NEVER pushes, tags, uploads, or authenticates. With ``--probe`` it additionally
reports local *presence* booleans (is ``gh`` on PATH? is a token env var set?) --
booleans only; no secret value is ever read or printed.

    python scripts/release_orchestrator.py             # human checklist
    python scripts/release_orchestrator.py --json       # metadata-only JSON
    python scripts/release_orchestrator.py --probe       # + local presence booleans
    python scripts/release_orchestrator.py --probe --strict   # exit 1 if an auth gap is open

Exit codes:
- 0  dry-run rendered (default), or --probe with no blocking auth gap
- 1  --probe --strict and at least one required auth presence check is missing
- 2  setup error (not a repo root)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# Phases, in execution order.
LOCAL = "local"
AUTH = "auth"
REMOTE = "remote"

# Auth kinds.
NONE = "none"
OIDC = "oidc"
DEVICE_FLOW = "device_flow"
TOKEN = "token"

DEFAULT_MCP_PUBLISHER_CANDIDATES = (
    r"E:\Temp\mcp-publisher.exe",
    r"E:\Temp\mcp-publisher-v1.7.9-windows-amd64\mcp-publisher.exe",
)


def _step(
    sid: str,
    phase: str,
    title: str,
    command: str,
    *,
    auth_required: bool = False,
    auth_kind: str = NONE,
    token_env: str | None = None,
    blocking: bool = True,
    stall_risk: str | None = None,
    timeout_hint: str | None = None,
    preflight_covered: bool = False,
    probe: str | None = None,
) -> dict:
    """One checklist step. ``probe`` names the local presence check (if any) that
    ``--probe`` can evaluate to a boolean -- it is the KEY, never a secret value."""
    return {
        "id": sid,
        "phase": phase,
        "title": title,
        "command": command,
        "auth_required": auth_required,
        "auth_kind": auth_kind,
        "token_env": token_env,
        "blocking": blocking,
        "stall_risk": stall_risk,
        "timeout_hint": timeout_hint,
        "preflight_covered": preflight_covered,
        "probe": probe,
    }


def build_checklist() -> list[dict]:
    """The full, ordered release pipeline as metadata-only steps (pure)."""
    return [
        # ---- LOCAL: no auth, safe anytime ---------------------------------
        _step("tests", LOCAL, "Full test suite",
              "python -m pytest tests/ -q",
              timeout_hint="~2-3 min"),
        _step("fact_sync", LOCAL, "Public-fact drift guard",
              "python scripts/check_public_fact_sync.py"),
        _step("claim_drift", LOCAL, "Public claim drift sweep",
              "python scripts/check_public_claim_drift.py"),
        _step("sanitize", LOCAL, "Secret + strategy disclosure scan",
              "python scripts/release_sanitize_check.py --internal --strict"),
        _step("allowlist", LOCAL, "Default-deny publish allowlist",
              "python scripts/check_publish_allowlist.py"),
        _step("gate", LOCAL, "Review-evidence gate",
              "python scripts/check_release_gate.py"),
        _step("build", LOCAL, "Build wheel + sdist",
              "python -m build"),
        _step("artifact_scan", LOCAL, "Built-artifact private-term scan",
              "python scripts/check_release_artifact_private_terms.py dist --strict"),
        _step("twine_check", LOCAL, "Package metadata sanity",
              "python -m twine check dist/*"),
        # ---- AUTH: must be live BEFORE the publish chain ------------------
        _step("gh_auth", AUTH, "GitHub CLI authenticated",
              "gh auth status   # if not: gh auth login",
              auth_required=True, auth_kind=DEVICE_FLOW, token_env="GITHUB_TOKEN",
              stall_risk="gh release create blocks on device-flow login if the "
                         "session is expired/absent.",
              timeout_hint="login is interactive -- do it first, not mid-release",
              preflight_covered=True, probe="gh_on_path"),
        _step("mcp_publisher_present", AUTH, "mcp-publisher binary available",
              "where mcp-publisher   # or known local fallback path",
              auth_required=False, auth_kind=NONE,
              preflight_covered=True, probe="mcp_publisher_on_path"),
        _step("mcp_publisher_auth", AUTH, "Warm MCP Registry auth",
              "python scripts/check_release_auth_preflight.py --warm-mcp",
              auth_required=True, auth_kind=TOKEN, token_env="GITHUB_TOKEN",
              blocking=True,
              stall_risk="PRIMARY HIDDEN STALL AVOIDED: warm-mcp reads the GitHub "
                         "CLI token and runs 'mcp-publisher login github -token "
                         "<token>' before REMOTE steps, so publish should not open "
                         "a hidden device-flow prompt.",
              timeout_hint="run before REMOTE steps; it refreshes auth only, "
                           "not publish",
              preflight_covered=True, probe="gh_on_path"),
        _step("mcp_version_match", AUTH, ".mcp/server.json version == pyproject",
              "python scripts/check_release_auth_preflight.py",
              auth_required=False, preflight_covered=True, probe=None),
        _step("twine_available", AUTH, "twine runnable (local upload path)",
              "python -m twine --version",
              auth_required=False, preflight_covered=True, probe="twine_runnable"),
        _step("pypi_oidc", AUTH, "PyPI Trusted Publishing configured",
              "(one-time) configure OIDC trust for Patdolitse/piia-engram on pypi.org",
              auth_required=True, auth_kind=OIDC, token_env=None,
              blocking=False,
              stall_risk="If OIDC trust is misconfigured the publish.yml action "
                         "fails the OIDC negotiation (looks like an auth error). "
                         "Not checkable locally -- verify once on pypi.org.",
              preflight_covered=False, probe=None),
        # ---- REMOTE: irreversible, user-gated ------------------------------
        _step("git_push", REMOTE, "Push commit + tag",
              "git push origin main --tags",
              auth_required=True, auth_kind=DEVICE_FLOW, token_env="GITHUB_TOKEN",
              timeout_hint="fast if gh/git auth is live"),
        _step("gh_release", REMOTE, "Create GitHub Release (triggers publish.yml)",
              "gh release create vX.Y.Z --title ... --notes ...",
              auth_required=True, auth_kind=DEVICE_FLOW, token_env="GITHUB_TOKEN",
              stall_risk="blocks on device-flow if gh auth is not live (see gh_auth)"),
        _step("pypi_publish", REMOTE, "PyPI publish (CI, OIDC trusted publishing)",
              "(automatic) publish.yml -> pypa/gh-action-pypi-publish@release/v1",
              auth_required=True, auth_kind=OIDC, token_env=None,
              stall_risk="no token needed; fails only if OIDC trust misconfigured",
              timeout_hint="watch the Actions run; ~1-2 min"),
        _step("mcp_publish", REMOTE, "MCP Registry publish",
              "cd .mcp && mcp-publisher publish   # wait ~10s after PyPI propagates",
              auth_required=True, auth_kind=DEVICE_FLOW, token_env=None,
              stall_risk="device-flow prompt if mcp_publisher_auth was skipped",
              timeout_hint="pre-authorize (mcp_publisher_auth) to avoid the stall"),
        _step("glama_manual_auth", REMOTE, "Glama manual/auth visibility",
              "(manual) verify Glama listing after GitHub/PyPI/MCP publish",
              auth_required=False, blocking=False,
              stall_risk="high",
              timeout_hint="passive auto-detection can lag; if manual refresh is "
                           "needed, do it in a visible browser session"),
    ]


# ---- Optional local presence probes (booleans only, never secret values) ----

def probe_presence(which=shutil.which, env=None) -> dict[str, bool]:
    """Local presence booleans for the steps that declare a ``probe`` key.

    Reads only PATH availability and env-var PRESENCE -- never any value.
    ``mcp_publisher_auth`` is covered by the auth preflight's ``--warm-mcp`` mode,
    so its probe only verifies that GitHub CLI is present before that command can
    run.
    """
    env = os.environ if env is None else env

    def _mcp_present() -> bool:
        if which("mcp-publisher"):
            return True
        explicit = str(env.get("MCP_PUBLISHER_PATH", "")).strip()
        if explicit and Path(explicit).is_file():
            return True
        return any(Path(raw).is_file() for raw in DEFAULT_MCP_PUBLISHER_CANDIDATES)

    def _token_present() -> bool:
        for key in ("TWINE_PASSWORD", "TWINE_API_KEY", "TWINE_USERNAME"):
            v = env.get(key)
            if v is not None and str(v).strip():
                return True
        try:
            return (Path.home() / ".pypirc").is_file()
        except (OSError, RuntimeError):  # pragma: no cover
            return False

    return {
        "gh_on_path": bool(which("gh")),
        "mcp_publisher_on_path": _mcp_present(),
        "twine_runnable": bool(which("twine")) or bool(which("python")),
        "pypi_credential_source": _token_present(),
    }


def build_report(*, probe: bool = False, which=shutil.which, env=None) -> dict:
    """Assemble the metadata-only orchestration report.

    When ``probe`` is True, each step's ``probe`` key is resolved to a boolean
    under ``presence`` and an ``auth_gaps`` list is computed: required auth steps
    whose presence probe is False, PLUS the always-listed un-probeable gaps such
    as ``pypi_oidc`` flagged as ``human_verify``.
    """
    steps = build_checklist()
    report: dict = {
        "dry_run": True,
        "phases": [LOCAL, AUTH, REMOTE],
        "steps": steps,
        "counts": {
            "total": len(steps),
            "auth_required": sum(1 for s in steps if s["auth_required"]),
            "remote_actions": sum(1 for s in steps if s["phase"] == REMOTE),
            "uncovered_by_preflight": sum(
                1 for s in steps if s["auth_required"] and not s["preflight_covered"]
            ),
        },
    }
    if probe:
        presence = probe_presence(which=which, env=env)
        report["presence"] = presence
        gaps = []
        for s in steps:
            if s["auth_required"] and s["probe"] and not presence.get(s["probe"], False):
                gaps.append({"id": s["id"], "kind": "presence_missing",
                             "title": s["title"]})
        # Un-probeable but high-risk steps are always surfaced for human verify.
        for s in steps:
            if s["auth_required"] and s["probe"] is None and not s["preflight_covered"]:
                gaps.append({"id": s["id"], "kind": "human_verify",
                             "title": s["title"]})
        report["auth_gaps"] = gaps
    return report


def _print_human(report: dict) -> None:
    phase_label = {LOCAL: "LOCAL  (no auth -- safe anytime)",
                   AUTH: "AUTH   (must be live BEFORE publish chain)",
                   REMOTE: "REMOTE (irreversible, user-gated)"}
    print("Release orchestrator -- DRY RUN (no push / no publish / no auth performed)")
    print("=" * 72)
    presence = report.get("presence")
    for phase in report["phases"]:
        print(f"\n## {phase_label[phase]}")
        for s in report["steps"]:
            if s["phase"] != phase:
                continue
            tag = ""
            if s["auth_required"]:
                tag = f"  [AUTH:{s['auth_kind']}]"
                if not s["preflight_covered"]:
                    tag += " [NOT preflight-covered]"
            mark = " "
            if presence is not None and s["probe"]:
                mark = "+" if presence.get(s["probe"]) else "!"
            print(f"  [{mark}] {s['id']}: {s['title']}{tag}")
            print(f"        $ {s['command']}")
            if s["token_env"]:
                print(f"        token env: {s['token_env']} (presence only; value never read)")
            if s["stall_risk"]:
                print(f"        ! stall: {s['stall_risk']}")
            if s["timeout_hint"]:
                print(f"        ~ {s['timeout_hint']}")
    c = report["counts"]
    print("\n" + "-" * 72)
    print(f"steps={c['total']}  auth_required={c['auth_required']}  "
          f"remote_actions={c['remote_actions']}  "
          f"uncovered_by_preflight={c['uncovered_by_preflight']}")
    if "auth_gaps" in report:
        if report["auth_gaps"]:
            print("OPEN AUTH GAPS (resolve before REMOTE steps):")
            for g in report["auth_gaps"]:
                print(f"  - {g['id']} ({g['kind']}): {g['title']}")
        else:
            print("No probeable auth gaps detected (human-verify steps still apply).")
    print("This is a dry run. No remote action or authentication was performed.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--root", default=".", help="Repo root (default: cwd).")
    ap.add_argument("--json", action="store_true", help="Metadata-only JSON output.")
    ap.add_argument("--probe", action="store_true",
                    help="Add local presence booleans (never reads secret values).")
    ap.add_argument("--strict", action="store_true",
                    help="With --probe: exit 1 if a probeable auth gap is open.")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not (root / "pyproject.toml").is_file():
        print(f"[error] no pyproject.toml under {root} (run from repo root).",
              file=sys.stderr)
        return 2

    report = build_report(probe=args.probe)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)

    if args.probe and args.strict:
        probeable_gaps = [g for g in report.get("auth_gaps", [])
                          if g["kind"] == "presence_missing"]
        if probeable_gaps:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
