"""Release authorization preflight (local, non-secret).

Turns the manual "is my publishing chain authorized?" checklist into a
repo-local check so a release does not stall halfway through. It verifies the
*availability and authorization* of the tools a release needs — it NEVER prints
token values and it NEVER performs a public/remote action (no push, tag,
upload, or registry write).

Checks (required unless noted):
- GitHub CLI: ``gh`` is on PATH and ``gh auth status`` succeeds.
- MCP publisher: ``mcp-publisher`` is on PATH or at a known local fallback path
  (needed for the MCP Registry).
- ``.mcp/server.json``: structurally valid, and (when ``mcp-publisher`` is
  available) passes ``mcp-publisher validate``. Its version is compared against
  ``pyproject.toml`` (mismatch is a blocking error).
- PyPI/Twine: ``twine`` is importable/runnable. A credential SOURCE is reported
  as present/absent only (never its value); absence is informational because CI
  publishes via OIDC trusted publishing.
- Cloudflare/Wrangler: out of scope by default; pass ``--include-wrangler`` to
  add an availability-only check.

Run from repo root:

    python scripts/check_release_auth_preflight.py            # report + exit 1 if not ready
    python scripts/check_release_auth_preflight.py --json     # machine-readable
    python scripts/check_release_auth_preflight.py --strict   # warnings also block
    python scripts/check_release_auth_preflight.py --warm-mcp # refresh MCP Registry auth

Exit codes:
- 0  every required check passed (and, with --strict, no warnings)
- 1  one or more required checks failed (or a warning, with --strict)
- 2  setup error (no pyproject.toml / not a repo root)

Security: this script reads only *presence* of credential sources. It does not
read, log, or echo any token value, and its output is safe to paste publicly.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Status constants for a single check result.
OK = "ok"
FAIL = "fail"
WARN = "warn"
SKIP = "skip"

DEFAULT_MCP_PUBLISHER_CANDIDATES = (
    r"E:\Temp\mcp-publisher.exe",
    r"E:\Temp\mcp-publisher-v1.7.9-windows-amd64\mcp-publisher.exe",
)


def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str, str]:
    """Run a command, returning (returncode, stdout, stderr).

    Output is captured (not streamed) so the caller decides what — if anything —
    to surface. A missing executable or timeout maps to a non-zero code so every
    caller can fail closed without special-casing exceptions.
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", "executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except OSError as exc:  # pragma: no cover - environment dependent
        return 1, "", f"os error: {exc.__class__.__name__}"


def _result(name: str, status: str, required: bool, detail: str) -> dict:
    """A single check result. ``detail`` MUST be free of any secret value."""
    return {"name": name, "status": status, "required": required, "detail": detail}


