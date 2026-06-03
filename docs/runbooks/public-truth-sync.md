# Runbook - Public Truth Sync

**Goal:** keep the facts piia-engram publishes about itself (test count, tool
count, version, telemetry posture) internally consistent, and keep "what the dev
tree says" cleanly separated from "what the public registries say."

This runbook backs the machine-readable manifest
[`docs/public-facts.json`](../public-facts.json) and the guard
[`scripts/check_public_fact_sync.py`](../../scripts/check_public_fact_sync.py).

---

## 1. Released vs dev - the framing that prevents false drift

There are two different kinds of "public fact," and conflating them is what
produced the original drift complaint.

| Kind | Lives in | Changes when | Source of truth |
|---|---|---|---|
| **Dev truth** | this repo's working tree: README, README.zh-CN, architecture.md, manifests | every commit that moves a number | `docs/public-facts.json` |
| **Released truth** | PyPI page, MCP Registry, Glama, Smithery, LobeHub | only during an actual publish/release | the last published artifact |

Key rule: **a gap between the dev tree and a public registry is normal between
releases.** If `pyproject.toml` is `3.47.0` but the PyPI page still shows
`3.28.1`, that is not a doc bug to "fix" by editing README; it is simply
un-published work. The guard only polices *dev truth* (in-repo current-state
docs). It never asserts anything about a remote registry, because this repo
cannot change a registry without a publish.

The `release_frame` field in the manifest states this in one line so any reader
(or AI) loading the manifest gets the framing for free.

---

## 2. The manifest is the single source of truth

`docs/public-facts.json` holds the canonical numbers plus, for each number, the
**exact command/file it came from** (the `sources` block). When a number
changes, you update the manifest *first*, re-derive it from its source command,
then let the guard tell you which docs still disagree.

Required facts: `package_name`, `local_dev_version`, `release_frame`,
`test_passed`, `test_skipped`, `test_collected`, `mcp_tools_total`,
`mcp_tools_core`, `mcp_tools_advanced`, `telemetry_default`,
`telemetry_remote_default`, `last_verified_date`, plus `sources`.

Self-consistency invariants the guard enforces on the manifest itself:

- `test_passed + test_skipped == test_collected`
- `mcp_tools_core + mcp_tools_advanced == mcp_tools_total`
- `local_dev_version == pyproject.toml [project].version`

---

## 3. Re-deriving the numbers (live verification checklist)

Run from the repo root with the project's test interpreter (`ENGRAM_DIR` must be
**cleared** so an ambient data dir does not perturb the suite):

```bash
# version
git tag --sort=-creatordate | head -1          # and: grep ^version pyproject.toml

# tests (passed / skipped)
pytest tests/ -q                                # tail: "<passed> passed, <skipped> skipped"

# tests (collected)
pytest tests/ --collect-only -q                 # tail: "<N> tests collected"

# mcp tool counts - total / core / advanced (deterministic, no package import)
python scripts/count_mcp_tools.py            # human line
python scripts/count_mcp_tools.py --json     # {"total":80,"core":16,"advanced":64}
```

> On this machine there is no system Python on PATH; use the project test
> interpreter and `PYTHONPATH=src` (see the team's test-runner note). Clearing
> `ENGRAM_DIR` is required for a clean full-suite count.

Then update `docs/public-facts.json` (numbers + `last_verified_date`) and run the
guard.

---

## 4. Running the guard

```bash
python scripts/check_public_fact_sync.py            # human report
python scripts/check_public_fact_sync.py --json     # machine-readable
```

Exit `0` = in sync, `1` = drift (it lists each stale doc/value), `2` = setup
error (manifest missing/invalid, or a policed surface missing).

The guard is suitable for CI / release-prep: wire it next to
`scripts/check_release_gate.py` so a release cannot proceed while a current-state
doc disagrees with the manifest.

### What it checks

1. Manifest schema + invariants (section 2).
2. Version-bearing surfaces (`.mcp/server.json`, `.claude-plugin/plugin.json`,
   `glama.yaml`) carry exactly `local_dev_version`.
3. Test-count renderings in `README.md` / `README.zh-CN.md` equal
   `facts.test_passed` (generic: catches any stale number, not just `2346`).
4. Required current-state substrings (the 80 / 16 / 64 tool split) are present.
5. No known-stale string (e.g. `**2346**`) appears in any current-state surface.

> **`current_state_surfaces` is a curated list, not auto-discovery.** Before
> adding a NEW public doc that carries a version / test-count / tool-count claim,
> add it to `current_state_surfaces` (and, if it asserts the tool split or a
> specific number, to `checks.required_substrings` / `checks.test_count_patterns`)
> in `docs/public-facts.json`, then re-run the guard. Otherwise the guard cannot
> see the new surface and drift can re-enter through it.

### What it deliberately does NOT check

- `CHANGELOG.md`, `CHANGELOG.zh-CN.md`, and `release-evidence/**` - these are
  **historical** records and are supposed to carry the numbers that were true at
  their release. Policing them would punish keeping accurate history.
- Any remote registry (PyPI / MCP Registry / Glama / Smithery / LobeHub). Those
  are *released truth* and only change during a publish - out of scope for an
  in-repo guard.

---

## 5. When a number really changed

1. Re-derive it from the `sources` command (section 3).
2. Update `docs/public-facts.json` (value + `last_verified_date`).
3. Run the guard; it lists every current-state doc still on the old value.
4. Update those docs (README "By the numbers", zh mirror, architecture split,
   manifests) to match. Leave CHANGELOG / release-evidence untouched.
5. Re-run the guard to green, then the normal test suite.

## 6. Public (remote) reconciliation - release-gated, NOT done here

**Published-truth sources (out of scope for this guard):** PyPI, MCP Registry,
Glama, Smithery, LobeHub. Each is updated *only* during an official
publish/release - never by editing this repo.

If a public registry genuinely lags (e.g. PyPI shows an older version/long
description), that is fixed **only** by an actual publish/release through the
existing release gate (`scripts/check_release_gate.py`,
`.github/workflows/publish.yml`). Do not claim a registry is "fixed" in docs
until that publish has happened. This runbook and guard cover the *local* half;
the *remote* half is owned by the release process.
