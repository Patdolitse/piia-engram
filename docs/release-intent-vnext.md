# Local Release Intent: post-v3.51.2

This is a local planning note only. It is not a release announcement, not a tag,
and not approval to publish.

## Current local commits after v3.51.2

- `1effe5f fix: align CLI tool-count help text`
- `8da40d5 feat(context-governance): add usage report and staged safety helpers`
- `16e3bc5 docs(mcp): clarify tool tiers and owner-gated surfaces`
- `965e643 feat(context-governance): add unified preview surface`

## Suggested release shape

Candidate version: next minor release, because the local branch contains new
context-governance product capability in addition to documentation and guard
improvements.

Expected release themes:

- Playbook/tool-registry and MCP tool-count cleanup carried over after v3.51.2.
- Context governance proposals and recall usage reporting.
- MCP tool-surface semantics and owner/export/admin wording hardening.
- Unified owner-gated context governance preview surface.

## Separate online dashboard lane

The Cloudflare Worker telemetry dashboard is a separate operational surface
from the Python/MCP package release train. Dashboard-only changes should stay in
worker-scoped commits and should not be bundled into the PyPI / MCP Registry
release story unless a release note explicitly needs to mention dashboard
operations.

Current local dashboard follow-up:

- PyPI download and activity trend range selectors are present locally.
- PyPI download display is being reshaped into a single-card KPI + trend layout.
- Any `git push` or Wrangler deploy still requires owner confirmation.

## Publication gate

The following actions remain blocked until the owner explicitly confirms them:

- `git push`
- tag creation
- GitHub Release
- PyPI upload
- MCP Registry publish
- external listing refreshes

Before publication, refresh `docs/public-facts.json`, run full tests, update the
project registry according to the post-push rule, and prepare release notes.
