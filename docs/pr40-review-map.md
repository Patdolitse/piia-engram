# PR #40 Review Map

## Status

- PR: https://github.com/Patdolitse/piia-engram/pull/40
- Branch: `feat/continuity-layer-m1`
- Base: `main`
- Draft: false
- HEAD observed: `44f4826`
- Merge state observed: MERGEABLE on 2026-07-06 Asia/Shanghai; re-check before merging
- GitHub checks observed: 10/10 visible PR checks passed on 2026-07-06 Asia/Shanghai; re-check before merging
- Scope observed: 51 commits, 87 files changed
- Public actions performed: branch push, Draft PR creation, and owner-approved Ready for Review conversion
- Public actions not performed: merge, release, tag, PyPI, MCP Registry, forum reply

## Review Order

1. Runtime/session-end contract
2. Memory governance and controlled memory
3. Agent context pack
4. Anchor/LIVE_SMOKE evidence workflow
5. Release/public boundary guards

## High-Risk Files To Read First

- `src/piia_engram/mcp_tools_session.py`: `wrap_up_session` public MCP behavior and closeout boundary.
- `src/piia_engram/mcp_tools_admin.py`: diagnostics, maintenance, and metadata-only runtime surfaces.
- `src/piia_engram/contexts.py`: resume pack, agent context pack, omission metadata, and project scoping.
- `src/piia_engram/mcp_tools_write.py`: controlled memory write gates and confirmation behavior.
- `scripts/collect_anchor_live_smoke_evidence.py`: aggregate evidence collection and owner-gated live mode.
- `scripts/validate_anchor_live_smoke_evidence.py`: public-safe evidence validation and overclaim/private-content rejection.
- `scripts/build_anchor_forum_evidence_packet.py`: local packet output, manifest shape, and relative filename boundary.
- `scripts/release_sanitize_check.py` and `scripts/check_publish_allowlist.py`: public/release boundary guards.

## Capability Map

### Runtime/session-end Contract

- Purpose: make `wrap_up_session` lightweight, bounded, local-first, and observable.
- Main behavior change: default session-end closeout records bounded context and timing metadata without running full maintenance reconciliation.
- Non-goal: this does not make reconcile automatic or always-on.
- Key commits: `a62d116`, `bb5fb61`, `0bda678`, `03ef522`, `0ddf527`, `f77c3ad`, `966d7d9`, `f95a259`, `01f1402`, `6e178e2`, `c2f7f4f`
- Key files:
  - `src/piia_engram/mcp_tools_session.py`
  - `src/piia_engram/mcp_tools_admin.py`
  - `src/piia_engram/session_closeout.py`
  - `scripts/diagnose_wrap_up_session.py`
  - `scripts/bench_wrap_up_session.py`
- Key tests:
  - `tests/test_mcp_entrypoint_smoke.py`
  - `tests/test_mcp_wrap_up_reliability.py`
  - `tests/test_wrap_up_diagnostic_contract.py`
  - `tests/test_wrap_up_benchmark_contract.py`
  - `tests/test_wrap_up_docs.py`
- Reviewer focus:
  - Default closeout does not run heavy reconcile.
  - Tests use isolated stores rather than real user data.
  - Timing metadata is bounded, parseable, and metadata-only.
  - Reconcile remains an explicit maintenance path.

### Memory Governance And Controlled Memory

- Purpose: prevent AI guesses from quietly becoming durable facts.
- Main behavior change: session evidence and candidates become reviewable metadata rather than silent verified memory.
- Non-goal: this does not grant AI tools authority to promote uncertain claims without owner-approved gates.
- Key commits: `712d223`, `4990553`, `791b4a6`, `254dbb5`, `042b38e`, `da4a2c7`, `89e4125`, `1ed1899`, `86ad9c8`
- Key files:
  - `src/piia_engram/continuity_digest.py`
  - `src/piia_engram/contexts.py`
  - `src/piia_engram/staging_review.py`
  - `src/piia_engram/mcp_tools_write.py`
  - `src/piia_engram/core.py`
- Key tests:
  - `tests/test_continuity_digest.py`
  - `tests/test_context_digest_backfill.py`
  - `tests/test_context_digest_integration.py`
  - `tests/test_project_resume_pack.py`
  - `tests/test_project_resume_pack_quality.py`
  - `tests/test_session_evidence_metadata.py`
  - `tests/test_session_evidence_review_surface.py`
  - `tests/test_mcp_write_confirmation.py`
  - `tests/test_project_memory_boundary.py`
- Reviewer focus:
  - Session-derived evidence can support review but does not imply verified truth.
  - Review/staging metadata does not silently promote memory trust.
  - Project boundaries prevent cross-project memory bleed.
  - Write confirmation paths resist smuggled durable writes.

