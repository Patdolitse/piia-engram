# Changelog

[English](CHANGELOG.md) | [中文](CHANGELOG.zh-CN.md)

All notable changes to Engram are documented in this file. For detailed release notes with upgrade instructions, see [GitHub Releases](https://github.com/Patdolitse/piia-engram/releases).

Format follows [Keep a Changelog](https://keepachangelog.com/). Versions follow [Semantic Versioning](https://semver.org/).

## [4.14.1] - 2026-08-05

### Fixed
- Pinned the `mcp` dependency to 1.x (`mcp>=1.0,<2`): mcp 2.0.0 removed a module the Engram MCP server imports at startup, so fresh installs failed to launch `piia-engram-mcp`. New installs now resolve a compatible mcp 1.x automatically. If you already hit this on an existing install, run `pip install "mcp>=1.0,<2"` or upgrade with `pip install -U piia-engram`.

## [4.14.0] - 2026-07-10

### Added
- Canonical product-boundary contract and guard: public package roots, optional extensions, tool surface, export surfaces, public docs, release evidence, and publish allowlist now share a machine-readable boundary in `docs/public-facts.json`.
- LIVE_SMOKE reliability and evidence integrity tooling: run records now preserve failure classes, bounded metadata, validated aggregate evidence, and append-only local history without exposing raw memory bodies, local paths, credentials, or transcripts.
- Longitudinal real-use evidence evaluator: local collection and report tooling can track first-value evidence over time, while explicitly distinguishing real-use accumulation from synthetic or operational checks.

### Changed
- `search_knowledge` now routes through a thin application service shared by CLI and MCP adapters, keeping behavior parity while preserving existing protocol boundaries.
- Provenance and trust semantics are consolidated into canonical helpers for source agent, confirmation source, trust projection, freshness, and recall-visible provenance; unsafe labels and credential-shaped identifiers fail closed.
- Public facts and test collection now use a deterministic, isolated environment profile, keeping the frozen MCP surface at 57 total tools: 17 core and 40 advanced.

### Fixed
- Product-boundary and release-surface guards now redact private-looking paths and unsafe public terms in diagnostics instead of echoing matched content.
- Public fact sync now seals host-environment drift from `PYTHONPATH`, pytest plugin autoload, and ambient Engram stores during collection.
- Synthetic nested memory-eval validation was tightened so aggregate verdicts cannot overstate readiness.

### Documentation
- Public docs keep remote telemetry and feedback as separate explicit opt-ins, still off by default.
- First-value and longitudinal evidence wording stays conservative: local first-value collection begins accumulating only after the client is restarted, and synthetic or operational evidence does not verify real-use longitudinal readiness.

## [4.13.0] - 2026-07-06

### Added
- Continuity runtime contracts: session-end closeout now records bounded, observable metadata while keeping heavy reconciliation on explicit maintenance paths.
- Agent context packs: callers can request scoped, role-aware, read-only context with privacy-safe omission signals for subagents and orchestrators.
- Anchor/LIVE_SMOKE evidence workflow: local aggregate evidence can be validated, bundled, and rendered into an owner-reviewed draft without posting publicly.

### Fixed
- Wrap-up diagnostics now distinguish tool-boundary timeouts from successful local daily-log writes.
- Archived context and evidence validation paths are hardened against malformed, overclaiming, or private-looking evidence.

### Documentation
- Added a PR #40 review map and clarified that live PR state, checks, and commit counts must be read from `gh` rather than durable documentation snapshots.

## [4.12.0] - 2026-06-23

### Fixed
- Playbook writes are now atomic: body file and shared index are committed together under a single lock, with automatic rollback on partial failure.
- Playbook access-count bumps run inside the file lock, closing a race where concurrent reads could lose increments.
- `save_execution_plan` no longer overwrites in-progress step states — a second save merges with existing step progress instead of replacing it.
- Watcher file-state persistence uses deep merge with monotonic watermarks, preventing concurrent scans from silently dropping sibling file entries.
- Web reader SSRF defense now resolves DNS before allowing a request, blocking domains whose A record points to a private IP (DNS-rebinding defense). Redirect hops are re-validated against the private-IP blocklist.
- Staging-tier knowledge items are excluded from the relation surface — `add_relation` no longer accepts unverified items as endpoints.
- JSON corruption from concurrent `_update_json` writes is caught fail-closed instead of silently proceeding.
- Cross-process file locking hardened with `portalocker` across all mutable stores.
- Timezone handling normalized to UTC throughout.

## [4.11.0] - 2026-06-22

### Added
- Self-contained web reader: `read_web_content` can now extract article text on its own, without the external sidecar. Install with `pip install "piia-engram[reader]"`. It prefers a local sidecar when one is running (YouTube subtitles, Bilibili, WeChat, ...), falls back to the built-in reader for standard pages, and otherwise returns an actionable install hint.

## [4.10.0] - 2026-06-22

> Note: 4.10.0 was tagged on GitHub but not published to PyPI; its changes ship to PyPI as part of 4.11.0.

### Added
- Leakage guard widened to detect all-drive paths (any `X:\...` pattern) plus `cfut_`, `ARK_API_KEY`, and PyPI token shapes in tracked files.
- MCP `search_knowledge` results bounded at the protocol boundary — prevents oversized payloads from overwhelming client context windows.
- Semantic near-duplicate surfacing on knowledge write — when you add a lesson or decision, Engram now warns if a semantically similar entry already exists, without silently dropping or merging.

### Fixed
- `hf_` token pattern scanner ↔ sensitivity configuration drift closed; full vendor-parity guard added.
- Circular import between `cli_commands` ↔ `setup_wizard` broken.

## [4.9.1] - 2026-06-20

### Fixed
- Cold start now delivers "it already knows me" through `get_user_context`: the auto-import of your CLAUDE.md / AGENTS.md rules was silently unreachable on that tool — a non-empty "identity not set" scaffold shadowed its trigger condition — so a brand-new user saw the generic scaffold instead of their imported preferences. It now imports on first call, matching `get_resume_brief`.

## [4.9.0] - 2026-06-20

### Added
- Automatic pre-upgrade backup: the first time your store is opened under a newer Engram version, Engram snapshots your data — knowledge, identity, playbooks, and project context — to `backups/engram-<version>-<timestamp>/` BEFORE any schema migration runs, so an upgrade can never silently lose or corrupt your irreplaceable memory. It keeps the 5 most recent snapshots, runs at most once per upgrade, and is best-effort: a failure warns and never blocks — unless a schema migration is actually pending, in which case it stops so you can free disk space rather than migrate unprotected. Opt out with `ENGRAM_NO_AUTO_BACKUP=1`.
- Dock GUI — Settings page: switch the interface language (中文 / English) and see your store's governance + telemetry status at a glance (read-only).
- Dock GUI — Rules & Permissions page: a read-only view of your governance rules and per-caller permission profile.
- `engram dock-playbooks`: a CLI counterpart to the Dock GUI's playbook list, sharing one core with the HTTP route.

## [4.8.0] - 2026-06-20

### Added
- Engram Dock — a local browser GUI. `engram serve --ui` opens a loopback-only (127.0.0.1) web interface for your memory, no commands needed: view/edit/archive lessons and decisions (with bulk archive and a restore-from-trash bin), browse playbooks, see a live overview of your store, and grab your cross-tool "接续" (resume) context to paste into whatever AI tool you're using — all in one click. Install with `pip install "piia-engram[ui]"`.

### Security
- The Dock GUI is security-first: it binds 127.0.0.1 only (never the network), exchanges a one-time startup token for a server-side session (HttpOnly + SameSite=Strict cookie), allowlists the Host header (DNS-rebinding defense), requires Origin + a CSRF token on every write, sets no CORS plus `Cache-Control: no-store` and `X-Content-Type-Options: nosniff` on responses, and never serves your store path to the browser. Reads are zero-write; a refused write opens no writable store.

## [4.7.0] - 2026-06-19

### Added
- Presence loop — Engram now makes itself visible each time it works. Saving a lesson, decision, or playbook returns a branded, tier-aware confirmation; `recall` opens with a branded trust header (`[Engram Recall] N memories · X fresh · Y stale`) computed only from freshness it can substantiate (never an unearned "verified"); and the cross-session resume brief now leads with `[Engram] Resumed N memories from <project> · last session <when>`.
- Weekly recap: new `engram weekly [--json]` prints a ≤10-line digest of the last 7 days — new lessons/decisions/playbooks counts, top domains, portrait growth, the review backlog, and one older memory worth resurfacing (chosen deterministically, never random). A once-per-week, ignorable SessionStart hint reminds you it exists.

### Fixed
- Read/refusal write boundary: a refused read-only-external caller can no longer cause any incidental disk write (such as a session checkpoint), and the new weekly recap reads are strictly zero-write.

## [4.6.2] - 2026-06-19

### Added
- Dependency successor detection: when a repo-backed fact's dependency anchor breaks because the dependency was replaced by a known successor (e.g. `jest` → `vitest`), recall now surfaces the migration as a hint (`superseded_by`) instead of just a bare invalidation — the now-false fact still drops back to an unconfirmed guess, but you also get a pointer to re-onboard the successor. The mapping is curated and conservative (high-confidence test-framework migrations only), and the hint clears automatically once the fact is re-validated.

## [4.6.1] - 2026-06-19

### Added
- Opt-in, content-blind first-value funnel telemetry: an optional local signal that records *where* you land in the onboard → trusted-recall funnel (scan, candidates, accept, trusted recall, cross-tool recall) as coarse buckets — never *what* is in your memory. Off by default, local-only (nothing is sent anywhere this phase), and it honors `DO_NOT_TRACK` / `NO_TELEMETRY` absolutely. It never records content, file names, paths, ids, raw errors, a persistent identifier, or the tool pair. New `engram telemetry funnel` shows your own local funnel; see [docs/telemetry-privacy.md](docs/telemetry-privacy.md).

## [4.6.0] - 2026-06-18

### Added
- Dock owner-console quality surfaces: four new local, owner-run commands a desktop client can drive. `engram dock-quality` and `engram dock-governance` are zero-write summaries — knowledge-quality counts plus the next review lane, and governance readiness plus per-client setting coverage. `engram dock-review-queue` lists items needing review, grouped by lane. `engram dock-quality-action` applies a single owner-confirmed action (validate, promote, or archive): it previews by default and writes only with `--yes`, promotes staged items only, and archives reversibly. All four return metadata only — no titles, bodies, reasoning, or paths.

## [4.5.1] - 2026-06-18

### Fixed
- `engram recall` now shows the owner trust block for each repo-backed fact — why it's trusted, its anchor, when it was last validated, and its decay policy — so the trusted-recall first value is visible from the CLI, not only through an MCP client. Pass `--no-trust` to hide it.

### Added
- Batch onboarding accept: `engram onboard-accept --all` accepts every staging candidate for the current repository in one command. It previews by default (how many would be accepted, refused for an invalid anchor, or skipped as cross-repo) and needs `--yes` to write. Each candidate is still verified against its anchor individually, so the batch can partially succeed and never accepts another repository's candidate.

## [4.5.0] - 2026-06-18

### Added
- Onboard-repo first value path: `onboard_repo` scans a local repository, writes staged knowledge candidates with repo provenance, and lets the owner promote selected candidates through `onboard_accept` / `accept_onboard_candidate`; accepted repo-backed facts can then surface trusted recall for private-self owner calls.
- CLI and MCP shells for repo onboarding: the local CLI can run the scan and accept flow, and the advanced owner-only MCP surface now exposes `onboard_repo` and `onboard_accept` without expanding the default Tier-1 tool set.
- Acceptance proof target: the owner-confirmed scan -> staging candidate -> accept -> trusted recall workflow is designed to complete in <=10 minutes for a representative repository.

### Fixed
- `accept_onboard_candidate` now refuses to promote a staged candidate when its recorded `anchor_project_id` conflicts with the accepting repository's resolved project id, leaving the candidate staged for review instead of trusting cross-repo provenance.

## [4.4.0] - 2026-06-18

### Added
- Source-aware freshness: `compute_freshness` now reads each fact's source and picks a decay policy from it. Human-confirmed facts keep time-based decay; test- and anchor-confirmed facts are trigger-bound and stay off the staleness clock. The public 4-state `freshness_status` is unchanged; source-awareness is an additive `skip_decay` / `decay_policy` signal honored by the decay, refresh, and stale-surfacing paths.
- Owner confirmation stamping: `confirm_knowledge` records an explicit confirmation source (human, test, or anchor). Agents cannot self-attest; trust fields are stripped on every write path.
- Anchor read-time validator (`check_anchors`, owner-run): ties a fact to an observable repo anchor (a dependency or a file), resolved against the project's git-remote identity. An invalidated anchor (the dependency is gone) demotes the fact back to a guess (tier to staging, confirmation cleared, the invalid anchor status kept as evidence); an unresolvable check only falls back to time decay, so "couldn't check" never reads as "it's gone". Demotion is one-way: a returning dependency does not auto-restore trust; re-confirmation is deliberate.

## [4.3.0] - 2026-06-17

### Security
- Hardened private-path sanitization with broader pattern coverage, and removed leaked local paths from fixtures and code.
- Validate worker feedback payloads server-side, and guard the public worker config so Cloudflare worker settings can't leak.
- Narrowed trusted-local writes: trusted-local agents may still direct-write low-risk new lessons/decisions, but high-impact operations (identity changes, overwriting existing knowledge, relation changes) now route to owner review.

### Added
- Optional governance-status surfacing in `doctor` and the status report.
- System-derived provenance/labeling metadata on knowledge, with a labeling validation loop across recall, staging, and review (labels are system-derived and advisory; agents cannot set them).
- Lesson version snapshots (knowledge version chain) so edits keep prior history.
- Dock M1: a read-only backend status contract (`dock-status`) - zero-write, metadata-only.

## [4.2.0] - 2026-06-15

### Added
- **Read-only, zero-write store mode.** `Engram(read_only=True)` now guarantees a
  true no-write open: field migration, trust-boundary backfill, session stamps,
  and the update reminder are all suppressed, so a client can read the store with
  zero side effects. `engram preview --read-only` renders the Memory Lens report
  under this guarantee.
- **Read-only CLI surface for a local desktop client.** A family of zero-write
  commands to pull paste-ready context without mutating the store: `dock-resume`
  (resume brief), `dock-search` (keyword search), `dock-list` (all active
  lessons/decisions for a browse-all view), `dock-portrait` (full styled
  user-portrait HTML), `dock-archived` (list archived entries), and
  `dock-get-lang` (read the owner's language). Plus `dock-export` for a one-click
  full backup (writes a backup file).
- **Owner knowledge management from a local client.** `dock-onboard-scan` /
  `dock-onboard-commit` extract lesson/decision candidates from pasted text or a
  chosen project folder (recent git subjects + README) for owner-confirmed,
  editable import; `dock-update` edits an entry's content; `dock-archive` /
  `dock-restore` reversibly archive and restore entries.
- **Richer user-portrait HTML.** The portrait gains work-style, knowledge
  composition, collaboration tools, a "days together" meta, and click-to-expand
  drill-in to the full lesson and decision content.
- **Owner language toggle.** `dock-set-lang` writes the profile language (zh/en),
  so a desktop client can switch its own interface and Engram's portrait, privacy
  preview, and CLI output together.

### Fixed
- Hardened `read_only` against lazy-write paths (field migration, atomic write,
  trust-boundary backfill) so a read-only open is truly side-effect-free.
- The pre-commit hook now selects a working Python interpreter instead of the
  Windows Store stub.

## [4.1.0] - 2026-06-12

### Added
- **Decision-conflict governance.** Conflict detection now uses one converged
  threshold across `doctor`, context assembly, and recall, and version-evolution
  chains (supersedes links) no longer count as conflicts. New owner CLI:
  `engram conflicts list [--json]` for a read-only view and `engram conflicts
  resolve --action supersede|archive|dismiss` (previews by default; writes
  require `--commit --yes`). `doctor` reports the top actionable conflict pairs
  with ids and scores, and dismissals travel with native export/import.
- **Capability modes for the MCP tool surface.** `ENGRAM_TOOLS` now accepts
  composable capability groups (`knowledge`, `governance`, `admin`,
  `integrations`) in addition to `core` / `all`; unknown tokens fail safe to
  core with a bilingual warning. `engram setup` adds a capability-mode picker
  (all 53 / core 17 / core + knowledge) — the default it writes is unchanged —
  and `doctor` shows the active mode. Guide:
  [docs/operator-mcp-cheatsheet.md](docs/operator-mcp-cheatsheet.md).
- **`engram preview` (Memory Lens).** A local bilingual HTML report showing
  exactly what a simulated AI caller would receive — identity card, injected
  context, and the governance decisions behind each item.
- **Supply-chain verification.** The release pipeline now generates SBOMs and
  GitHub artifact attestations for published wheel/sdist artifacts. How to
  verify them yourself: [docs/supply-chain.md](docs/supply-chain.md)
  ([中文](docs/supply-chain.zh-CN.md)).

### Fixed
- Corrected documentation drift that shipped with v4.0.0 (stale tool counts
  and version headers), and the drift guard now sweeps registered stale
  patterns so the same regressions cannot ship again.

### Documentation
- README gains CI/guard status badges and a "verify it yourself" path linking
  the public guard scripts and the release-evidence index.

## [4.0.0] - 2026-06-11

### Changed
- **BREAKING: MCP tool surface consolidated from 87 to 53 tools.** Families of
  closely related operations are merged into single tools with a
  `mode`/`action` selector; the old names were removed in the same release (no
  transitional aliases). The 17 Tier-1 core tools loaded by default are
  unchanged. Highlights: identity reads → `get_identity_facets`; Playbook
  reads → `get_playbooks` (`mode`); Playbook management → `manage_playbook`;
  execution → `playbook_execution`; staging review → `review_staging`;
  relations → `manage_relation`; exploration → `explore_knowledge`; decision
  threads absorbed into `get_decisions` (`thread_seed_id=` /
  `history_question=`); portraits → `user_portrait`; caller trust →
  `manage_caller_trust`; OpenClaw import/export folded into `export_engram` /
  `import_engram` (`format="openclaw"`); batch writes folded into
  `memory_store` (`items_json=`). Full old-name → new-call mapping and
  behavior notes: [docs/migration-v4.md](docs/migration-v4.md).
- **BREAKING: legacy Playbook scope migration moved out of MCP** into the
  owner-only local CLI: `engram playbook scope
  classify|apply|rollback|queue|resolve` (previews by default; writes require
  `--apply --yes`).
- **Tighter staging-review gate** — `review_staging` runs the write gate for
  every action including `list` (the old `list_pending_staging` was
  read-class); read-only-external callers are now refused. Owner and
  trusted-local callers are unaffected.

### Documentation
- New migration guide [docs/migration-v4.md](docs/migration-v4.md); tool
  tables and counts refreshed across the bilingual README, user guide,
  architecture, operator cheatsheet, tool-surface analysis, and registry
  metadata.

## [3.56.0] - 2026-06-11

### Added
- **Claude Code watcher adapter** — the universal watcher can now capture
  Claude Code sessions from on-disk transcripts (`~/.claude/projects/`).
  The adapter automatically yields when the Engram Stop hook is already
  wired in the Claude settings, so hook users never get duplicate captures;
  it acts as the fallback for setups without the hook.
- **Enhanced search in setup** — `engram setup` now offers an optional
  one-keystroke step to enable hybrid search (keyword + full-text + semantic
  vectors): offers the `[vector]` dependency install, persists
  `ENGRAM_SEARCH=hybrid` into detected AI clients' MCP configs, and builds the
  index at the end of setup. Re-running setup no longer silently drops a
  previously enabled `ENGRAM_SEARCH` from client configs. Hybrid stays
  opt-in/off by default.

### Fixed
- **Hook failures are no longer silent** — the Claude Code and Cursor hooks
  (save-on-stop, compact absorb, resume-brief inject, writeback) still never
  block the host tool, but a swallowed failure now leaves a one-line
  breadcrumb in `<ENGRAM_DIR>/logs/hooks.log` (size-capped), matching the
  watcher's existing `watcher.log` observability.

### Documentation
- Hybrid search is now discoverable from the README and user guide
  (`pip install "piia-engram[vector]"` + `ENGRAM_SEARCH=hybrid`), with a new
  Chinese translation of the hybrid-search guide.
- `docs/architecture.md` caught up with v3.55: new "capture channels" section
  documenting the `hooks/` and `watcher/` subpackages and their contracts,
  module map updated for the v3.55 monolith split
  (`knowledge_ops` / `playbooks` / `tools_registry` / `doctor` /
  `cli_commands` / `search_index`).

### Changed
- **Incremental watcher capture** — watcher checkpoints now carry only the
  conversation turns appended since the last successful save (per-file byte
  offset in the watcher state), instead of re-sending an overlapping tail of
  the whole transcript on every save. Long-running sessions no longer pile up
  duplicated content in their context logs. Existing watcher state migrates
  automatically; a rewritten/rotated transcript is detected and re-read from
  the start, and a half-written trailing line is never consumed.
- **Internal restructuring (no API change)** — the three largest modules were
  split for maintainability: `core.py` into `playbooks.py` /
  `tools_registry.py` / `knowledge_ops.py` mixins, `setup_wizard.py` into
  `doctor.py` / `cli_commands.py`, and `mcp_server.py` into five
  `mcp_tools_*.py` modules. All public entry points, the MCP tool surface
  (87 tools), and import paths are unchanged.

## [3.55.0] - 2026-06-10

### Added
- **Universal session watcher** (`piia_engram.watcher`) — background poller that
  auto-captures AI tool sessions into Engram contexts for tools without hook
  support. Ships with a Codex adapter (parses rollout JSONL transcripts).
  Watermark-based incremental scanning, per-file debounce, no backfill on first
  run, contexts-only writes (never touches the knowledge store).
- **One-command autostart install** — `engram watcher install` sets up per-user
  logon autostart on Windows (Startup-folder shortcut + console-less launcher;
  no admin rights needed). Companion subcommands: `start`, `status`,
  `uninstall`, `once`. Non-Windows platforms get cron/systemd guidance.
- **History-recall trigger rule** — instruction snippets for all four supported
  tools now tell the model to call `get_recent_context` when the user asks
  about past conversations.

### Fixed
- **PyPI release gate unblocked** — v3.54.0's PyPI publish was blocked because
  the public-content-boundary cleanup removed `release-evidence/` from tracking
  while the CI release gate still required it. Evidence files are tracked again
  as marker-only declarations (no internal test counts or scan details);
  detailed notes stay local. v3.54.0 was never published to PyPI; its changes
  ship in this release.

## [3.54.0] - 2026-06-10

### Added
- **Cursor session hooks** — automatic resume-brief injection on `sessionStart`
  and context-only save on `stop`. Adapted to Cursor's real protocol (env-var
  transport via `CURSOR_TRANSCRIPT_PATH` / `CURSOR_PROJECT_DIR`; empty stdin).
  Rich transcript extraction with role-prefixed conversation, tail-read for
  oversized files, per-conversation UUID debounce, and graceful degradation to
  minimal daily checkpoints. 36 new tests.
- **Playbook trigger matching on cold start** — `get_user_context` now surfaces
  relevant playbooks when the user's prompt matches trigger keywords, without
  requiring manual lookup.
- **Post-push GitHub status probe** (`scripts/post_push_closeout.py`) — read-only
  `--github-status` mode that checks CI / release state after push without
  triggering any action.

### Fixed
- **P0 security: tier-smuggle escape closed** — MCP entry layer now strips
  caller-supplied trust fields (`tier`, `approval_status`); trust docs aligned.
- **Concurrency stress test honesty** — Windows lock-timeout flake rewritten
  to "no silent loss" contract instead of false-passing on timeout.
- **Display-safe decryption failure** — when `ENGRAM_SECRET` is wrong or missing,
  read path returns a user-visible placeholder instead of raising.

### Changed
- **Public content boundary enforced** — 55 internal-only files (release evidence,
  runbooks, plans, design docs) removed from git tracking and gitignored.
- **`proposed_only` write policy renamed to `direct_write`** for honest labeling.

## [3.53.0] - 2026-06-08

### Added
- **Honest head-to-head comparison** (`docs/honest-comparison.md` + `.zh-CN.md`)
  — bilingual narrative comparison vs mem0, Basic Memory, and ByteRover.
  Every competitor claim is dated and footnoted to their own public docs
  (snapshot 2026-06-08). Includes "Where they win," "Where we are weaker,"
  and an honest decision guide. Companion to `docs/comparison.md`.

### Changed
- **Positioning rewrite (README first screen)** — lead with governance
  ("see, edit, and override"), not just continuity. Cross-tool portability
  moves to supporting pillar. Honesty-corrected: removed overclaim phrases
  ("asks before it remembers," "only what you approve") from all public copy,
  metadata, and guard tests.
- **Published metadata honesty correction** — `.mcp/server.json`,
  `pyproject.toml`, `.claude-plugin/plugin.json`, `.cursor-plugin/plugin.json`
  descriptions rewritten to the honest capability frame ("AI proposes;
  high-risk items wait for your review; everything visible and reversible").
- **Guard test reversal** — `test_public_positioning.py` now asserts the
  honest wording and bans the overclaim phrases.
- **License changed from Apache-2.0 to AGPL-3.0-or-later.** The open-source
  core remains free software you can use, modify, and self-host. Network or
  hosted redistribution must make the corresponding source available under the
  same license (AGPL §13). Releases up to and including 3.52.0 remain under
  Apache-2.0; this change applies to subsequent versions.

## [3.52.0] - 2026-06-08

### Added
- **Risk-tiered write gate for new knowledge (N3)** - new lessons and decisions
  are now assessed for risk on write. Low/medium-risk entries are auto-absorbed
  as `verified` (approved, audit-logged) so they are available to the next
  session immediately; only high-risk entries (credentials, shell commands,
  permission rules, MCP config) go to staging for human review. Unsupervised
  writeback hooks (e.g. Cursor) force staging regardless, LLM-extracted entries
  cannot self-label `verified`, and an explicitly pinned tier is honored.
  `get_resume_brief` surfaces a `pending_review` count (including high-risk) at
  the top of the handoff.
- **Auto-bootstrap on first empty-store connection** - the first
  `get_user_context` / `get_resume_brief` against an empty store reads existing
  rule files (CLAUDE.md / AGENTS.md / .cursorrules) read-only and imports them,
  removing a manual setup step. Runs at most once via a `.bootstrap_done`
  sentinel.
- **Setup language picker** - `engram setup` now starts with a numbered
  中文 / English language choice; the wizard renders bilingual strings for the
  whole flow, independent of system locale.
- **Context governance proposal helpers** - added local, proposal-only context
  governance helpers for context usage reporting, role-scoped recall, safe
  context / lockdown transforms, freshness and conflict proposals, compression
  replay packets, and external evidence page drafts. These helpers do not
  publish, push, tag, mutate stored knowledge, or apply archival decisions.
- **Context governance preview surface** - added the advanced
  `preview_context_governance` MCP tool as a single owner-gated preview entry
  for safe-context, freshness/conflict, replay, and evidence proposals.

### Changed
- **Setup external-config default is consent-then-write** - `engram setup` now
  detects supported clients, lists the exact config file paths it will modify,
  and writes the MCP connection only after a one-keystroke confirmation (each
  write is backed up first). Choosing "no" changes nothing.
  `--apply-external-config` is retained as the non-interactive / CI path that
  skips the prompt.
- **Public trust narrative aligned with new behavior** - README (EN/zh),
  SECURITY, trust docs, and the bilingual quickstart now describe consent-then-
  write external config and the risk-tiered approval model honestly, while
  retaining the genuine guarantees (backup-before-write, decline = zero changes,
  ledger path redaction, high-risk still needs human approval).
- **MCP tool-surface semantics** - clarified across public docs, skill
  references, Glama metadata, CLI help, and MCP docstrings that Tier-1 / core
  means "high-frequency and context-budget friendly", not "read-only". Owner
  export, owner/admin, optional local, internal/dogfood, and legacy maintenance
  surfaces are now labelled explicitly.

### Fixed
- **Tool-surface drift guards** - added tests that pin owner/export and
  owner/admin schema markers, `get_identity_card` as a core-but-export surface,
  local tool registry classifications, and legacy Playbook maintenance tools.
- **Public facts refreshed** - current local facts now report 3045 passed,
  2 skipped, 3047 collected tests, and 87 total MCP tools
  (17 core / 70 advanced).

## [3.51.2] - 2026-06-06

### Changed
- **Playbook passive-reference contract** - normalized saved playbooks into a
  versioned structural contract, preserved prose pitfalls/preconditions, exposed
  execution outcome rollups, added optional `required_tools` declarations with
  runtime-only `resolved_tools`, and extended the MCP `usage_policy` runtime hint
  to `search_knowledge` playbook hits so the main reuse path stays governed.
- **Playbook-to-tools-registry bridge** - Playbooks can now declare local tool
  needs through canonical `required_tools` metadata while preserving
  `tool_refs` as an input alias. `prepare_playbook_execution` resolves
  availability through the tools registry at runtime and returns
  `resolved_tools`, `tools_ready`, and `missing_tools` without persisting local
  absolute paths into Playbooks or execution plans.

### Fixed
- **Tool version uncertainty** - unknown registered tool versions now surface as
  an explicit unknown / unsatisfied status instead of pretending the
  `min_version` requirement is satisfied.
- **Public facts refreshed** - current local facts now report 2906 passed,
  2 skipped, and 2908 collected tests.

## [3.51.1] - 2026-06-05

### Added
- **Public trust evidence page** - added `docs/trust-evidence.md` as an
  outsider-readable map from trust/privacy claims to deterministic local
  checks, including explicit boundaries for what the evidence does not prove.
- **First-value quickstart** - added `docs/quickstart-first-value.md`, a short
  path from install to first approved memory to fresh-session recall using the
  default core tool surface.
- **MCP tool-surface analysis** - added `docs/tool-surface-analysis.md`, an
  analysis-only map of all 83 MCP tools, the 17 core / 66 advanced split,
  functional clusters, and governance classes.

### Fixed
- **Public trust-claim drift guard** - added
  `scripts/check_public_trust_claims.py`, tests, and CI/publish wiring so
  network, telemetry, plaintext, optional-encryption, and endpoint claims stay
  aligned across README, SECURITY, PRIVACY, and telemetry docs.
- **CI-like pytest entrypoint guard** - added a local guard that catches tests
  importing `scripts.*` under `PYTHONPATH=src` before GitHub Actions fails.
- **Public tool-count drift** - corrected stale 64/81 tool-count wording to
  the current 83 total, 17 core, and 66 advanced tools.
- **Same-name project disambiguation** - clarified that
  `Gentleman-Programming/engram` is an unrelated project with a different
  product shape.

### Changed
- **Public facts refreshed** - current local facts now report 2881 passed,
  2 skipped, and 2883 collected tests.
- **Release flow visibility** - split the release orchestrator into `prep` and
  `publish-fast` modes so local evidence checks stay separate from the hot
  publish path.

## [3.51.0] - 2026-06-05

### Added
- **Recall Eval v1 baseline** - added a deterministic, synthetic,
  public-safe recall benchmark that scores real `search_knowledge` results by
  expected knowledge IDs without using live stores, network calls, or LLM
  judges.
- **Admission Guard v1** - added a read-only candidate-memory guard that
  combines quality verdicts, duplicate detection, and obvious conflict routing
  into metadata-only actions: `accept`, `stage`, `reject`, `duplicate`, and
  `review_update`.
- **Held-out Memory Eval Suite** - added separate held-out recall/admission
  fixtures and `scripts/run_memory_evals.py` so memory quality can be checked
  as a citable aggregate suite before release.

### Fixed
- **Admission conflict miss** - release-style phrases such as "without user
  confirmation" now route to conflict/update review instead of being accepted
  as a new durable lesson, while `without downtime` remains a non-conflict
  deployment phrase.
- **Eval CLI source drift** - recall/admission eval scripts now prioritize the
  worktree `src/` path so direct CLI runs do not accidentally import an
  installed older package.
- **Release workflow guard coverage** - CI, publish workflow, and the release
  orchestrator now include the memory eval suite gate, with a red-light test
  proving the gate fails on degraded admission expectations.

### Changed
- **Public facts refreshed** - current local facts now report 2865 passed,
  2 skipped, and 2867 collected tests.
- **Ranking/search optimization deferred** - no recall ranking changes were
  made because the held-out suite did not expose a measured recall failure.

## [3.50.0] - 2026-06-05

### Added
- **Generated export redaction gate** - added a dynamic release/CI guard that
  builds synthetic Engram data, renders real identity-card, knowledge-report,
  and AGENTS export surfaces, and scans those generated outputs for secret,
  path, and email-shaped leakage.
- **Read-only staging queue tool** - added `list_pending_staging` as a
  metadata-only MCP read tool for reviewer and GUI workflows, while keeping
  `batch_review_staging` mutations behind write governance.
- **Telemetry vNext and dashboard alignment** - continued local/default-off
  telemetry contract hardening and aligned dashboard-facing copy with the
  current contract.

### Fixed
- **Export metadata redaction** - domain labels, source-tool labels, section
  headers, lesson summaries/details, decision text, stale titles, and related
  titles are now redacted before identity-card, knowledge-report, and AGENTS
  export rendering can expose them.
- **Release guard coverage** - the release orchestrator, CI workflow, and
  publish workflow now all include the generated export redaction gate in
  addition to the static clean-sample guard.

### Changed
- **Public facts refreshed** - current local facts now report 2830 passed,
  2 skipped, 2832 collected tests, and 83 MCP tools: 17 core plus 66 advanced.
- **OpenClaw claim boundary held** - OpenClaw remains documented as L3 static
  file-bridge evidence only; live OpenClaw agent/model continuity is still not
  claimed.

## [3.49.2] - 2026-06-05

### Added
- **Cursor writeback hook, opt-in and staging-only** - added a guarded Cursor
  stop-hook entry point that is disabled by default, re-entry protected,
  transcript-bounded, and routed through `extract_session_insights` so extracted
  knowledge lands in staging review instead of becoming verified automatically.
- **Batch staging review MCP tool** - added `batch_review_staging`, a
  metadata-only staging approval/rejection helper. It defaults to dry-run,
  requires explicit `confirm=true` for mutation, and only acts on staging
  lessons or decisions.
- **Continuity and compatibility signals** - refreshed MCIC/client-continuity
  evidence and compatibility scaffolding so Hermes/OpenClaw evidence remains
  narrowly labeled and reproducible from copied-store or static-bridge runs.

### Fixed
- **Encoding repair false positive** - valid Chinese text with ordinary ASCII
  question marks is no longer treated as lossy mojibake. Repairable-loss
  detection now requires the Unicode replacement character while existing
  strong mojibake markers remain covered.
- **Permission Profile vNext staging boundary** - `staging_optin` can no longer
  override review/publish-stage staging exclusion; staging visibility remains
  constrained by the current lifecycle stage.
- **Doctor MCP entry probes** - locked supported MCP entry shapes and bounded
  `--help` probing behavior in setup-wizard tests.

### Changed
- **Public facts refreshed** - current local facts now report 2782 passed,
  2 skipped, 2784 collected tests, and 82 MCP tools: 17 core plus 65 advanced.
- **OpenClaw claim boundary held** - OpenClaw remains documented as L3 static
  snapshot A/B for the compatible file bridge; live OpenClaw agent/model
  continuity is still not claimed.

## [3.49.1] - 2026-06-05

### Fixed
- **Governance ledger integrity check** - `integrity` now correctly unpacks the
  `(ok, message)` result from ledger verification instead of treating any
  non-empty tuple as truthy. Tampered governance ledgers are now reported as
  unhealthy instead of silently passing.

### Security
- **Identity-card export redaction** - exported identity cards now scrub
  credential-shaped values, bare emails, and absolute user-home paths from
  lesson summaries and decision question/choice text before rendering.
- **Telemetry trust-document correction** - `SECURITY.md`, `CONTRIBUTING.md`,
  `docs/architecture.md`, and `docs/comparison.md` now describe the same layered telemetry model as
  `PRIVACY.md`: local telemetry first, remote telemetry and feedback as separate
  explicit opt-ins, and no identity/content/project-path/free-text collection.
- **Test-data boundary wording** - client-validation and historical plan docs now
  use placeholder paths for isolated run roots and owner desktops instead of
  real local machine paths.

### Added
- **Public claim drift sweep** - `scripts/check_public_claim_drift.py` scans
  current tracked Markdown surfaces for stale quantified public claims and
  overclaim phrases, while explicitly skipping historical release evidence.
- **Export redaction linter** - `scripts/check_export_redaction.py` scans
  rendered export surfaces for high-confidence secret and PII shapes using
  metadata-only findings.
- **Release orchestration dry run** - `scripts/release_orchestrator.py` renders
  a local-only release checklist that makes GitHub, PyPI, MCP Registry, and
  manual Glama/auth stalls visible before remote release actions.
- **Deterministic evidence harnesses** - added offline synthetic harnesses for
  client A/B signal availability, version-chain determinism, recall-ranking
  reproducibility, backup/restore round trips, read-only management surfaces,
  store integrity faults, MCP tool schema drift, offline install matrices, and
  bounded concurrency stress.

### Changed
- **Telemetry boundary tests** - default-off telemetry is now covered by sealed
  network tests proving disabled telemetry does not write local logs or attempt
  remote calls. Remote telemetry still requires explicit opt-in.
- **Public facts refreshed** - current local facts now report 2743 passed,
  8 skipped, and 2751 collected tests.

## [3.49.0] - 2026-06-04

### Added
- **Opt-in import version-chain materialization** - full-backup imports now support
  `engram import <backup.json> --apply --yes --materialize-version-chain` for
  owner-confirmed same-key knowledge conflicts. Divergent lessons/decisions that
  dry-run already marks as `review_version_chain_candidate` are imported as new
  active entries, linked with a `supersedes` edge, and the older entry is marked
  `outdated`. Default `--apply --yes` merge behavior remains conservative and
  does not materialize conflicts. The result payload is metadata-only.
- **MCIC v1 benchmark** - `demos/mcic_benchmark.py` adds a synthetic,
  metadata-only Multi-Client Identity Continuity benchmark with 10 purpose-labeled
  scenarios. It covers explicit recall, implicit personalization signals,
  adversarial false-premise guard signals, public-action boundaries,
  version-chain HEAD selection, negative control, and provenance round-trip. The
  benchmark's claim is intentionally narrow: Engram makes the signal available
  to the next client; live model compliance still needs separate A/B testing.
- **Client-validation evidence harness** - `src/piia_engram/client_validation.py`
  and `scripts/run_client_validation.py` standardize copied-store client tests
  for Hermes, OpenClaw, Cursor, and future MCP hosts. The scaffold records
  purpose, isolated source/target paths, zero-pollution hash evidence, and
  public-safe bilingual summaries, while claim guards prevent unverified live
  client behavior from being reported as passed.

### Changed
- **MCP startup sync latency** - startup reconciliation now runs in a daemon background thread by default, so stdio clients are not blocked during MCP initialize by local AI memory/config scans. Set `ENGRAM_MCP_STARTUP_SYNC=eager` to restore the previous synchronous behavior or `ENGRAM_MCP_STARTUP_SYNC=off` to skip startup sync for latency-sensitive validation arms. `auto_migrate()` remains synchronous for stdio startup, and startup reconcile shares a process-local write lock with MCP write tools to avoid overlapping read-modify-write JSON updates.
- **Cursor plugin display name** - the Cursor plugin manifest now uses
  `piia-engram` as its display name so the local plugin surface matches the
  package and MCP Registry identity.

## [3.48.3] - 2026-06-04

Local import candidate release. Engram separates full-backup import/export from
the core engine and adds a safer owner import preview path. The default import
CLI remains read-only and metadata-only until the owner explicitly uses
`--apply --yes`.

### Added
- **Owner import preview CLI** - `engram import <backup.json>` now defaults to a
  metadata-only dry run. Mutating import requires `--apply --yes`; `--overwrite`
  maps to replace-mode import.
- **Semantic conflict preview** - dry-run import now flags same-summary lessons
  and same-question decisions with divergent semantic fields as
  `review_version_chain_candidate` conflicts, without writing version-chain
  edges or changing stored knowledge.

### Changed
- **Import/export extraction** - full-backup export/import logic moved into
  `ImportExportMixin`, reducing the core engine surface and isolating the next
  version-chain materialization work.

### Release Evidence
- See `release-evidence/v3.48.3.md`.

## [3.48.2] - 2026-06-04

OpenClaw bridge hardening patch. The OpenClaw-compatible `MEMORY.md` export now
includes only verified, active knowledge and enforces a conservative byte budget
so static file bridges cannot leak staging/pending review content or grow
without bounds.

### Fixed
- **OpenClaw `MEMORY.md` export boundary** - `export_to_openclaw` now filters
  lesson and decision exports to `tier=verified` and `status=active`. Staging,
  pending, archived, rejected, or non-active entries are excluded from the
  static bridge files by default.

### Changed
- **Static bridge size guard** - `MEMORY.md` output is capped at 32 KiB with
  clipped summaries/reasoning, keeping OpenClaw-compatible snapshots readable
  and safe for external clients.
- **Client-validation documentation** - added a purpose-first validation
  runbook for Cursor Agent, Hermes, OpenClaw-compatible flows, and future MCP
  hosts, including evidence requirements, negative controls, and zero-pollution
  checks.

## [3.48.1] - 2026-06-04

Performance patch — memoize tokenization on the hot search path. No behavior
change: search output (token sets, alias expansion, ranking) is identical; no
API, schema, telemetry, governance, or permission change.

### Changed
- **Tokenization cache** — `_tokenize` now delegates to a process-wide
  `@lru_cache`d pure function keyed on `(text, expand_aliases)` and the
  import-time-static alias tables. The hot search path re-tokenized the same
  entry fields on every query; memoizing collapses that repeated CPU work into
  a dict lookup. Warm full-corpus keyword search median ~53ms → ~20ms (−62%).
  The cached value is an immutable `frozenset` — read-only consumers
  (`_score_item` field intersection, `_bigram_similarity`) use it directly,
  while callers that mutate (e.g. `_score_item`) get a fresh `set` copy.

## [3.48.0] - 2026-06-03

Local product batch — owner-confirmed apply paths and readiness surfacing. All
changes are CLI / owner-only and metadata-only; no new agent-facing MCP apply
tool, no telemetry schema change, no permission/governance change, no hard
delete, and nothing is published.

### Added
- **Product-use flow hardening** - `engram merge --json` now returns the same
  metadata-only dry-run apply payload as `engram merge apply`, so preview JSON
  never echoes suggestion summaries or stored bodies.
- **Reconcile conflict preview v2** - `engram reconcile conflicts [--json]`
  surfaces conflict counts and match ids only. It is read-only, metadata-only,
  and never imports, supersedes, or overwrites existing decisions.
- **GUI-safe owner actions** - `engram dashboard --json` now includes
  `next_action` plus an `actions` list with code/label/command/count/risk and
  `executes=false`, giving a future UI safe metadata to render without adding
  one-click mutation.
- **Telemetry dashboard password rotation helper** -
  `scripts/rotate_telemetry_dashboard_password.ps1` generates or accepts a
  shell-safe `DASH_PASSWORD`, prints it for owner handoff first, and only writes
  the Cloudflare Worker secret when `-Apply` is present.
- **Near-duplicate merge apply (N4)** — `engram merge` lists metadata-only
  near-duplicate suggestions; `engram merge apply` previews/folds them via the
  existing reversible `merge_knowledge` soft archive (secondary marked
  `outdated`/`merged_into`, never hard-deleted). Dry-run by default; `--commit
  --yes` to apply. New `src/piia_engram/merge_apply.py`.
- **Reconcile import apply (N2)** — `engram reconcile` classifies external AI
  memory candidates (import / duplicate / conflict / skip); `engram reconcile
  apply` imports ONLY the novel (`import`) candidates. Duplicates and conflicts
  are surfaced as metadata no-ops and never mutate existing knowledge
  (conflict→supersede resolution is deferred). Dry-run by default; `--commit
  --yes` to import. New `src/piia_engram/reconcile_apply.py` plus a read-only
  `Engram.collect_memory_candidates()` scanner.
- **Version-chain HEAD surfacing (N5)** — `version_chain.head_ids()` plus
  render-only annotations: recall now reports `meta.version_chain`
  (collapsed/heads_present) and the resume brief notes when superseded version
  chains exist (recall/dashboard surface the current HEAD).
- **Owner dashboard readiness counts (D)** — `engram dashboard` now includes a
  metadata-only `readiness` block: pending owner-confirmed applies across
  lifecycle, reconcile, near-duplicate merge, and version-chain HEAD state.

### Changed
- README and README.zh-CN current-state test counts updated to the verified
  baseline: 2469 passed, 8 skipped, 2477 collected.

## [3.47.1] - 2026-06-03

Public truth sync patch: Engram now has a machine-readable public facts
manifest and release/CI gates that block README, manifest, tool-count,
test-count, and version drift before public publishing.

### Added
- `docs/public-facts.json` as the local development source of truth for current
  version, test count, MCP tool split, and telemetry default posture.
- `scripts/check_public_fact_sync.py`, now run in CI and the PyPI publish
  workflow before release gates and package upload.
- `scripts/count_mcp_tools.py`, a deterministic AST-based helper for re-deriving
  the MCP tool split without importing the package.
- `docs/runbooks/public-truth-sync.md`, documenting released-vs-dev truth,
  remote registry boundaries, and the live verification checklist.

### Changed
- README and README.zh-CN current-state tables now reflect the verified
  post-guard baseline: 2415 passed, 8 skipped, 2423 collected.
- The local release-build runbook now includes the public fact sync guard before
  private-term scanning and release evidence checks.

### Release Evidence
- See `release-evidence/v3.47.1.md`.

## [3.47.0] - 2026-06-03

The telemetry completion release: Engram closes the Telemetry Analysis Contract
v1/v1.1 loop with local readiness validation, dashboard analysis tiles, remote
D1/Worker closeout evidence, and explicit dashboard access-control guidance.
Telemetry remains opt-in; no identity, project path, prompt, knowledge body, or
free-text content is collected.

- **`engram telemetry-validate --remote-readiness`** — a pure, read-only
  pre-deploy checklist (payload↔schema mapping, worker event/feedback
  allowlists, both migration files, v1-before-v1.1 sequencing, dashboard
  anonymous-daily-id wording + v1.1 tiles, client opt-out defaults, no content
  fields). Performs no network/D1/deploy action.
- **Dashboard v1.1 analysis tiles** — the worker dashboard now renders the v1.1
  derived buckets (version adoption, knowledge activation, anonymous returning
  bucket, error trend), gated on the v1.1 migration and labelled as anonymous
  daily-id buckets (never "unique users").
- **Consolidated remote-closeout runbook** — a single canonical sequence
  (validate → v1 migration → v1.1 migration → deploy → health → smoke → verify
  → cleanup → rollback) with host/DB placeholders; the v1 and v1.1 runbooks now
  cross-link to it.
- **Telemetry privacy evidence** (`docs/telemetry-privacy.md`) — the explicit
  opt-in / no-content / rotating-daily-id / user-gated-activation statement,
  including the dashboard `DASH_PASSWORD` boundary.
- **Local worker smoke harness** — static tests pinning the three insert tiers
  (full v1.1 → v1 fallback → legacy), content-field rejection, and dashboard
  labels, with an optional node execution harness under `worker/test/`.
- Fixed a latent `parse_added_columns` false positive that read a column name
  out of an SQL comment.

### Operations
- After explicit user confirmation, the remote D1 schema was migrated to 19
  columns, the `engram-telemetry` Worker was deployed, a smoke event verified
  P0/P1 persistence and was deleted, and `DASH_PASSWORD` was set for the live
  dashboard.
- Final remote telemetry count after cleanup: 12 events and 2 anonymous daily-id
  buckets.

### Tests
- Telemetry readiness: `READY` (9/9 checks).
- Telemetry Python tests: 122 passed.
- Worker/v1.1 tests: 26 passed.
- Worker smoke harness: all smoke checks passed.

### Release Evidence
- See `release-evidence/v3.47.0.md`.

## [3.46.0] - 2026-06-03

The trust-and-readiness release: Engram productionizes the memory trust loop and adds a wide set of local, additive, proposal-only readiness surfaces (phases 6-13) without changing any default behavior or touching remote state.

### Added
- **Memory trust loop** - recall now carries provenance and freshness signals end to end. New `recall`, `quality_eval`, and `reports_review` surfaces wire the provenance/freshness contract through the MCP server so retrieved knowledge can be shown with its source and staleness without changing ranking defaults.
- **Recall and version-chain surfaces** - new `recall_service` and `version_chain` modules expose deterministic recall and knowledge version-chain projections locally; covered by recall-quality and recall/version end-to-end audits.
- **`engram backup-plan` CLI** - previews a metadata-only backup plan for local Engram data before any upgrade, with no destructive action.
- **`engram export-agents-md` CLI** - exports an `AGENTS.md` identity/context file for non-MCP tools from local knowledge, owner-gated.
- **Lifecycle / integrity / reconcile safety surfaces** - new `lifecycle`, `integrity`, and `reconcile_proposal` modules plus `engram lifecycle` and `engram integrity` CLIs produce proposal-only previews (decay/scale, self-diagnostics, conflict reconciliation) that never mutate stored knowledge on their own.
- **Owner control surfaces** - new `engram dashboard` (owner_dashboard), `engram release-check` (release_readiness), and `engram telemetry-validate` (telemetry_validation) local CLIs summarize state, release-evidence readiness, and telemetry payload validity as metadata-only projections.
- **Cross-tool continuity harness** - new `continuity_harness` module and corpus give a local, deterministic check that resume/continuity output stays coherent across tools.
- **Permission Profile vNext (read-only scaffolding)** - `permission_profile_vnext` lands the profile model and previews only; the read-gate enforcement wiring remains gated and is not enabled by default.

### Changed
- **Telemetry analysis contract v1.1 (local buckets)** - opt-in local telemetry payloads now include v1.1 derived buckets (version adoption, activation state, returning bucket, error trend) while keeping the transport `schema` unchanged and all values as short, timestamp-free buckets.
- Owner/management projections continue to summarize counts and states without printing local project paths or stored knowledge bodies.

### Security
- **send_feedback boundary hardening** - the feedback/telemetry send boundary now runs a fail-closed denylist and field validation so no free-text knowledge content leaves the local send boundary; ambiguous very-short CJK tokens are rejected as an accepted residual.
- **Release / public boundary hardening** - publish allowlist refreshed and positioning copy kept honest (no remote-deploy, real-sync, live-Cursor-hook, or read-gate-enforcement claims) so the public package surface stays aligned with what actually ships.

### Tests
- Full suite: **2327 passed**, 8 skipped, 4 expected `engram_core` deprecation warnings.
- Added audits: provenance wiring, recall/version end-to-end, proposal correctness/determinism, feedback denylist, telemetry contract v1.1, lifecycle/integrity/reconcile, owner dashboard, release readiness, continuity harness, and permission-profile-vNext previews.

### Not executed (user-gated)
- No remote Cloudflare Worker / D1 migration, PyPI upload, MCP Registry or Glama update, GitHub Release, tag, or push is performed by this release prep.
- Permission Profile vNext read-gate enforcement, Cursor live stop-hook write-back, and real multi-device sync remain designed-but-gated and are not enabled.

### Release Evidence
- See `release-evidence/v3.46.0.md`.

## [3.45.3] - 2026-06-01

The publication-boundary correction release: Engram removes an internal built-in Playbook template from the public package surface and adds build-artifact private-term scanning to the release gate.

### Fixed
- Removed the internal built-in Playbook template from source, CLI help, status output, README command examples, changelog wording, and release-note evidence so the public package only describes generic Playbook engine capabilities.
- Kept status and management surfaces metadata-only without advertising maintainer workflow templates.

### Security
- Added a release artifact private-term scanner that extracts wheel and sdist artifacts after build and scans generated metadata, README copies, packaged tests, and package files with gitignored maintainer-private patterns.
- The PyPI publish workflow now runs the source sanitizer with internal strict mode and runs the artifact private-term scan in strict mode before publishing.
- Release evidence now requires an `artifact-private-scan` marker, so package-level private-term scanning is a CI-enforced release gate.

### Release Evidence
- See `release-evidence/v3.45.3.md`.

## [3.45.2] - 2026-06-01

The CI entry-point patch release: Engram now makes the cross-tool resume benchmark test robust under the `pytest` console-script entry point used by GitHub Actions, not only `python -m pytest` local runs.

### Fixed
- Added an explicit repository-root import guard in `tests/test_cross_tool_resume_benchmark.py`, fixing CI collection when `pytest` runs with `src` on `PYTHONPATH` but without the repository root on `sys.path`.

### Tests
- Full suite: **2020 passed**, 1 skipped, 4 expected `engram_core` deprecation warnings.
- CI-style regression: `pytest tests/test_cross_tool_resume_benchmark.py -q` passes without `PYTHONPATH`.

### Release Evidence
- See `release-evidence/v3.45.2.md`.

## [3.45.1] - 2026-06-01

The CI packaging patch release: Engram now packages the `demos` namespace so clean CI checkouts can import the cross-tool resume benchmark tests consistently on Linux, macOS, and Windows.

### Fixed
- Added a package initializer for `demos/`, fixing `ModuleNotFoundError: No module named 'demos'` during CI collection of `tests/test_cross_tool_resume_benchmark.py`.

### Tests
- Full suite: **2020 passed**, 1 skipped, 4 expected `engram_core` deprecation warnings.
- Release gates: sanitize high=0/warn=0, publish allowlist complete, package build + twine check passed.

### Release Evidence
- See `release-evidence/v3.45.1.md`.

## [3.45.0] - 2026-06-01

The extraction and management workflow release: Engram now filters short-lived reminders more reliably, keeps metric-backed operational findings, and exposes safer metadata-only management projections for future UI work.

### Added
- **Metric-backed extraction signal** - automatic extraction now recognizes measured outcomes such as latency reductions, percentages, timing changes, and regressions so concrete operational findings are more likely to be saved for review.
- **Ephemeral reminder filter** - short-lived personal reminders such as tomorrow/send/email/call/remind tasks are rejected from durable memory unless they also carry durable evidence or measured outcomes.
- **Playbook migration impact summaries** - legacy playbook scope apply/rollback previews now include metadata-only impact counts, target scope distributions, skipped reason counts, and confirmation status without exposing playbook titles, bodies, steps, or project paths.
- **GUI-ready management filters** - `engram management` and `build_management_view()` can filter review items by kind/quality and playbooks by state/scope while keeping the projection metadata-only.

### Changed
- Management text and JSON outputs continue to summarize counts and states without printing local project paths or stored knowledge bodies.
- Claude acceptance is now run as a narrow read-only review while Codex records local test evidence, reducing timeout risk on complex release checks.

### Tests
- Full suite: **2020 passed**, 1 skipped, 4 expected `engram_core` deprecation warnings.
- Release gates: sanitize high=0/warn=0, publish allowlist complete, package build + twine check passed, Claude acceptance PASS.

### Release Evidence
- See `release-evidence/v3.45.0.md`.

## [3.44.0] - 2026-06-01

The setup file-safety release: Engram now defaults to read-only external MCP client configuration during setup, lets users choose the Engram data location, and keeps Engram-owned file backups before replacing local JSON stores.

### Added
- **Selectable Engram data folder** - the setup wizard now lets users choose the default local data folder, another drive, or a custom Engram root; generated client entries carry that `ENGRAM_DIR` forward.
- **File-safety backup ledger** - Engram-owned writes now create timestamped backups before replacing existing JSON stores, with a redacted ledger that records only metadata and paths relative to the Engram root.
- **Management action CLI** - `engram management action request|approve|reject|complete|list` records metadata-only management receipts so future UI work can expose user-controlled cleanup and review flows without printing stored knowledge bodies.

### Changed
- Setup no longer rewrites external AI client config files by default. Users can opt into config updates with `--apply-external-config`, while dry-run and normal setup leave external files byte-for-byte unchanged.
- `engram doctor --fix` and explicit setup config writes now route through the same backup-and-ledger path before touching external config files.
- Legacy MCP configs keep their existing custom `ENGRAM_DIR` during repair or upgrade unless the user explicitly selects a new data location.

### Fixed
- `auto_migrate()` now treats legacy JSON and TOML MCP client configs as read-only guidance instead of silently modifying them during import-time startup.
- Storage updates now back up existing Engram-owned JSON files before atomic replacement.

### Tests
- Full suite: **1994 passed**, 1 skipped, 4 expected `engram_core` deprecation warnings.
- Release gates: sanitize high=0/warn=0, publish allowlist complete, package build + twine check passed, Claude acceptance PASS.

### Release Evidence
- See `release-evidence/v3.44.0.md`.

## [3.43.0] - 2026-05-31

The continuity diagnostics release: Engram now has a shareable metadata-only handoff proof, recall-loop counters, and clearer Windows encoding diagnostics.

### Added
- **Metadata-only continuity proof** - `engram continuity` reports saved-session counts, contributing tools, resume-brief build status, aggregate context-load / wrap-up signals, and cross-tool readiness without printing memory bodies, raw telemetry events, session IDs, decision reasoning, or local paths.

### Fixed
- `engram repair-encoding` now clarifies that a clean scan means stored Engram data is healthy, and points Windows/PowerShell users to terminal display encoding when UTF-8 files still appear garbled.
- `engram status` now falls back to a sibling `piia-engram-mcp` launcher when the CLI is started by absolute path and the console-script directory is not on `PATH`, avoiding a false MCP-entry warning in Windows/Codex runtime workflows.

### Tests
- Full suite: **1834 passed**, 1 skipped, 4 expected `engram_core` deprecation warnings.
- Release gates: sanitize high=0/warn=0, publish allowlist complete, release evidence complete, Claude acceptance PASS.

### Release Evidence
- See `release-evidence/v3.43.0.md`.

## [3.42.0] - 2026-05-31

The trust, resume, and portability foundation release: Engram now gives safer recovery diagnostics, a stronger cross-tool resume brief, trust-mode metadata primitives, and metadata-only configuration integrity checks.

### Added
- **Recovery retention dry-run** - `engram recover-json lessons` now reports a content-free retention plan for valid recovery candidates, including overlap/union/overflow counts and a recommendation, without restoring or printing lesson bodies/raw IDs.
- **30-second resume handoff** - `get_resume_brief()` now starts with a compact handoff that names the project, latest activity, next action, and a trust note that memory is reference context rather than fresh user approval.
- **Trust-mode metadata primitives** - lessons and decisions now carry derived `memory_state`, `approval_status`, `provenance`, `risk_level`, `risk_flags`, and `approval_required` fields while preserving existing `tier`/`status` compatibility.
- **Config integrity diagnostics** - terminal `engram doctor` now reports metadata-only MCP config, AI instruction, shared instruction, Claude hook, and project-rule integrity counts/hashes.

### Changed
- Trust metadata is derived server-side and monotonic: callers cannot self-promote staging entries, suppress high-risk flags, or disable approval for high-risk memory.
- `get_resume_brief()` and non-fix doctor continuity checks read lessons/decisions without implicitly backfilling legacy knowledge files.
- Documentation now describes the current access-based staging promotion path for lessons/decisions while keeping playbook review explicit.

### Fixed
- Hardened entry help and encoding reads so CLI help avoids initialization side effects, Windows UTF-8 output decodes reliably, and JSON readers accept UTF-8 BOM.
- Fixed Codex/MCP config generation so Windows-style source paths derive the correct `PYTHONPATH` even when tests or setup run on Linux/macOS.
- Closed a trust-field self-downgrade edge where caller-supplied `memory_state`, `risk_level`, `risk_flags`, or `approval_required` could conflict with server-derived state.
- Closed a non-`--fix` doctor side effect where building a resume brief could rewrite old-format knowledge files.
- Fixed staging-to-verified promotion so derived trust metadata updates from `staging/pending` to `verified/approved` after review or access-based promotion.

### Tests
- Full suite: **1826 passed**, 1 skipped, 4 expected `engram_core` deprecation warnings.
- Release gates: sanitize high=0/warn=0, publish allowlist complete, package build + twine check passed, Codex subagent reviews PASS, Claude Code read-only acceptance PASS.

### Release Evidence
- See `release-evidence/v3.42.0.md`.

## [3.41.0] - 2026-05-31

The market-positioning and trust package release: Engram now presents itself more precisely as a local-first personal AI identity layer for MCP-compatible coding tools, with clearer trust boundaries and a public cross-tool continuity demo.

### Added
- **Trust model documentation** - `docs/trust.md` explains what stays local, what is never sent by default, governance boundaries, user controls, and known limitations.
- **Cross-tool continuity demo** - `docs/cross-tool-continuity-demo.md` and `demos/cross_tool_continuity_demo.py` show a simulated Claude Code -> Codex -> Cursor/Windsurf handoff using an isolated temporary Engram root.
- **Listing copy pack** - `docs/listing-copy.md` provides conservative marketplace copy for MCP Registry, Claude plugin, PyPI, GitHub, and website surfaces.
- **Public positioning regression tests** - `tests/test_public_positioning.py` guards against old overclaims, MCP Registry description length drift, demo path disclosure, and missing publish allowlist entries.

### Changed
- README, Chinese README, PyPI metadata, MCP Registry metadata, Claude plugin metadata, architecture notes, and comparison docs now use the narrower "local-first personal AI identity for MCP-compatible coding tools" positioning.
- `docs/comparison.md` now distinguishes Engram from OpenMemory and native coding-tool memory instead of positioning it as a generic agent-memory database.
- Chinese README security docs now include the optional agent-governance environment variables and their non-cryptographic identity boundary.

### Tests
- Full suite: **1788 passed**, 4 expected `engram_core` deprecation warnings.
- Release gates: sanitize high=0/warn=0, publish allowlist complete, MCP Registry manifest valid, Codex subagent review PASS, Claude Code read-only acceptance PASS.

### Release Evidence
- See `release-evidence/v3.41.0.md`.

## [3.40.0] - 2026-05-31

The first-run confidence release: Engram now gives users a clearer local status surface after setup, including MCP client configuration health, shareable redacted HTML status, and follow-up commands.

### Added
- **MCP client summary in `engram status`** - the CLI now reports configured / missing client entries using redacted metadata only.
- **Richer status HTML** - `engram status --html` now includes an MCP Clients table and Next Commands section for `engram doctor`, `engram review`, and `engram sessions`.
- **Status probe coverage** - the status path now has regression coverage for bounded MCP entry probing and `status --help` output.
- **Codex + Claude acceptance workflow** - local project workflow documentation now records the agreed Codex-implements / Claude-accepts review loop.

### Changed
- `engram status --html` renders the Engram storage path as `<engram-root>` so the generated HTML can be shared as redacted evidence without exposing local user paths.
- `scripts/release_sanitize_check.py` now uses ASCII-only user-visible messages for the custom sensitive-term notice and internal-scan help text, avoiding Windows terminal mojibake.
- README, Chinese README, architecture notes, privacy examples, MCP registry metadata, and Claude plugin metadata are synced to v3.40.0.

### Fixed
- Closed a status-report disclosure edge where HTML output embedded the local Engram storage path through the text status block.
- Added a regression test to ensure status HTML does not contain MCP config paths, entry args/env, tokens, or local Engram root paths.

### Tests
- Full suite: **1781 passed**, 4 expected `engram_core` deprecation warnings.
- Release gates: sanitize high=0/warn=0, publish allowlist complete, Codex subagent review PASS, Claude Code read-only acceptance PASS.

### Release Evidence
- See `release-evidence/v3.40.0.md`.

## [3.39.1] - 2026-05-30

The terminal encoding diagnostics patch release: Engram now helps users distinguish real stored-data mojibake from Windows/terminal display encoding issues.

### Added
- **Terminal encoding diagnostics in `engram doctor`** — the CLI doctor now reports stdout/stderr encoding, `PYTHONIOENCODING`, and Python runtime encodings separately from stored-data `Encoding health`.
- **Windows UTF-8 code page support** — code page `cp65001` is recognized as UTF-8 so Windows terminals configured with `chcp 65001` are not reported as legacy encodings.

### Changed
- `engram doctor` treats an unset `PYTHONIOENCODING` as OK when stdout/stderr are already UTF-8, reducing false "needs attention" output for healthy terminals.
- README, Chinese README, cross-tool guide, architecture notes, and privacy examples are synced to v3.39.1.

### Fixed
- Prevented a confusing diagnosis loop where clean Engram data could still look suspicious because the terminal display layer, not the store, rendered Unicode poorly.

### Tests
- Full suite: **1767 passing**, 4 expected `engram_core` deprecation warnings.
- Release gates: sanitize high=0/warn=0, publish allowlist complete, Claude Code read-only review PASS.

### Release Evidence
- See `release-evidence/v3.39.1.md`.

## [3.39.0] - 2026-05-30

The local workflow visibility release: Engram now gives users and AI agents clearer local surfaces for saved sessions, staged-knowledge review, and governance boundaries, without changing the default local-first privacy model.

### Added
- **Session continuity CLI** — `engram sessions` lists saved cross-tool agent sessions using metadata only, and `engram sessions show <id>` prints one explicitly requested session.
- **Doctor continuity checks** — `engram doctor` now includes a Continuity section that distinguishes a clean "no saved sessions yet" install from real resume-brief failures.
- **Staged knowledge review CLI** — `engram review`, `engram review show <id>`, `engram review approve <id> --yes`, and `engram review archive <id> --yes` provide a terminal path for approving or archiving staging lessons and decisions.
- **Governance documentation** — new public documentation explains caller trust levels, read/write/export gates, file-side-effect hardening, corpus encryption boundaries, and the deny-by-default tool matrix.

### Changed
- README, Chinese README, architecture notes, and privacy documentation now agree on the 16 Core / 56 Advanced / 72 total MCP tool split, `enc:v2` encryption wording, session storage layout, playbook storage layout, and current CLI surfaces.

### Tests
- Full suite: **1762 passing**, 4 expected `engram_core` deprecation warnings.

## [3.38.0] - 2026-05-30

### Added
- **Encoding repair guardrails** — new `engram repair-encoding` CLI dry-runs the active Engram root for high-confidence mojibake in JSON / JSONL / Markdown / text files. `--apply` repairs reversible cases with a timestamped backup; lossy suspicious cases are reported for manual review instead of being guessed.
- **Doctor encoding health check** — `engram doctor` now includes an "Encoding health" section, and `engram doctor --fix` can repair reversible mojibake as part of the normal self-diagnosis flow.

### Fixed
- **Windows stdio UTF-8 hardening** — the MCP server now reconfigures stdout/stderr to UTF-8 on startup so Windows GBK/CP936 console defaults cannot corrupt MCP JSON frames or Chinese text output.
- **Incoming text normalization** — lesson, decision, playbook, profile, project snapshot, and saved-context write paths now repair only high-confidence mojibake before persisting, while leaving valid Chinese untouched.

### Tests
- Added regression coverage for reversible GBK mojibake repair, non-repair of valid Chinese, lossy/suspect reporting, Markdown context repair, `doctor` integration, and MCP stdio UTF-8 startup.
- Full suite: **1742 passing**, 4 expected `engram_core` deprecation warnings.

## [3.37.0] - 2026-05-30

The GUI-entry adoption release: piia-engram now exposes a universal MCP server command that is easier to paste into GUI AI tools, and the setup wizard covers two more home-level MCP clients.

### Added
- **`piia-engram-mcp` console entry point** — MCP clients can now launch the server with a single command, instead of spelling out `python -m piia_engram.mcp_server`. The old module path still works and calls the same `main()` function.
- **Zero-install MCP config path** — README examples now document `uvx --from piia-engram piia-engram-mcp` for clients where users want to paste a command without pre-installing the package.
- **Trae and Tencent CodeBuddy setup support** — `engram setup` can write their standard home-level MCP config files (`~/.trae/mcp.json` and `~/.codebuddy/mcp.json`).
- **Domestic AI IDE setup docs** — README / README.zh-CN now distinguish auto-configurable tools from UI-managed or project-scoped tools such as Tongyi Lingma, Baidu Comate, and Qoder.

### Tests
- Packaging tests now pin the new `piia-engram-mcp` entry point and verify that it resolves to an importable callable.
- Setup-wizard tests now pin the Trae and CodeBuddy config paths so future refactors cannot silently drop those GUI entry points.
- Full suite: **1720 passing**.

## [3.36.0] - 2026-05-30

The identity-layer security release: knowledge content is encrypted at rest, every AI tool sees its own permission boundary inline, and the governance layer is sealed against both write bypass and read-path side effects. The governance and encryption work each went through multiple rounds of independent (Codex) adversarial audit; the read-path closure alone took five rounds.

### Added
- **Corpus encryption at rest (a5)** — knowledge content fields (`summary`, `detail`, `question`, `choice`, `reasoning`, `title`, `description`, `outcome`) are encrypted with a pre-derived key (PBKDF2-SHA256 600K + per-engram `.corpus_salt`) and per-field random AES-GCM nonce, under a new `enc:v2c:` prefix. Metadata stays plaintext so search and filtering still work. Backward compatible: plaintext entries pass through transparently and are lazily re-encrypted on next write. Playbook compound fields (steps / pitfalls / preconditions), playbook index titles, and execution-plan derived files (including step notes) are all covered.
- **Caller permissions surfaced inline (a1–a3)** — AI tools now learn their governance status, trust level, sensitivity ceiling, and write policy from the first message, with no extra MCP call: `get_user_context` and `get_resume_brief` append a "Caller Permissions" section, and `search_knowledge` / `get_relevant_knowledge` results carry a `_caller_permissions` key.
- **`TOOL_GOVERNANCE_CLASS`** — a deny-by-default classification of every `@mcp.tool`. A reflection test red-lights any tool that is neither classified nor explicitly exempted, so a future un-gated tool cannot ship silently.
- **`maybe_refuse_owner_write`** governance helper for owner-only write/export pre-gating.

### Security
- **Write-path governance gate (a4)** — 18 write tools (`add_lesson`, `add_decision`, `add_playbook`, `memory_store`, `update_knowledge`, `archive_knowledge`, `review_knowledge`, `merge_knowledge`, `link_knowledge`, `unlink_knowledge`, `update_playbook`, `archive_playbook`, `update_identity`, `register_tool`, `save_project_snapshot`, `start_project`, `save_agent_context`, `update_execution_step`) refuse before executing when the caller's write policy is "no" (read-only-external). Owner and trusted-local callers pass through.
- **Read paths are disk-side-effect-free for non-owners (R5–R9)** — when governance is enabled, a `read-only-external` / low-trust caller can no longer cause any file write through a read-classed tool. Previously a "refused" read could still have written to disk *before* the refusal. Closed surfaces: access-count / `last_reviewed` write-back on knowledge reads, telemetry (`_track` flush and `_beta` event files), `audit.log` entries, and `contexts/mcp_auto/*` session checkpoints (which only triggered after a call-count threshold, so single-call tests had missed them).
- **Owner-only pre-gate on permission-management tools** — `set_caller_trust`, `revoke_caller`, and import now refuse before any side effect, so a low-trust caller cannot self-escalate by writing grants first and being governed second.
- **`get_identity_card` reclassified as export-owner-only** — its on-disk export is gated as a write surface, not treated as a plain read.
- **All governance gates fail closed** — if owner resolution raises (corrupt grants, import failure), the side effect is suppressed rather than allowed through. The gate's failure mode is "deny", not "permit".
- **Encryption fail-closed hardening** — a missing `.corpus_salt` in the presence of any existing ciphertext (scanned over full file contents, including derived index/execution files, not just the first 4KB) fails the engram open rather than minting a fresh salt; a stale plaintext `search_index.db` is purged when the corpus key is active so enabling encryption cannot be undermined by a leftover index; the hybrid search index is suppressed entirely under corpus encryption to prevent plaintext materialisation into the FTS table.

### Changed
- **Release gate enforces the R1/R5 self-test admission rules** — `release-evidence/v<version>.md` now requires two presence-only markers in addition to `eval-gate`: `negative-control` (R1: new regression tests for a security-sensitive change must be shown to FAIL on the pre-fix code) and `field-assertion-audit` (R5: every free-text field in a touched security-sensitive module must have an on-disk assertion proving it is not written in the clear). Each must be `passed` or `n/a`. Encodes the discipline learned from the a5 audits, where "the tests I wrote all pass" hid four plaintext-leak P1s.
- CLI `engram reindex` now reports "corpus encryption enabled; persistent search index skipped/purged" instead of a misleading "reindexed 0".

### Tests
- **Governance write-gate matrix: 166 tests** — writer-spy full-root snapshot diffing, a reflection sweep over read tools × client types that repeats each call past the telemetry/checkpoint thresholds, root-external path monitoring (fake `HOME`/`TEMP`), and fail-closed error-path proofs (owner resolution raising must still write nothing), with an owner-control test guarding against over-correction. Every gate is pinned by a revert-to-RED proof: each fix was confirmed to make its regression test fail when removed.
- Corpus encryption and caller-permission work added ~115 tests across a1–a5 and the Codex audit rounds, each with R1 negative-control proofs on the pre-fix commits.
- Full suite: **1718 passing**.

## [3.35.0] - 2026-05-29

Decision threads, decision history, and permission profile — the first release where users can trace how decisions evolved and control who accesses their Engram data.

### Added
- **Decision thread auto-supersedes (c1)**: `add_decision` now automatically creates a `supersedes` edge when the same question gets a different answer. Supports explicit `supersedes` parameter for cross-question superseding. Dedup comparator fixed (`>` → `>=`) so the most recent entry wins on similarity ties.
- **`remove_relation` MCP tool**: undo for `add_relation` — remove a typed relation between knowledge items. Idempotent.
- **`get_decision_history` MCP tool (c2)**: query the full revision history of a decision by question text (not ID). Returns chronological revisions with supersedes chain and current active decision. Uses bigram similarity matching with configurable threshold.
- **Permission profile (a0)**: three new MCP tools for user-facing governance control:
  - `get_permission_profile`: view all callers' trust levels, auto-classification rules, and revoked callers
  - `set_caller_trust`: assign or change a caller's trust level (private-self / trusted-local / read-only-external)
  - `revoke_caller`: forward-revoke a caller's future access

### Changed
- MCP tool count: 65 → **72** (16 Tier-1 Core + 56 Tier-2 Advanced).
- README "By the numbers" refreshed to v3.35.0 data (1439 tests, 72 tools, 16 Core).

### Tests
- 50 new tests: decision-thread c1 (14) + c2 (15) + permission profile (21).
- Full suite: **1439 tests** passing.

## [3.34.0] - 2026-05-29

Governance layer (a0), decision-thread scaffold (c0), and the playbook passive-reference header — the first release with runtime trust enforcement and a product-level "AI does not auto-execute" safety property.

### Added
- **Governance layer (a0, opt-in)**: set `ENGRAM_GOVERNANCE=1` to enable runtime trust enforcement. Non-owner callers (untrusted `web` tier) cannot read, export, or derive stored knowledge above their trust ceiling. All 65 MCP tools are classified deny-by-default: governed (return filtered), export-owner-only (pre-write refusal), or safe-allowlisted (documented). Off by default — zero behavior change without the flag.
- **Playbook `usage_policy` header**: every playbook and execution plan returned by MCP tools now carries a `usage_policy` field instructing consuming AI tools to treat it as a passive reference — confirm with the user before each step, do not auto-drive decisions or execute all steps at once. Applied to `get_playbook`, `get_playbooks`, `get_recent_playbooks`, `prepare_playbook_execution`, `get_execution_status`.
- **Decision-thread scaffold (c0)**: `add_relation` and `get_decision_thread` MCP tools — typed/directed relations between knowledge items with thread reconstruction. Foundation for future decision-chain traceability.
- **Sensitivity auto-classification**: zero-config-safe classifier that assigns `public` / `work` / `secret` based on content heuristics. Used by the governance gate but safe to ignore when governance is off.

### Security / Hardening
- **Governance a0 read-path cutover — 6 rounds of independent Codex review (R15→R20)**:
  - R15: fail-closed for unknown trust tiers + wire all knowledge-body reads
  - R16: deny-by-default coverage for ALL tools (no more prefix-based heuristics)
  - R17: file-side-effect gates for `refresh_quick_context`, `get_identity_card`, `export_knowledge_report`
  - R18: pre-write gate for `prepare_playbook_execution` (execution-plan file leak)
  - R20: hybrid search-index suppression for non-owners (`search_index.db` FTS table leak)
- **Universal file-side-effect harness**: parametrized regression test covering all 41 governed/export tools with case-insensitive content diff, coverage assertion (new tools auto-fail if not in harness), and negative verification (proves the harness catches real leaks).
- **Hash-chained governance audit ledger**: append-only `governance_ledger.jsonl` with SHA-256 chain for tamper detection.

### Changed
- Repository docs adopted English-canonical policy (i18n via separate files).
- Added LobeHub marketplace badge and Awesome-MCP-ZH listing.

### Release Evidence
- Independent Codex review: R20 PASS (a0 read-path full cutover incl. write-echo + export gate + dedup-echo + audit-log + file-side-effect gate + hybrid-index gate).
- Full suite: 1385 tests passing. Governance-specific: 215 tests.
- eval-gate: n/a (no retrieval algorithm change).

## [3.33.2] - 2026-05-28

A batch of correctness / security issues found and fixed by independent code review (Codex)—the first release to fully clear all three gates: "self-review + independent Codex review + evaluation gate."

### Fixed
- **Hybrid search recall guarantee**: when hybrid is enabled, RRF re-ranking + truncation could push keyword-matched items out of the top-N. Keyword results (top-`limit` with score ≥ threshold) are now always retained and then backfilled via RRF, ensuring hybrid recall ≥ keyword recall.
- **Index not rebuilt after installing `[vector]`**: an FTS-only index was first built without a vector backend; after installing the `[vector]` dependency, a rebuild was previously not triggered and the semantic signal stayed absent. "Vector backend availability" is now part of the index freshness fingerprint, and a rebuild is forced when vector is enabled but the vector table is missing.
- **pre-commit secret scan `--staged` false negative**: it previously read working-tree file contents, so if a file containing a secret was `git add`ed and then the working tree was cleaned without re-adding, the secret actually being committed would be missed. `--staged` now scans the staged-area blob (`git show :path`).
- **pre-commit allowlist `--staged`**: `.publishallow` is now read from the staged area, so unstaged local changes no longer affect the commit decision (hook marker v2→v3).

### Security / Hardening
- **Publish workflow hardening**: `publish.yml` removes `workflow_dispatch` (a bypass surface that could be triggered manually from unprotected branches) and verifies before publishing that the release commit is an ancestor of `origin/main`. Adding a deployment-branch restriction in the GitHub repository Environment settings is also recommended.

### Release Evidence
- All three gates passed: self-review + independent Codex re-review (round-2, commit dcd8621, all 6 items verified fixed) + round11 evaluation gate PASS; full suite of 1022 tests passing.

## [3.33.1] - 2026-05-28

Hybrid search patch: an index-freshness fix found in code review.

### Fixed
- **Index not rebuilt after changing the embedding model**: when `ENGRAM_EMBED_MODEL` changed (or the default model was upgraded) but knowledge content stayed the same, the index freshness fingerprint previously counted only content, not the model, so a rebuild was not triggered—the vector table from the old dimensionality lingered and the vector signal was silently disabled (KNN dimension mismatch → swallowed → empty results) until content changed or `engram reindex` was run manually. The embedding model is now part of the fingerprint, so changing the model triggers a rebuild.
- Non-vector indexes no longer write an `embed_model` marker, avoiding a false dimension-drift flag.

## [3.33.0] - 2026-05-28

Hybrid search is now generally available, opt-in, with no change to default behavior.

### Added
- **Hybrid search (opt-in)**: on top of the existing keyword retrieval, it fuses FTS5 full-text search with an optional semantic vector layer, merging rankings via Reciprocal Rank Fusion (k=60). Enable it with `ENGRAM_SEARCH=hybrid`; **the default remains keyword, with behavior completely unchanged**.
  - The semantic layer is installed via `pip install piia-engram[vector]` (sqlite-vec + FastEmbed), with default model **BAAI/bge-small-zh-v1.5** (Chinese-first, overridable via `ENGRAM_EMBED_MODEL`; changing the model automatically rebuilds vectors and does not error on dimension changes).
  - The index is a rebuildable SQLite file, and **JSON remains the single source of truth** (deleting the index allows a full rebuild from JSON), with support for lazy rebuilds (rebuild only when the content fingerprint changes) + incremental vector embedding.
  - FTS5 now performs CJK bigram tokenization, so Chinese is no longer treated as a single token.
  - Added the `engram reindex` command to rebuild the index manually.

### Validated
- A/B evaluation gate (keyword vs hybrid) passed: zero recall regression on the Chinese set (recall@5 1.00→1.00, MRR within tolerance), and **cross-language recall (English query → Chinese knowledge) improved from 0.50 to 0.875**—something keyword retrieval cannot achieve structurally.

### Release Evidence
- Full regression suite of 1005 tests passing.

## [3.32.0] - 2026-05-28

A follow-up to publish-workflow hardening: moving the pre-release security check one step earlier.

### Changed
- **The pre-commit hook now also runs the publish allowlist check**: the pre-commit hook installed by `python scripts/install_git_hooks.py`, in addition to the secret scan, verifies that all staged content is within the `.publishallow` allowlist—adding a new, unregistered tracked file is now blocked at commit time rather than waiting for CI. (Can be bypassed temporarily with `git commit --no-verify`.)

### Security / Hardening
- **The secret scanner adds multi-line scanning**: it previously matched line by line only, so internal narration wrapped across lines (e.g. split by a line break inside a docstring) was missed; it now performs an additional whole-file scan over text files such as `.py` / `.md`, reporting only matches that genuinely span lines, without duplicating the line-by-line results.

### Release Evidence
- Full regression suite passing.

## [3.31.0] - 2026-05-28

Cross-tool auto-resume completion + knowledge tier management + publish-workflow hardening.

### Added
- **Cross-tool session resume**: the instruction snippets for Cursor / Codex / Windsurf now all prompt the AI to call `get_resume_brief` at session start to continue from the previous round of work, consistent with the behavior of Claude Code's SessionStart hook. Added support for Windsurf.
- **Optional pre-commit secret gate**: once installed via `python scripts/install_git_hooks.py`, the staged area is automatically scanned for sensitive content before every commit (can be bypassed temporarily with `--no-verify`).

### Changed
- **`update_knowledge` supports adjusting tier**: a knowledge item can be moved directly between `staging` / `verified` / `archived` without the two-step "archive the old entry + add a new one"; tier changes are written to the audit log.
- **PostCompact hook responsibility narrowed**: the command hook now only archives the compaction summary to the daily log, while semantic extraction (lesson/decision) is handled uniformly by the agent hook, eliminating duplicate writes.
- **doctor detects stale instruction snippets**: it can identify older snippets missing the cross-tool resume instruction and refresh them on `--fix`.
- **README switched to the official Glama quality badge** (dynamic rating, replacing the hand-written badge).

### Security / Hardening
- The specific path patterns of the publish guard moved to a local `.guardignore` (not checked in), with the public workflow keeping only the generic categories.
- Consolidated the storage-scale notes in the comparison docs to avoid repeatedly emphasizing the limits.
- Tightened publish content control: upgraded from a blocklist to a deny-by-default publish allowlist (`.publishallow` + CI verification), and introduced dual-track public / internal CHANGELOGs.
- The secret scanner adds internal-information-leak pattern detection (review codenames, model codenames, etc.).

### Release Evidence
- Full regression suite passing.

## [3.30.1] - 2026-05-27

Fixes an issue where `engram doctor --fix` could not upgrade stale hooks.

### Fixed

- `engram doctor --fix` now correctly upgrades legacy Claude Code hook configurations (e.g. upgrading the old style pointing at a `scripts/*.py` script path to the current `python -m piia_engram.hooks.*` form). Previously doctor's strict-match check reported "missing," but the idempotent skip logic of `--fix` considered it "already registered" and skipped it—leaving users stuck in a "doctor says missing, --fix can't fix it" loop.
- The hook registrar adds a `force_rewrite` parameter: the default `False` preserves the backward-compatible idempotent behavior; `doctor --fix` explicitly passes `True` to overwrite a hook that matches but has stale content. Unrelated user-custom hooks under the same event are unaffected.

### Changed

- `doctor --fix` output changed from "Could not register" to the more accurate "already up to date" (when the hook fully matches the current spec).

### Release Evidence

- Full pytest: 933/933 passing (added 3 force_rewrite tests covering: stale upgrade, no-op not written to disk, coexisting hooks not deleted by mistake).
- Dogfooding self-verification: a stale PreCompact hook on this machine (old `.py` script path) was automatically upgraded to the `-m` form by `doctor --fix`.

## [3.30.0] - 2026-05-27

Cross-session / cross-tool continuation + a full crash-recovery mechanism go live.

### Added
- **Timed heartbeat snapshots**: session state is saved every 5 minutes by default (tunable via the `ENGRAM_HEARTBEAT_INTERVAL` environment variable), reducing data loss when a long session crashes.
- **`get_resume_brief` MCP tool** (Tier-1 core): returns a merged brief of identity card + project snapshot + daily log + recent lessons/decisions, with a default 1500-token budget.
- **`get_daily_log` MCP tool** (Tier-1 core): reads a project's human-readable day-by-day timeline.
- **Daily log layer**: `~/.engram/projects/<hash>/daily/YYYY-MM-DD.md`, with event_type distinguishing session/lesson/decision/compact/checkpoint.
- **PreCompact hook**: triggered before Claude Code compacts a conversation, with a lower trigger threshold than the Stop hook (5 vs 10 turns), preventing state loss when a long session is compacted.
- **PostCompact hook (`auto_absorb_compact.py`)**: triggered after Claude Code compacts a conversation, it extracts a summary from the post-compaction transcript into the daily log (event_type=`compact`) and makes a best-effort call to `extract_session_insights` to automatically extract staging knowledge. Summaries over 3000 characters are automatically truncated.
- **SessionStart hook**: at the start of a new session, the brief is injected into the first-round system prompt via the `hookSpecificOutput.additionalContext` protocol, giving the user resume context with zero action.
- **Audit log on by default**: detects abnormal exits at startup.
- **Doctor 4-hook check**: full coverage of Stop / PreCompact / SessionStart / PostCompact, with `engram doctor --fix` able to auto-register missing items.
- **Doctor cloud-sync directory detection**: identifies whether ENGRAM_DIR is located on an iCloud / Dropbox / OneDrive / Google Drive / NFS / SMB mount and issues a WARN (concurrent writes in these directories can cause lock-file or JSONL inconsistencies).
- **Total MCP tool count**: increased from 61 in v3.29.4 to 65 (Tier-1: 16 / Tier-2: 49).

### Changed
- The generic hook registrar was extracted into internal infrastructure, reused by all four Claude Code hooks.
- The doctor hook check introduces a strict-match mode, distinguishing "any marker hit suffices" from "all markers must hit."
- README / README.zh-CN sync the tool count and Tier-1 table, and add a Remote Deployment section.
- The pre-release secret check is now formally a documented step in the release workflow.

### Fixed
- Optimized the nonce comparison in `save_agent_context` cross-process merge, avoiding an erroneous merge caused by an empty on-disk nonce.
- Optimized `_quote_for_shell` cross-shell compatibility: no quoting when there are no shell-sensitive characters, correct quoting of paths with spaces, compatible with cmd.exe and PowerShell.
- The daily log path calculation for an empty `project_folder` is now consistent with the project_id hash.
- Added a lock around the final auto-save path, avoiding an edge race with the heartbeat thread.
- The heartbeat function's documentation is now consistent with its return-value semantics.
- Multiple copy / wording improvements (the resume brief wording is more neutral, comparison-type docs are more falsifiable).

### Release Evidence
- Full pytest: 930/930 passing.

## [3.29.4] - 2026-05-27

A cross-tool / cross-session audit-driven optimization release. All multi-round regressions passed.

### Added
- **`doctor` MCP tool**: a user troubleshooting entry point covering 8 checks (identity_completeness, health_score, stale_knowledge, near_duplicates, decision_conflicts, knowledge_volume, quick_context_freshness, identity_provenance). Included in the core tier by default.
- **Field-level provenance**: the profile now records `_provenance: {by, at}` for each field, along with `_last_updated_by`, making it easier to track "who changed my preference" across tools.
- **`update_identity` MCP adds a `source_tool` parameter**: passing it records a source entry in the profile.
- **Type-aware staleness decay**: `STALE_DECAY_MULTIPLIERS` adjusts the staleness threshold by domain (`user_preference=3.0`, `architecture=2.0`, `workflow=1.0`, `debug=0.5`), avoiding long-term preferences being wrongly judged stale.
- **Cross-tool usage guide**: added `docs/cross-tool-guide.md`, covering configuration, auto-recovery, multi-tool coexistence, and the doctor troubleshooting workflow.
- **Regression guard tests**: `tests/test_optimizations_v3294.py` locks in 6 key regressions (description rewrite, three-tool coexistence, decision no self-reference, lesson no self-reference, doctor core).

### Changed
- **Three-level Lesson/Decision deduplication** (duplicate / related / pass):
  - `SIMILARITY_DUPLICATE_THRESHOLD` raised from 0.85 to 0.95, avoiding a "supplementary case" being misjudged as a duplicate.
  - Introduced `_SUPPLEMENT_MARKERS` (including `补充/案例/反例/边界/edge case`, etc.), allowing the related path even when similarity is high.
  - Items with 0.55 ≤ sim < 0.95 are written bidirectionally into `related_ids`, with a `_dedup_note` attached.
- **Decision ID generation**: the ID seed now includes `choice`, avoiding "same question, different option" producing the same ID.
- **Field-level description merge**: markers written by multiple tools coexist; rewriting an existing marker does not lose the writes from other tools.
- **`get_lessons` / `get_decisions` no longer update access_count by default**: read paths such as the identity card no longer cause side effects.
- **`related_ids` self-reference guard**: lesson / decision linking skips its own ID, avoiding `related_ids: [self]`.

### Fixed
- `doctor` called a nonexistent `knowledge_overview` method → switched to `get_knowledge_overview()`.
- Re-writing an existing description marker overwrote markers from other tools.
- A decision's `related_ids` showed a self-reference for the same question with a different choice.

### Release Evidence
- All multi-round regression tests passed.

## [3.29.0] - 2026-05-24

AI instruction auto-injection, hooks adapter, activation funnel, comparison docs.

### Added
- **AI instruction auto-injection**: `engram setup` now injects instruction snippets into each tool's native config file (`CLAUDE.md`, `.cursorrules`/`.mdc`, `AGENTS.md`) so AI proactively calls Engram without relying solely on MCP server instructions
- **Claude Code Stop hook auto-registration**: `engram setup` registers the session auto-save hook in Claude Code's `settings.json`; `engram doctor --fix` can repair it
- **Stop hook enhanced**: `auto_save_on_stop.py` now calls `wrap_up_session()` for substantial sessions (10+ messages), extracting lessons/decisions/playbook drafts into staging
- `engram doctor` checks Claude Code Stop hook registration status
- `_inject_instruction_snippet()` / `_remove_instruction_snippet()` — programmatic injection with marker-based idempotent updates
- Setup problem Issue template (`.github/ISSUE_TEMPLATE/setup_problem.md`) for activation funnel feedback
- Issue template chooser config (`.github/ISSUE_TEMPLATE/config.yml`) with Discussions and Security links
- `setup_report.jsonl` — local setup result tracking for activation funnel analysis
- 20 new tests: instruction injection (10), setup report (6), hook registration (4)
- `docs/comparison.md` rewritten with 3-category competitive positioning

### Changed
- MCP server instructions rewritten: structured "WHEN TO CALL" format with 5 explicit trigger points
- README tagline updated: "AI can suggest memories. You decide what becomes true."
- `docs/comparison.md` rewritten with 3-category structure (agent memory / project memory / personal identity)
- `engram doctor` now calls `_configure_utf8_stdio()` to fix Chinese display on Windows GBK consoles
- Doctor section headers use ASCII-safe characters instead of box-drawing Unicode

## [3.28.1] - 2026-05-24

Auto project snapshots and mid-session checkpoints.

### Added
- Auto project snapshot on MCP server exit — collects version, module count, test count, MCP tool count
- `_collect_project_info()` helper for filesystem-based project metrics
- Stop Hook (`auto_save_on_stop.py`) also updates project snapshots

### Fixed
- Test isolation: `isolated_engram` fixture now resets `_session` to prevent atexit data leak to real `~/.engram/`

## [3.28.0] - 2026-05-23

Session auto-tracking and execution plan fix.

### Added
- MCP server session auto-tracking via `_SessionTracker` — records all tool calls during session
- `atexit` auto-save: persists session context on MCP server shutdown (tool list, call count, duration)
- Claude Code Stop Hook script (`scripts/auto_save_on_stop.py`) — saves session metadata when conversation ends
- Three-layer session protection: AI manual save (high quality) → MCP atexit (medium) → Stop Hook (basic)

### Fixed
- `prepare_playbook_execution` now auto-saves execution plan in core layer (previously only saved at MCP layer, causing data loss when called via Python API)
- Removed redundant `save_execution_plan` call from MCP layer (now handled by core)

## [3.27.1] - 2026-05-23

### Fixed
- Telemetry opt-in now part of normal setup wizard flow, not hidden behind `--advanced`
- Identity card content quality: limit domains, filter config directives, clean XML artifacts

## [3.27.0] - 2026-05-23

Execution tracking, stats i18n, and steps format compatibility.

### Added
- Playbook execution tracking: `prepare_playbook_execution` → `update_execution_step` → `get_execution_status`
- i18n module with `t(zh, en)` for bilingual output in stats

### Fixed
- Handle string-format steps in playbook parameter extraction, merge, and execution

## [3.26.0] - 2026-05-23

Playbook lifecycle, bilingual UX, knowledge intelligence.

### Added
- Playbook auto-extraction improvements
- Tools registry as Tier-1 knowledge type

## [3.25.0] - 2026-05-23

### Changed
- Playbook auto-extraction P0 improvements
- Bumped MCP Registry server.json version

## [3.24.0] - 2026-05-23

Phase 2 remote telemetry with Cloudflare Worker dashboard.

### Added
- Opt-in remote anonymous usage statistics via Cloudflare Worker + D1
- Visual telemetry dashboard (Chinese, password-protected) with PyPI download stats
- Periodic telemetry flush (every 10 tool calls) to prevent data loss on exit
- `atexit` handler as fallback flush on MCP server shutdown
- `force` parameter on `ToolCallTracker.flush()` to bypass daily rate limit
- Remote consent: `engram telemetry remote on/off/status` CLI commands
- 18 new telemetry tests (remote config, sender, payload fields)

### Changed
- `wrap_up_session` now force-flushes telemetry (previously skipped if already flushed today)
- Telemetry payload includes `os_platform`, `python_version`, `tools_tier` fields

## [3.23.0] - 2026-05-23

New knowledge type: **Playbook** — structured operational procedures stored as individual files for future sharing.

### Added
- Playbook knowledge type: multi-step operational procedures with trigger keywords
- Independent file storage (`~/.engram/playbooks/<id>.json`) with lightweight index
- Trigger-based retrieval: keyword anchors for instant recall (e.g., search "发布 registry" to find publish workflow)
- MCP tools: `add_playbook` (Tier-1), `get_playbooks`, `get_playbook`
- `search_knowledge` extended with `scope="playbooks"` support
- Trigger exact-match scoring bonus (weight 5.0 per hit) for high-precision retrieval
- Playbook support in `export_all` / `import_all` for backup and migration
- Playbook support in `update_knowledge` / `archive_knowledge` / `_find_item_by_id`
- Playbook tier promotion in `evaluate_tiers`
- 15 new tests covering full playbook lifecycle

### Changed
- `FIELD_WEIGHTS` extended with `triggers` (4.0) and `description` (2.0)
- `_score_item` now handles list-type fields (backward compatible)
- `_TERM_ALIASES` expanded with playbook/publish vocabulary

## [3.22.2] - 2026-05-23

Search discovery and conversion optimization release.

### Changed
- README rewritten with pain-point language for GEO/SEO/AIEO search discovery
- Per-client config blocks added (Claude Code, Cursor, Codex, Claude Desktop, Windsurf)
- FAQ rewritten with search-optimized Q&A for AI citation
- Chinese README synced with English version
- pyproject.toml description and keywords updated for search discovery
- MCP Registry description updated to "persistent memory" framing

## [3.22.1] - 2026-05-23

MCP Registry distribution release.

### Added
- Official MCP Registry `server.json` (`.mcp/server.json`)
- `mcp-name` tag in README for PyPI ownership verification
- CODE_OF_CONDUCT.md (Contributor Covenant v2.0)

### Changed
- Smithery listing published and set to public

## [3.22.0] - 2026-05-23

Doctor upgrade and onboarding polish release.

### Added
- **`engram doctor` functional checks**: After config health scan, doctor now verifies core library import, Engram initialization, identity profile, quick_context.md, and MCP tool registration
- **Setup post-completion verification guide**: Clear next-step instructions after setup finishes

### Changed
- CI workflows opt into Node.js 24 (`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`), eliminating GitHub deprecation warnings
- Shared instructions cleaned up: removed 30 lines of stale version history, updated Tier-1 tool list to 13 tools

### Fixed
- CHANGELOG v3.21.0 tool count corrected (was 43→46, now 45→48)

## [3.21.0] - 2026-05-23

Agent context auto-save release — recover lost AI conversations.

### Added
- **Agent context auto-save**: Office-style autosave for AI session context. Silently records work state at key checkpoints (task start, milestone, direction change); recoverable on demand after tool restart or session disconnect
- **`save_agent_context` MCP tool**: Save or append context checkpoints per tool, with session ID for multi-checkpoint sessions
- **`get_recent_context` MCP tool**: Retrieve the most recent session context after context loss (tool restart, session disconnect)
- **`list_agent_sessions` MCP tool**: Browse available session records across all tools (metadata only)
- **`ContextStoreMixin`** (contexts.py): New mixin with per-tool session file storage in `~/.engram/contexts/{tool}/`
- Storage: append-only markdown files, never auto-expire or auto-delete
- 14 new tests for context save, append, recovery, listing, and tool isolation
- All 3 context tools added to Tier-1 (always available)

### Changed
- MCP Tier-1 tools increased: 10 → 13 (added save_agent_context, get_recent_context, list_agent_sessions)
- MCP tool count increased: 45 → 48
- Directory structure: `contexts/` added to `~/.engram/` on init

## [3.20.0] - 2026-05-23

Knowledge health scoring and smart deduplication release.

### Added
- **Knowledge health score**: `get_knowledge_overview(section="health")` now returns a 0–100 composite `health_score` with four-dimension breakdown: freshness (% reviewed within 30 days), quality (verified vs staging ratio), coverage (domain diversity via Shannon entropy), cleanliness (absence of duplicates/archive candidates)
- **`suggest_merges` MCP tool**: Full-knowledge-base scan for near-duplicate items above a similarity threshold (default 0.45). Returns actionable merge commands — each suggestion includes primary/secondary IDs, summaries, similarity score, and a ready-to-call `merge_knowledge()` command
- Tests for health scoring dimensions and suggest_merges functionality

### Changed
- README updated to describe health score dimensions and `suggest_merges` tool
- MCP tool count increased: 19 read + 17 write + 1 web + 4 import/export + 2 workflow = 43 tools

## [3.19.0] - 2026-05-23

Cold-start optimization release — solving the "installed but never used" gap.

### Added
- **Environment auto-probing**: `engram setup` now detects name, email (from git config), tech stack (from project files), language preference (from commit history), and commit style automatically
- **Seed knowledge templates**: Setup injects best-practice lessons based on detected tech stack (Python, TypeScript, Go, Rust, Java + universal), marked as `staging` tier
- **Guided empty-state response**: `get_user_context` on empty Engram now returns a 5-step AI onboarding guide instead of a bare "no context" message
- **Auto-refresh `quick_context.md`** at end of setup wizard — all AI tools can read it immediately
- **Distribution monitoring script** (`scripts/metrics.py`): tracks GitHub traffic, PyPI downloads, referral sources, and local usage signals
- 4 new tests for cold-start functions (probe, seed templates, dedup, empty dir)

### Changed
- **Supported tools table expanded to 13 entries** in README (was 6): 4 verified + 7 expected-to-work + OpenClaw + ChatGPT fallback
- **"Status" column renamed to "Confidence"** for clearer messaging
- Setup menu options now pre-ordered based on probed environment signals

## [3.18.0] - 2026-05-23

Repo rename, security hardening, and doctor upgrade release.

### Changed
- **GitHub repo renamed** `Patdolitse/engram` → `Patdolitse/piia-engram` (avoids collision with Gentleman-Programming/engram 3.7k stars)
- **Module rename completed** across all files: `engram_core` → `piia_engram` (backward-compat shim retained with `DeprecationWarning`)
- **`engram doctor` expanded to 11 AI tools** (was 6): Claude Code, Cursor, Claude Desktop, Codex + 7 community-supported (Windsurf, Copilot, Cline, Roo Code, Amazon Q, Augment, Zed)
- **Doctor output now shows verified vs community tiers** — clear labeling of team-tested vs untested tools
- **Social preview images updated** with piia-engram branding

### Security
- **Removed 20 tracked result/data files** from git (benchmark outputs, evaluation logs with LLM payloads)
- **Scrubbed 4 hardcoded personal paths** (Windows username) from reports and docs
- **.gitignore hardened** — added `.env.*`, `*.pem`, `*.key`, `credentials*`, `secrets*`, broader evaluation result patterns
- **CI workflows locked down** — explicit `permissions: contents: read` on both ci.yml and publish.yml

### Tests
- **674 passed**, 0 failed
- Post-rename verification: 10/10 checks PASS (old imports, URLs, package metadata, CLI entry points, doctor coverage, backward compat)

## [3.17.0] - 2026-05-23

Quality & reliability release: 657 tests at 96% coverage (all modules ≥90%), cross-platform CI fixes, and Round 10 retrieval quality benchmark achieving 43/43 PASS.

### Added
- **Cold-start setup streamlining** — simplified first-run experience with guided setup flow
- **Round 10 retrieval/injection quality benchmark** — 7-dimension, 43-case test suite; all 43 PASS under an external LLM judge

### Fixed
- **CI stability** — safe tilde expansion (no `os.path.expanduser` on `~` in path literals), test auth hardening, job matrix reduced 12→6 for faster feedback
- **Cross-platform path parsing** — `_sanitize_project()` uses `PureWindowsPath` so Windows paths parse correctly on all platforms

### Tests
- **657 passed** (up from 490 in v3.16.0; +167 new)
- Total coverage: **83% → 96%** (+13pp); all modules ≥90%
- Key module coverage: storage 100%, core 95%, reconcile 98%, mcp_server 99%, setup_wizard 93%, reports_identity 100%, stats 100%

### Benchmarks
- Round 10: retrieval quality 43/43 PASS across 7 dimensions (relevance, completeness, noise, format, latency, edge cases, injection safety)

## [3.16.0] - 2026-05-22

Code quality release: split the last monolithic module, brought mcp_server coverage to production-grade, and ran third-party milestone evaluation.

### Changed
- **`reports.py` split into 5 modules** (1103 lines → max 520 per file):
  - `reports.py` (22 lines) — thin hub composing 4 sub-mixins
  - `reports_rarity.py` (85 lines) — `RarityMixin`: quality classification + `RARITY_TIERS`
  - `reports_review.py` (520 lines) — `ReviewMixin`: HTML review page, promote/archive
  - `reports_identity.py` (97 lines) — `IdentityCardMixin`: Markdown identity card export
  - `reports_analytics.py` (310 lines) — `AnalyticsMixin`: health reports, stale detection, digest, stats
- Public API unchanged — `from piia_engram.reports import ReportsMixin` still works
- `architecture.md` updated to v3.16.0 with new module map and two-level mixin diagram
- README "By the numbers" updated to v3.16.0 stats (490 tests, 83% coverage)
- CONTRIBUTING test baselines updated: 490+ tests, 83%+ coverage

### Tests
- **490 passed** (up from 437 in v3.15.1; +53 new)
- New `tests/test_mcp_coverage.py` (53 tests) — covers write tools, search, review/merge, identity update, import/export, workflow shortcuts, and all 7 MCP resources
- `mcp_server.py` coverage: **58% → 86%** (+28pp)
- Total coverage: **78% → 83%** (+5pp)

### Evaluated
- External 3-pass milestone evaluation: architecture 8.0 (+0.5), security 8.0 (+0.5), overall 7.53
- 5/5 v3.14.3 suggestions verified as fixed
- Key feedback: architecture.md and CONTRIBUTING.md were lagging (now fixed)

## [3.15.1] - 2026-05-22

### Fixed
- **GBK console safety**: Identity card preview in setup wizard now uses `_safe_print()` to avoid `UnicodeEncodeError` on Windows Chinese consoles (strips unsupported emoji, preserves CJK text)

### Improved
- **README**: Added PyPI download badge, "30 seconds" quick start framing, setup step 5-6 (privacy + identity card preview), updated "By the numbers" to v3.15.0 stats (437 tests), added CLI commands reference section
- **README.zh-CN.md**: Synced all English README improvements
- **CONTRIBUTING baselines**: 394+ → 437+ tests

## [3.15.0] - 2026-05-22

Privacy-focused feature release: opt-in anonymous usage statistics, reconcile authorization gate, and setup wizard privacy step. Designed through cross-AI consultation (4 independent AI evaluations synthesized).

### Added
- **Anonymous usage statistics (Phase 1: local log only)** — `telemetry.py` module
  - Off by default; opt-in during `engram setup` Step 5 or via `engram telemetry on`
  - Collects only 4 fields: tool call distribution (success/error counts), knowledge entry totals, engram version, daily anonymous ID
  - Daily ID via `HMAC(local_uuid, date)` — cannot link across days
  - Payload validator rejects strings >200 chars or with natural language patterns (no content leakage possible)
  - All data stored locally in `~/.engram/telemetry.log` (JSONL, human-readable)
  - **No network requests** — Phase 2 gated by 30 days + 5 users sharing logs
  - CLI: `engram telemetry status|preview|on|off`
  - Env override: `ENGRAM_TELEMETRY=0|1`
- **Reconcile authorization gate** — `reconcile.py`
  - `reconcile_memories()` and `reconcile_ai_configs()` now require explicit authorization
  - Controlled via `ENGRAM_RECONCILE` env var or `telemetry_config.json` preference
  - Default: authorized (backward-compatible for existing users)
  - New users explicitly choose during setup
- **Setup wizard Step 5: Privacy Preferences**
  - [1] Cross-tool memory sync authorization (default: Yes)
  - [2] Anonymous usage statistics (default: **No**)
  - Numeric selection UI (no free-text input)
- **ToolCallTracker wired into MCP server** — 10 Tier-1 tools instrumented with success/error tracking; auto-flush during `wrap_up_session`
- Internal telemetry planning notes (phased rollout + decision-gate criteria)

### Changed
- `README.md` / `README.zh-CN.md`: updated "0 network calls" claim to reflect opt-in statistics; FAQ rewritten
- `SECURITY.md`: updated from "no telemetry" to describe opt-in anonymous statistics with preview/off instructions
- `docs/comparison.md`: corrected "no opt-out telemetry" claim to describe opt-in model

### Tests
- **424 passed** (up from 394 in v3.14.4; +30 new)
- New `tests/test_telemetry.py` (30 tests): config persistence, env overrides, daily ID properties, payload validation (length/language/nested), build_payload gating, local log append, preview, ToolCallTracker lifecycle, opt-out safety
- CONTRIBUTING baseline raised: 394+ → **424+ tests**

## [3.14.4] - 2026-05-22

Patch driven by the v3.14.3 milestone evaluation. Two high-severity findings addressed; full regression context captured internally.

### Security
- **`crypto.py`: `DecryptionError` + `strict=True` mode**. The default `decrypt()` still returns the original ciphertext on failure (backward-compatible warning + passthrough), but new callers can now opt into `decrypt(value, strict=True)` / `decrypt_fields(..., strict=True)` to raise `DecryptionError` instead. Uses `raise from None` to avoid leaking timing-oracle info about which stage failed (b64 / key derivation / AEAD tag).
- The default behavior preserves backward compatibility for any caller that may already depend on it, but the docstring now explicitly warns: "callers that don't validate the prefix after this call may treat ciphertext as plaintext — prefer strict=True in new code."

### Fixed
- **README MCP tool count inconsistency**. README's "By the numbers" section claimed 45 tools while elsewhere said 43; actual count is **43** (`grep -c '^@mcp.tool' src/piia_engram/mcp_server.py`). All documents now consistent at 43:
  - `README.md` and `README.zh-CN.md` quantitative sections + comparison tables
  - `docs/comparison.md`
  - `docs/architecture.md` (3 references)
  - internal coverage + evaluation notes (with explicit erratum note)

### Tests
- **394 passed** (up from 386 in v3.14.2; v3.14.3 was docs-only)
- New `TestDecryptionStrict` class in `tests/test_crypto.py` (8 tests): wrong-key raises, bad payload raises, truncated payload raises, unprefixed passthrough in strict mode, happy-path round trip, default mode unchanged, `__cause__` is None (no timing leak), `decrypt_fields(strict=True)` raises without mutating input dict
- CONTRIBUTING baseline raised: 386+ → **394+ tests**

### Docs
- Milestone evaluation closure for v3.13.2 → v3.14.3 (external multi-pass review)
  - Architecture score: 5.4 → 7.50 (+2.10, biggest movement)
  - Overall: 6.9 → 7.90 (+1.00)
  - Self-assessment calibration bias narrowed from +1.7 (security blind spot) to −0.5 (now slightly conservative)
  - 15/21 v3.13.2 issues marked `fixed`, 5 `partial`, 1 `unverified`, 0 `regression`
  - Roadmap items extracted for v3.15.0: split reports.py (1103 lines), explicit Mixin dependencies, add SSE integration tests, mock LLM extraction

## [3.14.3] - 2026-05-22

### Docs
- New `docs/architecture.md` — 30-second mental model diagram, complete module map (post v3.14.1 refactor), three canonical data flows (cold start / capture / review), storage layout, MCP surface, conventions, "where to add things" matrix
- New `docs/comparison.md` — factual side-by-side with Letta, Mem0, Cline memories, Claude Code memory; explicit "choose someone else when..." section; identity-layer vs memory-layer architectural framing
- README upgrade: comparison table expanded to 5 competitors with clearer dimensions (purpose, locality, encryption, knowledge tiers, conflict detection); new "By the numbers" section with v3.14.2 quantitative claims (45 MCP tools, 386 tests, 78% coverage, PBKDF2 600k, < 100ms cold start, 0 network calls in core); both English and Chinese
- README FAQ: explanation of the `piia-engram` PyPI name vs the "Engram" product brand (English + Chinese)

### Tests
- Unchanged — 386 passed (no code changes in this release)

## [3.14.2] - 2026-05-22

### Tests
- **386 passed** (up from 329 in v3.14.1, +57 new)
- New `tests/test_mcp_tools.py` (37 tests) — direct coverage of MCP tool wrappers: identity reads, knowledge read/write, search, context, error catching, Tier-1 filtering, path validation
- New `tests/test_review_page_xss.py` (10 tests) — verifies `_esc` escaping prevents HTML / attribute injection in the review HTML page (lesson summary, decision title, domain label, profile fields, source_tool, ampersand, CJK passthrough)
- Expanded `tests/test_crypto.py` (+10 tests, now 19) — v1↔v2 mixed-field decryption, v1→v2 re-encryption upgrade, Unicode (emoji/CJK/RTL/combining chars), bad base64 / truncated payload / unknown prefix passthrough, non-string field skip, iteration-count pinning, default-prefix-is-v2 contract

### Security
- **Path validation**: new `_validate_path` helper in `mcp_server.py` rejects NUL bytes in user-supplied paths. Applied to `import_engram`, `export_engram`, `save_project_snapshot`. Engram remains local-first (not a sandbox), but null-byte handling now matches OWASP guidance for paths crossing trust boundaries.

### Docs
- Published the first test-coverage baseline and documented remaining gaps internally
- New `.coveragerc` — pins source root and exclude rules so future runs are reproducible
- CONTRIBUTING baseline raised: 329+ tests → 386+ tests, 78%+ coverage required

## [3.14.1] - 2026-05-22

### Refactor
- **`core.py` split**: 4277 → 1083 lines (-74.7%), extracted into 7 modules via mixin pattern. Public API unchanged — all imports from `piia_engram.core` continue to work via re-exports.
  - `storage.py` (224) — constants + I/O primitives (`_read_json`, `_write_json`, `_engram_root`, etc.)
  - `retrieval.py` (639) — `RetrievalMixin`: search, scoring, tokenization, batch ops, conflict detection
  - `context.py` (688) — `ContextMixin`: `generate_context`, ingestion + standalone `extract_knowledge` / `ingest_extraction`
  - `reconcile.py` (425) — `ReconcileMixin`: external AI memory + config file sync
  - `reports.py` (1103) — `ReportsMixin`: review HTML, identity card, health, stats, knowledge digest
  - `compat.py` (318) — OpenClaw / OCA migration functions
  - `core.py` (1083) — `Engram(RetrievalMixin, ContextMixin, ReconcileMixin, ReportsMixin)` facade

### Security
- **PBKDF2 iterations: 100,000 → 600,000** (OWASP 2023+ recommended floor). New encryptions use `enc:v2:` prefix.
- **Backward compatibility**: `enc:v1:` ciphertexts (legacy 100k iterations) continue to decrypt. Old data is re-encrypted to v2 on next write of that field.

### Fixed
- **Schema version comparison**: `_migrate_v1_to_v2` used lexicographic string comparison (`"10.0" < "2.0"`). Now parses to tuples via `_parse_schema_version`.

### Changed
- **`print(file=sys.stderr)` → `logging`** across all piia_engram modules (audit, compat, context, crypto, mcp_server, setup_wizard, stats, storage). Each module gets `logger = logging.getLogger(__name__)`. Library output is now respectful of host application's logging config.

### Tests
- **329 passed** (up from 328 in v3.14.0)
- New: `test_v1_ciphertext_still_decrypts` — verifies forward decryption of legacy v1 ciphertexts after the PBKDF2 upgrade

## [3.14.0] - 2026-05-22

### Breaking
- **Encryption fail-fast**: `EncryptionEngine` now raises `RuntimeError` when `ENGRAM_SECRET` is set but `cryptography` package is missing. Previously it silently disabled encryption, risking plaintext storage.

### Security
- **Timing attack fix**: SSE token comparison changed from `==` to `secrets.compare_digest`
- **SECURITY.md corrected**: "Fernet" → "AES-256-GCM" to match actual implementation
- **SSE hardening**: `0.0.0.0` bind emits HTTPS warning; new `ENGRAM_CORS_ORIGINS` env var for cross-origin restriction
- **sys import fix**: `core.py` was missing top-level `import sys` — error handlers would have raised `NameError` instead of logging

### Fixed
- `_apply_tool_tier` docstring corrected (core is the default, not all)
- Removed redundant `import sys as _sys` in mcp_server.py startup sync block
- README: "100% local" → "local-first" (honest about `read_web_content` network path)
- README: "automatically" → "one tool call away" (knowledge inheritance requires explicit call)
- README: stale knowledge days 90 → 30 (matches `STALE_KNOWLEDGE_DAYS` constant)
- README FAQ: installation path unified to `pip install piia-engram && engram setup`
- README: added `ENGRAM_TOOLS=all` config example with JSON snippet
- README: added `ENGRAM_CORS_ORIGINS` to SSE security notes
- All fixes applied to both English and Chinese README

### Tests
- 328 passed (up from 327 in v3.13.2)
- New: `test_secret_without_crypto_raises` — verifies fail-fast on missing cryptography

### Docs
- v3.13.2 milestone evaluation closure (captured internally)

## [3.13.2] - 2026-05-22

### Tests
- **327 passed** (up from 281 in v3.13.1) — 46 new tests covering critical algorithm gaps
- New: 7 `_score_item` tests (field weights, access bonus, multi-term coverage, CJK queries)
- New: 4 `search_knowledge` tests (ranking, CJK search, alias expansion, threshold filtering)
- New: 4 `_detect_decision_conflicts` tests (same/different domains, overlapping domains)
- New: 4 `_detect_lesson_conflicts` tests (negation/affirmation markers, CJK, domain separation)
- New: 7 `generate_context` tests (empty profile, token budget, conflict section, section inclusion)
- New: 8 `ingest_notes` tests (decision/lesson triggers, short line skipping, dedup, CJK triggers)
- New: 4 `_infer_domain` tests (single/multi match, fallback behavior)
- New: 4 `_bigram_similarity` tests (identical, empty, partial, completely different)
- New: 2 `evaluate_tiers` + 1 eviction test (staging-first eviction policy)

## [3.13.1] - 2026-05-22

### Fixed
- **CJK line classification**: Chinese lines (e.g. "我是全栈开发者") were incorrectly skipped during rule file import because the minimum length threshold (8 chars) didn't account for CJK character density. Now uses 4-char threshold for CJK text.
- **Rule directory globbing**: `reconcile_ai_configs` now correctly imports rule files from directory-style configs (e.g. `~/.cursor/rules/*.mdc`) instead of silently skipping them.
- **Stale knowledge display**: 3 remaining hardcoded "30 天" strings now use the `STALE_KNOWLEDGE_DAYS` constant consistently.

### Tests
- 281 passed (up from 258 in v3.13.0)
- New: 22 parametrized `_classify_line` tests covering CJK, user identity, project rules, skip, and ambiguous cases
- New: 2 `_scan_rule_files` tests (project detection + tiny file skip)
- New: `reconcile_ai_configs` directory globbing test

## [3.13.0] - 2026-05-22

### Breaking
- **Default tool set changed to Tier-1 Core (10 tools)**. Previously all 43 tools were loaded by default. Set `ENGRAM_TOOLS=all` in your MCP config `env` to restore the full set. `engram doctor` will show an info notice if your config doesn't specify `ENGRAM_TOOLS`.

### Changed
- **Tier-1 tool set revised**: added `wrap_up_session` (session lifecycle) and `update_identity` (profile updates); removed `extract_session_insights` and `export_engram` (moved to Tier-2)
- **Quickstart simplified**: `pip install piia-engram && engram setup` is the complete flow; manual MCP JSON config moved to collapsible section
- README tool tables reorganized: Tier-1 as main table, Tier-2 in collapsible `<details>` section

### Improved
- `engram doctor` shows info notice when configs lack `ENGRAM_TOOLS` setting
- Extracted `MAX_KNOWLEDGE_ENTRIES` constant (was hardcoded `200` in 11 places)

## [3.12.3] - 2026-05-22

### Fixed
- **JSON corruption logging**: `_read_json()` now warns to stderr on parse failure instead of silently returning empty data
- Last 3 silent exception blocks (stats.py, crypto.py) now log to stderr — **zero silent exceptions** across all source files

### Improved
- Extracted `SEARCH_RELEVANCE_THRESHOLD`, `STALE_KNOWLEDGE_DAYS`, and `MAX_KNOWLEDGE_ENTRIES` as module constants (was hardcoded in 17 places total)
- CI workflow: added pip caching for faster runs
- README tool tables now list all 43 tools (was missing `apply_review` and `request_outline_review`)
- Replaced `__import__('sys')` hacks with proper imports

### Tests
- 258 passed (up from 242 in v3.12.2)
- New: 12 tests for staging/review/rarity workflow (classify_rarity, evaluate_tiers, apply_review, promote_knowledge)
- New: 3 tests for export_all/import_all error handling

## [3.12.2] - 2026-05-22

### Added
- **Search alias expansion**: 16 new CJK/English alias pairs (js→javascript, db→数据库, 部署→deploy, 前端→frontend, etc.)
- **CJK trigram alias lookup**: 3-character Chinese terms (e.g. "数据库") now correctly expand to English aliases during search

### Improved
- Removed redundant `test.yml` workflow — `ci.yml` already covers 3 OS × 4 Python versions

### Tests
- 242 passed (up from 224 in v3.12.1)
- New: 16 tests for `export_to_openclaw`, `import_from_openclaw`, `migrate_from_oca_memory`, `increment_domain_usage`
- New: 2 alias expansion tests (abbreviation + cross-language search)

## [3.12.1] - 2026-05-22

### Fixed
- **Search ranking**: multi-term queries now correctly prioritize items matching more query terms via coverage bonus (D6-RANK-01 benchmark fix)

### Improved
- SPDX license format in pyproject.toml (silences setuptools deprecation warnings)
- pytest `pythonpath` config replaces `sys.path.insert` hack in all test files

### Tests
- Round 10 benchmark: 43/43 (100%), up from 40/43

## [3.12.0] - 2026-05-22

### Improved
- Cold-start empty-state guidance: actionable next steps (update_identity / engram setup) instead of bare warning
- All silent `except Exception: pass` blocks now log to stderr for debugging
- Python 3.13 added to CI test matrix and PyPI classifiers

### Tests
- 212 passed (up from 193 in v3.11.2)
- New: `test_stats.py` — 11 tests covering stats module (API mocking)
- New: 3 `engram doctor` tests (healthy config, legacy name, invalid path)
- New: 5 edge-case tests (token budget, CJK conflict, config size limit)

### Docs
- Bilingual issue templates (bug report + feature request)
- Bilingual PR template with security checklist
- Consolidated 9 individual RELEASE_NOTES files into CHANGELOG.md
- Bilingual docstrings for review tools

## [3.11.2] - 2026-05-22

### Security
- `export_identity_card()` now respects `trust_boundaries.restricted_fields`
- `get_profile` MCP tool default changed to `safe=True`
- `engram://identity/profile` resource endpoint now returns safe (filtered) profile
- Field whitelist validation on `update_profile`, `update_preferences`, `update_trust_boundaries`, `update_quality_standards`
- `reconcile_memories` skips files > 10 KB; `reconcile_ai_configs` skips files > 50 KB
- Audit log written after every reconcile run

### Tests
- 193 passed (7 new security tests)

## [3.11.1] - 2026-05-22

### Changed
- Version bump for PyPI (3.11.0 filename already occupied)

## [3.11.0] - 2026-05-22

### Added
- **Knowledge conflict detection** — `generate_context()` warns about contradictory decisions (same domain + similar question + different choice) and contradictory lessons (sentiment asymmetry)
- **Token budget control** — `generate_context(max_tokens=N)` drops low-priority sections first; 11 sections ranked by priority
- **Staging backlog reminder** — `wrap_up_session` and `generate_context()` notify about unreviewed auto-imported items
- **Simplified rarity system** — 3+1 tiers (legendary/epic/rare + staging gray); staging-first eviction on truncation
- **Auto-sync** — `reconcile_memories()` + `reconcile_ai_configs()` import from Claude Code memory, CLAUDE.md, .cursorrules, etc.
- **Interactive review page** — browser-based knowledge review with domain grouping, rarity badges, retain/archive toggles
- SECURITY.md — bilingual vulnerability reporting policy
- NOTICE — Apache 2.0 attribution file

### Fixed
- **P0**: Truncation now evicts staging items first (never drops verified knowledge)
- **P1**: Staging auto-promote removed; promotion only via `evaluate_tiers()`
- **P1**: XSS — all user-controlled HTML fields escaped via `_esc()`, including domain group titles
- **P1**: Archive false success — `apply_review` checks `result.get("error")` properly
- **P1**: Frontmatter parsing — `---` in content body no longer toggles frontmatter mode
- **P1**: Nested project paths — greedy recursive `_decode_claude_project_name()`
- **P2**: Review page `access_count` pollution — `generate_review_page()` uses `_update_access=False`

### Tests
- 186 passed; Round 10 benchmark 43/43 across 7 dimensions

## [3.10.1] - 2026-05-19

### Fixed
- Context quality hotfix: lesson allocation, domain sanitization, empty profile guidance

## [3.10.0] - 2026-05-18

### Added
- Bilingual MCP tool descriptions (Chinese + English)
- Bilingual setup wizard with numbered menu selection
- SSE transport mode for remote deployment
- Token-based authentication middleware

### Tests
- Round 9 lifecycle verification: T1 10/10, T2 20/20

## [3.9.0] - 2026-05-15

### Added
- 10-minute aha onboarding with smart scan + split import
- Auto-detect existing AI tool configs during setup

## [3.8.1] - 2026-05-13

### Fixed
- AI context injection no longer pollutes staleness detection

## [3.8.0] - 2026-05-12

### Added
- Knowledge lifecycle tools: `review_knowledge`, `get_stale_knowledge`
- Domain parameter on `get_decisions` and `add_decision`
- Multi-label domain support (comma-separated)

### Tests
- Round 7: domain softening T1 15/15, T2 19/20
- Round 8: decisions domain T1 8/8, T2 20/20

## [3.7.0] - 2026-05-09

### Added
- Optimized tool descriptions and workflow shortcuts (39 tools)
- Round 6 full coverage benchmark: 39 tools, 88 scenarios, 98.9% accuracy

## [3.6.0] - 2026-05-07

### Fixed
- Include decisions in cold-start context and identity card

## [3.5.1] - 2026-05-05

### Added
- Onboarding seed knowledge, MCP tool tiering, narrow ICP

## [3.5.0] - 2026-05-03

### Added
- Sharpened positioning as AI identity layer

## [3.4.0] - 2026-04-30

### Added
- Personal knowledge card (PKC) export and identity card improvements

## [3.3.0] - 2026-04-27

### Added
- Audit logging for all read/write operations

## [3.2.0] - 2026-04-24

### Added
- Encryption at rest (AES-256-GCM) for sensitive profile fields

## [3.1.0] - 2026-04-21

### Added
- Trust boundaries and restricted fields

## [3.0.0] - 2026-04-18

### Changed
- Major architecture rewrite: MCP-native, modular core engine
- Knowledge stored as structured JSON (lessons + decisions)

## [2.9.0] - 2026-04-15

### Added
- Weighted multi-term search + `find_similar_knowledge`

## [2.6.0] - 2026-04-12

### Added
- Weighted search scoring

## [2.5.0] - 2026-04-09

### Added
- Bulk knowledge import + note ingestion

## [2.4.0] - 2026-04-06

### Added
- Bidirectional knowledge linking

## [2.3.0] - 2026-04-03

### Added
- Knowledge quality: aging, digest, report export

## [2.2.0] - 2026-03-31

### Added
- Atomic writes, file locking, restricted_fields enforcement

## [2.1.0] - 2026-03-28

### Added
- Knowledge search, lifecycle management, health report

## [2.0.0] - 2026-03-25

### Added
- Initial release: AI identity layer with profile, work style, lessons, decisions
- MCP server with stdio transport
- Apache 2.0 license
