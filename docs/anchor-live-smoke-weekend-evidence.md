# Anchor / LIVE_SMOKE Weekend Evidence Packet

Date: 2026-06-30
Purpose: prepare the evidence packet for the Cursor forum follow-up after the multi-day Anchor and LIVE_SMOKE run.

## Public Boundary

Do not post raw memory bodies, local paths, repo-private IDs, API keys, debug logs, or user-specific transcripts. The public reply should use aggregate counts, pass/fail rates, sanitized examples, and a short methodology note only.

No public forum reply is sent from this document. Any forum response still needs owner confirmation before posting.

## Current Local Findings

- Project-level Claude folder checked: no Anchor-specific evidence files found.
- Existing repo evidence source: Anchor implementation, Anchor tests, recall trust tests, onboard candidate tests, release evidence markers.
- Current LIVE_SMOKE source: runtime MCP entrypoint smoke added in M9 (`tests/test_mcp_entrypoint_smoke.py`).

## Weekend Data To Merge

- Latest Anchor run window: include dates covered by the week-long run.
- Anchor results: checked, valid, invalid, unknown, superseded, adopted legacy, demoted-to-staging counts.
- LIVE_SMOKE results: runs, passed, failed, failure classes, median duration if available.
- Claude-collected notes: attach only sanitized aggregate observations if a separate file is found before the weekend reply.
- Engram M9 runtime evidence: MCP entrypoint smoke, lightweight closeout, explicit reconcile boundary, quality metadata return.

## Repro Commands

```powershell
pytest tests/test_freshness_anchors.py tests/test_onboard_candidates.py tests/test_onboard_cli_firstvalue.py tests/test_recall_trust.py -q
pytest tests/test_mcp_entrypoint_smoke.py tests/test_mcp_wrap_up_reliability.py -q
python scripts/run_memory_evals.py --json
python scripts/release_sanitize_check.py --internal --strict
```

Use the bundled Codex Python path if the system `python` resolves to the WindowsApps placeholder.

## Collection Command

```powershell
python scripts/collect_anchor_live_smoke_evidence.py --json --synthetic
```

Replace `--synthetic` with `--live --allow-live` only after verifying the live collector emits aggregate metadata and no raw memory bodies.

## Weekend Workflow

```powershell
New-Item -ItemType Directory -Force .engram-local-evidence
python scripts/collect_anchor_live_smoke_evidence.py --json --live --allow-live --out .engram-local-evidence/anchor-live-smoke-weekend.json
python scripts/render_anchor_forum_reply.py --evidence .engram-local-evidence/anchor-live-smoke-weekend.json > .engram-local-evidence/cursor-forum-reply-draft.md
```

Owner confirmation required before posting. Review the draft manually, remove any claim that is not supported by aggregate counts, and do not paste raw memory bodies, local paths, repo-private IDs, debug logs, or transcripts.

## Reply Shape

- One short thanks/continuation sentence to Deanrie.
- Methodology: ran Anchor validation plus LIVE_SMOKE over roughly one week.
- Results: aggregate counts only.
- What changed: guesses no longer silently become facts; anchor-backed facts degrade to review/staging when evidence breaks; unknown checks do not imply falsehood.
- Caveats: local dataset, limited repos/tools, no raw data shared.
- Offer: share sanitized reproduction harness or metrics table if useful.
