# Runbook: Local release build & check (Phase 12)

Status: **local build/check commands only.** This runbook lists the read-only /
local-only steps to validate a release candidate. **It performs no public or
remote action** — no `git push`, no tag, no GitHub release, no PyPI upload, no
registry/Glama update. Those remain explicit, separately-gated maintainer steps
(see `docs/internal/release-playbook.md`, which is private).

## 0. One-shot readiness snapshot

```bash
python scripts/build_release_dossier.py
python scripts/build_release_dossier.py --run-readiness
python scripts/check_pre_push_release_readiness.py
python scripts/check_pre_push_release_readiness.py --full-tests
engram release-check          # read-only readiness report (exit 1 if NOT ready)
engram release-check --json   # machine-readable
python scripts/release_orchestrator.py --mode prep
python scripts/release_orchestrator.py --mode publish-fast
```

`scripts/check_pre_push_release_readiness.py` is the first local gate before
asking the maintainer to approve any public action. Default mode runs only
local file / git-index read checks (tool-count smoke, publish allowlist,
public-fact sync, trust-claim guard, public-claim drift, publish workflow
ordering). `--full-tests` also runs `python -m pytest -q` for a release
candidate. It performs no `git push`, tag, release, upload, registry write,
deploy, or external listing refresh.

`scripts/build_release_dossier.py` renders the local v.next dossier from git
metadata, public facts, and MCP tool counts. `--run-readiness` embeds the same
local readiness gate. It is a planning artifact only, not a release approval.

`engram release-check` aggregates the checks below into a single status:
required files present, English-first release notes, publish allowlist present,
no reverse-disclosure signals in public docs (generic shapes — personal absolute
paths and the maintainer's private drive — plus any gitignored local private
terms; this committed guard does not enumerate product names), and
release-evidence completeness for the current `pyproject` version. It only reads
the working tree. The precise maintainer-private term scan lives in
`scripts/release_sanitize_check.py` (gitignored term sources).

## 0.5 Authorization preflight (before the publish chain)

Before starting any publish steps, confirm the publishing tools are present and
authorized — this avoids stalling halfway through a release:

```bash
python scripts/check_release_auth_preflight.py            # report + exit 1 if not ready
python scripts/check_release_auth_preflight.py --json     # machine-readable
python scripts/check_release_auth_preflight.py --strict   # warnings also block
python scripts/check_release_auth_preflight.py --check-jwt-age # warn if MCP auth cache looks stale
python scripts/check_release_auth_preflight.py --warm-mcp # refresh MCP Registry auth
```

It checks (required unless noted): GitHub CLI auth (`gh auth status`),
`mcp-publisher` availability, `.mcp/server.json` validity + version match with
`pyproject.toml`, and `twine` runnability. A local PyPI credential source is
reported as present/absent only (informational — CI publishes via OIDC trusted
publishing). Cloudflare/Wrangler is out of scope unless `--include-wrangler`.

`--warm-mcp` is the fast-path fix for the MCP Registry login stall. It runs
`gh auth token`, passes that token directly to
`mcp-publisher login github -token <token>`, and reports only success/failure.
It never prints the token and it does **not** push, tag, upload, publish, or
write a registry entry. Run it before remote release steps so a stale MCP
Registry token fails early instead of blocking inside `mcp-publisher publish`.

`--check-jwt-age` is the lightweight warning gate. It inspects only the token
cache file timestamp (never the path, value, or contents) and warns when the
cache looks older than the safe window. Treat that warning as a cue to run
`--warm-mcp` before the remote publish chain.

This check is **non-secret and publish-safe**: default mode never reads token
values; `--warm-mcp` reads a GitHub CLI token only to refresh local
`mcp-publisher` auth and never logs the value. Output is safe to paste publicly.
It fails closed (exit 1) with an actionable message for each gap.

## 1. Local check commands (run from repo root)

```bash
# CI-like import gate: catches tests that only pass when repo root is on sys.path
python scripts/check_ci_pytest_entrypoint.py --discover-script-imports

# Full test suite (must be green before shipping)
python -m pytest tests -q

# Public truth drift guard (README/docs/manifests must match docs/public-facts.json)
python scripts/check_public_fact_sync.py

# Tracked-tree private-term sanitizer (high=0 required)
python scripts/release_sanitize_check.py --staged --strict   # staged only
python scripts/release_sanitize_check.py --strict            # whole tree

# Publish allowlist: every tracked file must be allowlisted
python scripts/check_publish_allowlist.py

# Deterministic release gate: evidence file must be complete
python scripts/check_release_gate.py            # current pyproject version
```

## 2. Build the artifacts locally (no upload)

```bash
python -m build                                  # wheel + sdist into dist/

# Scan the BUILT artifacts for private terms (post-build, complements §1)
python scripts/check_release_artifact_private_terms.py dist --strict

# Metadata sanity (does not upload)
python -m twine check dist/*
```

If a local PyPI upload is needed after a partial release attempt, use
`python -m twine upload --skip-existing dist/*` so already-uploaded wheel/sdist
files do not turn the closeout into a false blocker. CI trusted publishing is
still the preferred normal path.

`PROJECT_REGISTRY.md` lives in the parent workspace, not inside this git repo.
Update it after a successful push/release as a workspace synchronization step;
do not treat it as an Engram repo commit blocker.

## 2.5 Fast publish path after explicit release confirmation

Once release-prep evidence is current and the maintainer has confirmed release,
the hot path is intentionally short:

```bash
python scripts/release_orchestrator.py --mode publish-fast --probe
git push origin main --tags
gh release create vX.Y.Z --title "..." --notes-file <public-notes.md>
# watch the release-triggered Publish to PyPI workflow
python scripts/publish_mcp_registry.py .mcp/server.json
python scripts/verify_mcp_registry_version.py --version X.Y.Z
```

Do **not** block the publish-complete report on workspace bookkeeping. After the
channels are verified, update `PROJECT_REGISTRY.md`, Engram memories, and the
private Core Self Optimization notes as post-release closeout.

## 3. Release notes are English-first

The English `README.md` / `CHANGELOG.md` are the primary; `README.zh-CN.md` /
`CHANGELOG.zh-CN.md` are the translations. Release-evidence and listing copy put
English first, then Chinese (enforced by
`tests/test_public_positioning.py::test_release_notes_bilingual_order_is_documented`).

## 4. What stays user-gated (NOT in this runbook)

- `git push` to origin, tags, GitHub releases.
- `twine upload` to PyPI.
- Registry / Glama / Smithery updates.
- Cloudflare worker deploy and any remote D1 migration (see
  `docs/runbooks/telemetry-contract-v1-remote-closeout.md`).

Only proceed to those after `engram release-check` is `READY`, the full suite is
green, and the release-evidence file records every mandatory gate as passed.
