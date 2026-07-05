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

## Packet Finalizer Workflow

```powershell
python scripts/build_anchor_forum_evidence_packet.py --live --allow-live --out-dir .engram-local-evidence/weekend-packet --label weekend-live-review
python scripts/validate_anchor_live_smoke_evidence.py --evidence .engram-local-evidence/weekend-packet/anchor-live-smoke-evidence.json --json
```

Expected local files:

- `.engram-local-evidence/weekend-packet/anchor-live-smoke-evidence.json`
- `.engram-local-evidence/weekend-packet/anchor-live-smoke-metrics.md`
- `.engram-local-evidence/weekend-packet/cursor-forum-reply-draft.md`
- `.engram-local-evidence/weekend-packet/manifest.json`

Use `--anchor-json` and `--live-smoke-json` with the builder only for already-sanitized aggregate JSON from Claude/manual notes. Do not pass raw transcripts, memory bodies, debug logs, or local file listings as inputs.

Accepted aggregate input shape:

```json
{"anchors": {"checked": 12, "valid": 9, "invalid": 1, "unknown": 2, "superseded": 1, "demoted_to_staging": 1}}
```

```json
{"live_smoke": {"runs": 7, "passed": 6, "failed": 1, "failure_classes": {"timeout": 1}}}
```

No public forum reply is sent by these commands. The owner must read `anchor-live-smoke-metrics.md`, inspect `manifest.json`, and approve the exact final text before anything is posted.

## Continuous History Workflow

Use this after daily development closeout or before preparing a new forum draft:

```powershell
python scripts/append_anchor_live_smoke_history.py --live --allow-live
python scripts/build_anchor_forum_evidence_packet.py --history-summary .engram-local-evidence/anchor-live-smoke-history/latest.json --history-window-days 7 --out-dir .engram-local-evidence/weekend-packet-history-7d --label history-7d-review
```

Expected local history files:

- `.engram-local-evidence/anchor-live-smoke-history/anchor-live-smoke-history.jsonl`
- `.engram-local-evidence/anchor-live-smoke-history/latest.json`
- `.engram-local-evidence/anchor-live-smoke-history/summary.md`

The history appends one aggregate JSONL entry per run. It does not install a Windows scheduled task and does not post, push, tag, release, or publish anything.

If a live run reports `0` anchor checks, keep that as `0` and explain that the current live store has no structured anchor records. Do not infer anchor checks from daily logs, lessons, free-text notes, transcripts, or other narrative records.

## Reply Shape

- One short thanks/continuation sentence to Deanrie.
- Methodology: ran Anchor validation plus LIVE_SMOKE over roughly one week.
- Results: aggregate counts only.
- What changed: guesses no longer silently become facts; anchor-backed facts degrade to review/staging when evidence breaks; unknown checks do not imply falsehood.
- Caveats: local dataset, limited repos/tools, no raw data shared.
- Offer: share sanitized reproduction harness or metrics table if useful.
