# Local Release Intent: post-v3.51.2

This is a local planning note only. It is not a release announcement, not a tag,
and not approval to publish.

The local branch is ahead of the last published PyPI / MCP Registry release.
That gap is expected between releases and does not mean these changes are
publicly available yet.

## Product/release-candidate commits after v3.51.2

This list intentionally excludes this planning note's own maintenance commit
chain when it only refreshes release-intent wording. It includes local guard /
evidence commits when they are part of the future release-readiness surface.

- `1effe5f fix: align CLI tool-count help text`
- `8da40d5 feat(context-governance): add usage report and staged safety helpers`
- `16e3bc5 docs(mcp): clarify tool tiers and owner-gated surfaces`
- `965e643 feat(context-governance): add unified preview surface`
- `5153726 feat(worker): reshape telemetry dashboard range views`
- `9f067a6 fix(registry): add title field to server.json for subregistry display name`
- `b5cfeca docs(continuity): add live cross-tool proof and repoint README CTA`
- `515aff6 feat(release): add local readiness aggregator`
- `e243024 feat(release): add local readiness dossier package`

## Suggested release shape

Candidate version: next minor release, because the local branch contains new
context-governance product capability in addition to documentation and guard
improvements.

Expected release themes:

- Playbook/tool-registry and MCP tool-count cleanup carried over after v3.51.2.
- Context governance proposals and recall usage reporting.
- MCP tool-surface semantics and owner/export/admin wording hardening.
- Unified owner-gated context governance preview surface.
- Live cross-tool continuity proof and clearer external-listing metadata.
- Local pre-push / pre-release readiness aggregation, release dossier support,
  context-governance eval scaffolding, and post-push closeout dry-run.

## Separate online dashboard lane

The Cloudflare Worker telemetry dashboard is a separate operational surface
from the Python/MCP package release train. Dashboard-only changes should stay in
worker-scoped commits and should not be bundled into the PyPI / MCP Registry
release story unless a release note explicitly needs to mention dashboard
operations.

Current local dashboard follow-up:

- PyPI download and activity trend range selectors are present locally.
- PyPI download display has been reshaped into a single-card KPI + trend layout.
- The dashboard update was deployed to Cloudflare Workers after owner confirmation.
- Current deployed Worker version ID: `61579816-fcc0-4a7e-8492-14647ed4dfc0`.
- Any `git push` or Wrangler deploy still requires owner confirmation.

## External listing lane

LobeHub currently renders stale/wrong header metadata for the public listing:

- displayed title: `MCP Server Manifest Plugin`
- displayed version: `3.47.0`
- correct title/version: `piia-engram` / `3.51.2`

Local metadata and official MCP Registry data have been checked. The owner has
already requested help on Discord, so do not send duplicate public feedback
unless explicitly asked.

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

Current local verification snapshot:

- full pytest: `2980 passed, 2 skipped, 4 warnings`
- collect-only: `2982 tests collected`
- MCP tool count: `84 total / 17 core / 67 advanced`
- staged publish allowlist: `422 tracked files covered by 94 patterns`
- `check_pre_push_release_readiness.py --full-tests`: passed