### Agent Context Pack

- Purpose: give subagents and orchestrators scoped, role-aware, read-only context.
- Main behavior change: callers can request a bounded context pack with role slices, source metadata, and privacy-safe omission signals.
- Non-goal: this does not expose raw memory bodies or cross-project context by default.
- Key commits: `536f9da`, `7572492`, `58d677d`, `e074a34`, `77ce938`, `896bf2f`, `0752415`
- Key files:
  - `src/piia_engram/contexts.py`
  - `scripts/eval_agent_context_pack.py`
  - `scripts/run_memory_evals.py`
  - `docs/integrations/codex.md`
  - `docs/integrations/cursor.md`
  - `docs/integrations/claude-code.md`
- Key tests:
  - `tests/test_agent_context_pack.py`
  - `tests/test_agent_context_pack_docs.py`
  - `tests/test_agent_context_pack_eval.py`
  - `tests/test_memory_eval_suite.py`
  - `tests/fixtures/agent_context_pack_eval_cases.json`
- Reviewer focus:
  - Context pack is read-only and project-scoped.
  - Role scoping and omission metadata are privacy-safe.
  - Eval store isolation prevents local store mutation.
  - Agent context pack is included in release readiness gates.

### Anchor/LIVE_SMOKE Evidence Workflow

- Purpose: generate public-safe local evidence packets and forum drafts without posting.
- Main behavior change: local aggregate evidence can be validated, bundled, and rendered into an owner-reviewed draft.
- Non-goal: this does not publish a forum reply or claim a general benchmark result.
- Key commits: `dc4db48`, `39379ac`, `8773db2`, `4e76799`, `8536e2e`, `5d95e51`, `f28c9c2`, `5005812`, `51d334e`, `da52b2b`, `6f6a2a9`, `e88e104`, `30b0367`
- Key files:
  - `docs/anchor-live-smoke-weekend-evidence.md`
  - `scripts/collect_anchor_live_smoke_evidence.py`
  - `scripts/render_anchor_forum_reply.py`
  - `scripts/validate_anchor_live_smoke_evidence.py`
  - `scripts/build_anchor_forum_evidence_packet.py`
- Key tests:
  - `tests/test_anchor_live_smoke_evidence_collector.py`
  - `tests/test_anchor_forum_reply_renderer.py`
  - `tests/test_anchor_evidence_validator.py`
  - `tests/test_anchor_evidence_packet_builder.py`
  - `tests/test_anchor_evidence_packet_dependencies.py`
  - `tests/test_anchor_live_smoke_evidence_docs.py`
- Reviewer focus:
  - Evidence is aggregate-only and owner-reviewed.
  - Validator rejects malformed, overclaiming, or private-looking evidence.
  - Packet manifest uses relative filenames only.
  - Forum draft generation stays local and does not post.

### Release/public Boundary Guards

- Purpose: keep local evidence and release decisions separated from public publishing.
- Main behavior change: release and evidence surfaces have explicit allowlist/sanitize checks and owner-confirmation boundaries.
- Non-goal: this PR does not release, tag, publish, merge, or post publicly.
- Key files:
  - `.publishallow`
  - `scripts/release_sanitize_check.py`
  - `scripts/check_publish_allowlist.py`
  - `scripts/check_release_gate.py`
  - `scripts/check_release_preflight.py`
  - `scripts/check_public_release_surface.py`
- Key tests:
  - `tests/test_public_positioning.py`
  - `tests/test_public_release_surface.py`
  - `tests/test_release_gate.py`
  - `tests/test_release_readiness.py`
  - `tests/test_pre_push_release_readiness.py`
- Reviewer focus:
  - No automatic public posting.
  - No provider/API dependency.
  - Local-only evidence tooling is not described as a public benchmark claim.
  - Release/tag/registry actions remain owner-confirmed and separate from this PR.

## Verification Evidence

- Local focused tests were refreshed during M13; keep exact counts in local task notes rather than this public review map.
- Local full pytest was refreshed during M13; keep exact counts in local task notes rather than this public review map.
- Local collect-only was refreshed during M13; keep exact counts in local task notes rather than this public review map.
- Release sanitize and publish allowlist checks passed during M13.
- GitHub PR checks: all visible checks passed when observed during M13 on 2026-06-30 10:37 Asia/Shanghai; re-check before marking ready or merging.

## Known Follow-up

- Real weekend Anchor/LIVE_SMOKE packet still needs final data merge and owner review.
- One live `wrap_up_session` call timed out at the MCP tool boundary after daily log write succeeded; M15 should classify whether the cause is transport, runtime, store scan, log flush, or app-side wait.
- PR #40 is now Ready for Review after owner approval; merge, release, tag, registry publishing, and forum replies remain separate owner-confirmed actions.
