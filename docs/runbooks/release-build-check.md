# Runbook: Local release build & check (Phase 12)

Status: **local build/check commands only.** This runbook lists the read-only /
local-only steps to validate a release candidate. **It performs no public or
remote action** — no `git push`, no tag, no GitHub release, no PyPI upload, no
registry/Glama update. Those remain explicit, separately-gated maintainer steps
(see `docs/internal/release-playbook.md`, which is private).

## 0. One-shot readiness snapshot

```bash
engram release-check          # read-only readiness report (exit 1 if NOT ready)
engram release-check --json   # machine-readable
```

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
```

It checks (required unless noted): GitHub CLI auth (`gh auth status`),
`mcp-publisher` availability, `.mcp/server.json` validity + version match with
`pyproject.toml`, and `twine` runnability. A local PyPI credential source is
reported as present/absent only (informational — CI publishes via OIDC trusted
publishing). Cloudflare/Wrangler is out of scope unless `--include-wrangler`.

This check is **non-secret and local-only**: it never reads, logs, or prints any
token value, performs no network/publish action, and its output is safe to paste
publicly. It fails closed (exit 1) with an actionable message for each gap.

## 1. Local check commands (run from repo root)

```bash
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
