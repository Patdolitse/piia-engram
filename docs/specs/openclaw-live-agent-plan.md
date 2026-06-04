# OpenClaw Live Agent Validation Plan

> Status: plan only. Current verified evidence is **L3 static snapshot A/B**.
> OpenClaw live agent behavior is not yet verified.

## Goal

Verify whether an OpenClaw live agent can actually use Engram-exported context in
a realistic local run, without using the user's main OpenClaw auth store and
without polluting the live Engram store.

## Current Evidence

- Passed: Engram can export OpenClaw-compatible `SOUL.md`, `USER.md`, and
  `MEMORY.md`.
- Passed: OpenClaw `oc-path` can parse a marker lesson from the exported
  `MEMORY.md`.
- Passed: Empty `MEMORY.md` baseline has zero marker matches.
- Passed: Live Engram lessons/decisions files were unchanged during validation.
- Blocked: `openclaw agent --local --agent main` stopped because the isolated
  profile had no provider API key/auth profile.

## Non-Goals

- Do not use the user's main OpenClaw profile for validation.
- Do not copy broad private auth stores into the run directory.
- Do not claim L4 or live model continuity from static `oc-path` evidence.
- Do not turn Engram into an OpenClaw plugin.

## Proposed Test Design

1. Create an isolated run directory under a temporary root.
2. Copy a test Engram store into the run directory.
3. Seed one synthetic marker lesson into the copied store only.
4. Export OpenClaw files from the copied store into `workspace_with`.
5. Create `workspace_without` with an empty `MEMORY.md`.
6. Configure a dedicated isolated OpenClaw profile for the run.
7. Configure a low-cost provider auth profile only inside that isolated profile.
8. Run `openclaw agent --local --agent main` against both workspaces with the
   same prompt.
9. Pass only if the Engram-exported arm returns the marker phrase and the
   baseline does not.
10. Compare live Engram file hashes before and after.

## Required Evidence Files

Use the standard evidence contract from
`docs/runbooks/agent-client-validation.md`:

- `run_meta.json`
- `tool_locations.json`
- `client_config_summary.txt`
- `prompts/`
- `raw/`
- `parsed/`
- `timings.json`
- `zero_pollution.txt`
- `REPORT.md`
- `OPTIMIZATION_NOTES.md`

Local/internal `REPORT.md` and `OPTIMIZATION_NOTES.md` should be written in
Chinese by default. Public summaries should be English-first bilingual and
metadata-only.

## Pass / Fail Rules

Pass:

- Isolated profile runs successfully.
- Engram-exported arm returns the marker phrase.
- Baseline arm does not return the marker phrase.
- No live Engram data files change.
- No raw auth secrets are written into public evidence files.

Fail or blocked:

- Provider auth is missing.
- OpenClaw reads from a non-isolated profile.
- Baseline also returns the marker.
- The run changes the live Engram store.
- The agent uses broad native memory/search and the marker source cannot be
  attributed to the exported files.

## Public Claim Boundary

Until this plan passes, the only safe public wording is:

"OpenClaw-compatible static file bridge is verified to L3 static snapshot A/B.
OpenClaw live agent behavior is not yet verified."
