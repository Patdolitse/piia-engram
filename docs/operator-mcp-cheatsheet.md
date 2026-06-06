# Operator MCP cheatsheet

This is the short operational view of the Engram MCP surface.

## Default surface

- Default: 17 core tools.
- Opt-in full surface: 84 tools with `ENGRAM_TOOLS=all`.
- Core means high-frequency and context-budget friendly. It does not mean read-only.

## Core boundaries

- Normal startup/read tools: `get_user_context`, `get_resume_brief`,
  `get_recall`, `search_knowledge`, `get_relevant_knowledge`.
- Core write tools: `memory_store`, `add_lesson`, `add_decision`,
  `add_playbook`, `update_identity`, `save_project_snapshot`,
  `wrap_up_session`.
- Core export surface: `get_identity_card` is owner-gated because it writes and
  returns a portable Markdown identity card.

## When to enable all tools

Use `ENGRAM_TOOLS=all` when you intentionally need:

- review queues, merges, and stale-knowledge maintenance;
- imports, exports, and backup previews;
- Playbook maintenance and local tool-registry operations;
- proposal-only context-governance previews;
- owner/admin diagnostics or migration surfaces.

All-tool mode increases the visible tool list. It does not remove governance
checks, owner gates, or the need to confirm public actions.

## Evidence labels

- L0/L1: installed, wired, or reachable.
- L2: read/search behavior observed.
- L3: Engram-on beats a control arm with zero-pollution evidence.
- L4: one client writes or exports and another cold-starts and recalls.
- L5: scrubbed, reproducible, public-safe evidence.

Before publishing a claim, check the evidence pack with
`evidence_readiness(...)` and the wording with `validate_public_claim(...)`.
