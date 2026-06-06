# Local Release Intent: post-v3.51.2

This is a local planning note only. It is not a release announcement, not a tag,
and not approval to publish.

## Current local commits after v3.51.2

- `1effe5f fix: align CLI tool-count help text`
- `8da40d5 feat(context-governance): add usage report and staged safety helpers`
- `16e3bc5 docs(mcp): clarify tool tiers and owner-gated surfaces`

## Suggested release shape

Candidate version: next minor release, because the local branch contains new
context-governance product capability in addition to documentation and guard
improvements.

Expected release themes:

- Playbook/tool-registry and MCP tool-count cleanup carried over after v3.51.2.
- Context governance proposals and recall usage reporting.
- MCP tool-surface semantics and owner/export/admin wording hardening.

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