def _pyproject_version(root: Path) -> str | None:
    """Read ``version = "x.y.z"`` from ``[project]`` in pyproject.toml.

    Uses a tiny line scan instead of a TOML parser so this script has no
    dependency beyond the stdlib (matching the other release scripts).
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    in_project = False
    for raw in pyproject.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if in_project and line.startswith("version") and "=" in line:
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def check_github_cli(which=shutil.which, run=_run) -> dict:
    """``gh`` present on PATH and authenticated.

    Only the exit code of ``gh auth status`` is consulted — its output is never
    surfaced, so no (even masked) token text can leak into this report.
    """
    if not which("gh"):
        return _result(
            "github_cli_auth", FAIL, True,
            "GitHub CLI 'gh' not found on PATH (install it, then 'gh auth login').",
        )
    rc, _out, _err = run(["gh", "auth", "status"])
    if rc == 0:
        return _result("github_cli_auth", OK, True, "gh is authenticated.")
    return _result(
        "github_cli_auth", FAIL, True,
        "gh is installed but not authenticated (run 'gh auth login').",
    )


def _resolve_mcp_publisher(which=shutil.which, candidates=None) -> str | None:
    """Resolve MCP Publisher without requiring PATH-only installation."""
    found = which("mcp-publisher")
    if found:
        return found
    for raw in candidates or DEFAULT_MCP_PUBLISHER_CANDIDATES:
        path = Path(raw)
        if path.is_file():
            return str(path)
    return None


def check_mcp_publisher(which=shutil.which, candidates=None) -> dict:
    """``mcp-publisher`` executable available (needed for MCP Registry)."""
    if _resolve_mcp_publisher(which=which, candidates=candidates):
        return _result("mcp_publisher", OK, True, "mcp-publisher is available.")
    return _result(
        "mcp_publisher", FAIL, True,
        "mcp-publisher not found on PATH or known local fallback paths.",
    )


def warm_mcp_registry_auth(which=shutil.which, run=_run, candidates=None) -> dict:
    """Refresh MCP Registry auth non-interactively via GitHub CLI.

    This is intentionally separate from the default preflight: it reads a token
    from ``gh auth token`` and passes it directly to ``mcp-publisher login
    github -token <token>``. The token is never printed, logged, or returned in
    the result detail. The operation refreshes local publisher auth only; it
    does not push, tag, upload, or publish.
    """
    if not which("gh"):
        return _result(
            "mcp_registry_auth_warm", FAIL, True,
            "GitHub CLI 'gh' not found on PATH (install it, then 'gh auth login').",
        )
    publisher = _resolve_mcp_publisher(which=which, candidates=candidates)
    if not publisher:
        return _result(
            "mcp_registry_auth_warm", FAIL, True,
            "mcp-publisher not found on PATH or known local fallback paths.",
        )

    rc, out, _err = run(["gh", "auth", "token"])
    token = out.strip()
    if rc != 0 or not token:
        return _result(
            "mcp_registry_auth_warm", FAIL, True,
            "could not read a GitHub CLI token (run 'gh auth login' first).",
        )

    rc, _out, _err = run([publisher, "login", "github", "-token", token], timeout=60)
    if rc == 0:
        return _result(
            "mcp_registry_auth_warm", OK, True,
            "mcp-publisher GitHub token login succeeded (token value not printed).",
        )
    return _result(
        "mcp_registry_auth_warm", FAIL, True,
        "mcp-publisher GitHub token login failed.",
    )


def validate_server_json(path: Path) -> tuple[bool, str, str | None]:
    """Structural validation of an MCP ``server.json``.

    Returns ``(ok, detail, version)``. ``ok`` is False with an actionable detail
    if the file is missing, not JSON, or missing a required field. ``version``
    is the manifest version when readable (used for the pyproject cross-check).
    """
    if not path.is_file():
        return False, f"{path.as_posix()} not found.", None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"{path.name} is not valid JSON (line {exc.lineno}).", None
    if not isinstance(data, dict):
        return False, f"{path.name} top level must be a JSON object.", None
    missing = [k for k in ("name", "version", "packages") if k not in data]
    if missing:
        return False, f"{path.name} missing required field(s): {', '.join(missing)}.", None
    if not isinstance(data.get("packages"), list) or not data["packages"]:
        return False, f"{path.name} 'packages' must be a non-empty array.", None
    version = data.get("version")
    return True, f"{path.name} is structurally valid.", str(version) if version else None


def check_server_json(root: Path, which=shutil.which, run=_run, candidates=None) -> list[dict]:
    """Validate ``.mcp/server.json`` (structure, publisher, version match).

    Returns one or more results: the structural/publisher check plus a version
    cross-check against pyproject. Version mismatch is a blocking error because
    publishing a manifest whose version disagrees with the package is a real
    release defect.
    """
    server = root / ".mcp" / "server.json"
    ok, detail, manifest_version = validate_server_json(server)
    results: list[dict] = []
    if not ok:
        results.append(_result("mcp_server_json", FAIL, True, detail))
        return results

    # If the publisher CLI is available, let it validate authoritatively too.
    publisher = _resolve_mcp_publisher(which=which, candidates=candidates)
    if publisher:
        rc, _out, _err = run([publisher, "validate", str(server)])
        if rc == 0:
            results.append(
                _result("mcp_server_json", OK, True,
                        "server.json valid (structural + mcp-publisher validate).")
            )
        else:
            results.append(
                _result("mcp_server_json", FAIL, True,
                        "mcp-publisher validate failed for .mcp/server.json.")
            )
    else:
        results.append(
            _result("mcp_server_json", OK, True,
                    "server.json structurally valid (mcp-publisher not available "
                    "for authoritative validation).")
        )

    # Version cross-check against pyproject.
    pyproject_version = _pyproject_version(root)
    if manifest_version and pyproject_version:
        if manifest_version == pyproject_version:
            results.append(
                _result("mcp_version_match", OK, True,
                        f"server.json version matches pyproject ({pyproject_version}).")
            )
        else:
            results.append(
                _result(
                    "mcp_version_match", FAIL, True,
                    f"version mismatch: server.json={manifest_version} "
                    f"pyproject={pyproject_version} (sync before publishing).",
                )
            )
    return results


def _has_pypi_credential_source(env) -> bool:
    """True if SOME PyPI credential source is present — value is never read.

    Checks only for the *existence* of common sources: twine env vars or a
    ``~/.pypirc``. It deliberately does not read, parse, or return any value.
    """
    for key in ("TWINE_PASSWORD", "TWINE_API_KEY", "TWINE_USERNAME"):
        value = env.get(key)
        if value is not None and str(value).strip():
            return True
    try:
        if (Path.home() / ".pypirc").is_file():
            return True
    except (OSError, RuntimeError):  # pragma: no cover - home not resolvable
        pass
    return False


def check_twine(run=_run, env=None, python=sys.executable) -> list[dict]:
    """``twine`` runnable, plus presence-only PyPI credential reporting.

    twine availability is required (it is the local metadata/upload tool). A
    credential SOURCE being absent is a warning, not a failure, because the CI
    publish path uses OIDC trusted publishing and needs no stored token.
    """
    env = os.environ if env is None else env
    results: list[dict] = []
    rc, _out, _err = run([python, "-m", "twine", "--version"])
    if rc == 0:
        results.append(_result("twine_available", OK, True, "twine is runnable."))
    else:
        results.append(
            _result("twine_available", FAIL, True,
                    "twine not runnable (pip install twine).")
        )
    if _has_pypi_credential_source(env):
        # Presence only — never the value, never which one beyond category.
        results.append(
            _result("pypi_credential_source", OK, False,
                    "a PyPI credential source is present (value not read).")
        )
    else:
        results.append(
            _result(
                "pypi_credential_source", WARN, False,
                "no local PyPI credential source detected — fine if publishing "
                "via CI OIDC trusted publishing; set one only for local upload.",
            )
        )
    return results


def check_wrangler(include: bool, which=shutil.which) -> dict:
    """Cloudflare Wrangler availability — opt-in, never required."""
    if not include:
        return _result("wrangler", SKIP, False,
                       "Cloudflare/Wrangler not in scope (use --include-wrangler).")
    if which("wrangler"):
        return _result("wrangler", OK, False, "wrangler is available.")
    return _result("wrangler", WARN, False,
                   "wrangler not found on PATH (only needed for worker deploys).")


def run_preflight(
    root: Path,
    *,
    include_wrangler: bool = False,
    warm_mcp: bool = False,
    which=shutil.which,
    run=_run,
    env=None,
    python=sys.executable,
    mcp_publisher_candidates=None,
) -> tuple[bool, list[dict]]:
    """Run all checks. Returns ``(all_required_passed, results)``."""
    results: list[dict] = []
    results.append(check_github_cli(which=which, run=run))
    results.append(check_mcp_publisher(which=which, candidates=mcp_publisher_candidates))
    if warm_mcp:
        results.append(warm_mcp_registry_auth(
            which=which,
            run=run,
            candidates=mcp_publisher_candidates,
        ))
    results.extend(check_server_json(
        root,
        which=which,
        run=run,
        candidates=mcp_publisher_candidates,
    ))
    results.extend(check_twine(run=run, env=env, python=python))
    results.append(check_wrangler(include_wrangler, which=which))
    required_ok = all(r["status"] == OK for r in results if r["required"])
    return required_ok, results


def _print_human(results: list[dict], required_ok: bool, strict: bool) -> None:
    marker = {OK: "[ok]", FAIL: "[FAIL]", WARN: "[warn]", SKIP: "[skip]"}
    print("Release authorization preflight (local, non-secret):")
    for r in results:
        req = "required" if r["required"] else "optional"
        print(f"  {marker.get(r['status'], '[?]')} {r['name']} ({req}): {r['detail']}")
    warns = [r for r in results if r["status"] == WARN]
    if required_ok and not (strict and warns):
        print("[OK] release authorization preflight passed.")
    else:
        if not required_ok:
            failed = [r["name"] for r in results if r["required"] and r["status"] != OK]
            print(f"::error::release auth preflight failed: {', '.join(failed)}")
        if strict and warns:
            print(f"::error::--strict: unresolved warning(s): "
                  f"{', '.join(r['name'] for r in warns)}")
        print("Resolve the items above before starting the publish chain. "
              "No token values are read or printed by this check.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else ""
    )
    ap.add_argument("--root", default=".", help="Repo root (default: cwd).")
    ap.add_argument("--json", action="store_true", help="Machine-readable output.")
    ap.add_argument("--strict", action="store_true",
                    help="Treat warnings as blocking (exit 1).")
    ap.add_argument("--include-wrangler", action="store_true",
                    help="Also check Cloudflare Wrangler availability.")
    ap.add_argument("--warm-mcp", action="store_true",
                    help=("Refresh MCP Registry auth via 'gh auth token' + "
                          "'mcp-publisher login github -token <token>'. "
                          "No publish action; token value is never printed."))
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not (root / "pyproject.toml").is_file():
        print(f"[error] no pyproject.toml under {root} (run from repo root).",
              file=sys.stderr)
        return 2

    required_ok, results = run_preflight(
        root,
        include_wrangler=args.include_wrangler,
        warm_mcp=args.warm_mcp,
    )
    warns = [r for r in results if r["status"] == WARN]
    passed = required_ok and not (args.strict and warns)

    if args.json:
        print(json.dumps(
            {"passed": passed, "required_ok": required_ok, "strict": args.strict,
             "results": results},
            ensure_ascii=False, indent=2,
        ))
    else:
        _print_human(results, required_ok, args.strict)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
