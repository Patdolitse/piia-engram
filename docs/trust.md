# Trust model

piia-engram is a local-first personal AI identity layer. It helps your AI coding tools share the same approved context about you: preferences, standards, lessons, decisions, playbooks, and project snapshots.

The trust model is simple:

1. Your core identity and knowledge stay on your machine.
2. AI tools can suggest new knowledge; suggested items stay visible for review, and current lessons/decisions may also follow the existing access-based promotion path after repeated use.
3. Reports meant for sharing should contain status and counts, not private content or local paths.
4. Engram reduces memory and MCP risk by being transparent and local-first, but it is not a sandbox or a secrets manager.

## What stays local

By default, Engram stores its core data under `~/.engram/`:

| Data | Typical location | Purpose |
|---|---|---|
| Identity and preferences | `~/.engram/identity/` | Who you are and how you work |
| Lessons and decisions | `~/.engram/knowledge/` | What you have learned and why you chose things |
| Playbooks | `~/.engram/playbooks/` | Reusable multi-step workflows |
| Project snapshots | `~/.engram/projects/` | Project-specific context |
| Recent contexts and daily logs | `~/.engram/contexts/`, `~/.engram/daily/` | Cross-session continuity |
| Optional telemetry log | `~/.engram/telemetry.log` | Local opt-in usage counts; remote sending is a separate opt-in |

The files are plain JSON or Markdown unless you explicitly enable optional field-level encryption for supported sensitive fields.

## What never happens by default

Core identity and knowledge tools do not upload your memory to a hosted Engram service. There is no Engram cloud account, no required subscription, and no default cloud sync.

By default:

- No hosted account is required.
- Telemetry is off.
- Local telemetry, when enabled, writes a local log first; remote telemetry and weekly feedback reports require separate explicit opt-in.
- Knowledge content, prompts, AI responses, file paths, email addresses, and IP addresses are not collected by telemetry.
- High-risk AI-suggested knowledge (credentials, executable commands, permission or MCP-config changes) is staged for your review before becoming verified, and unsupervised background writeback is always staged. Low/medium-risk items are auto-verified unless you set `ENGRAM_APPROVAL=strict`, which stages every write. See the risk-gated workflow below.
- `engram setup` lists the external MCP client config files it would touch and asks for a one-keystroke confirm before writing; declining leaves every external config untouched. `engram setup --apply-external-config` skips the prompt for non-interactive/CI runs.

Optional features can have different behavior:

- `read_web_content` fetches a URL you explicitly provide through the configured reader path.
- Self-hosted HTTP transport is available for advanced deployments.
- `engram telemetry remote on` can send anonymous count-only telemetry to the configured endpoint after explicit consent.
- `engram telemetry feedback on` can send weekly anonymous feedback reports after explicit consent.

See [PRIVACY.md](../PRIVACY.md) for the full data-flow description.

## File-safety and upgrade boundaries

Engram separates its own data folder from external tool configuration, and treats external client config as a confirm-before-write surface.

What Engram writes to its own data folder:

- Files inside the selected Engram data folder, such as identity JSON, knowledge JSON, playbooks, local status reports, setup reports, and Engram-owned backups.
- Project instruction snippets only through explicit setup/doctor actions that are documented in the local diagnostic output.

How Engram writes external client config:

- `engram setup` lists the exact external MCP client config files it will touch and asks for a one-keystroke confirm before writing the connection. Declining leaves every external config untouched. `engram setup --apply-external-config` skips the prompt for non-interactive/CI runs.
- Every external write — whether from the interactive confirm, the `--apply-external-config` flag, or an approved repair path — goes through the central file-safety layer:
  - the previous external config is backed up under `<engram-root>/backups/file_safety/external/`;
  - a metadata-only `file_safety_ledger.jsonl` entry is appended under the Engram root;
  - ledger paths are redacted or hashed instead of storing raw external absolute paths;
  - existing custom `ENGRAM_DIR` values in legacy client configs are preserved unless you explicitly choose a new data folder.

What Engram does not write:

- User project documents outside the Engram data folder.
- Arbitrary files outside the selected Engram root.
- Raw external absolute paths into the ledger (they are redacted or hashed instead).

For existing users, the upgrade boundary stays conservative: startup migration only logs guidance inside the Engram root and leaves old external client configs byte-for-byte unchanged. To update those configs, run the setup or doctor repair command from a terminal you control (it confirms before writing, or uses the explicit flag).

## AI suggests; you review what matters

Engram is designed around a risk-gated staging-to-verified workflow.

AI tools may call functions such as `add_lesson`, `add_decision`, `add_playbook`, or `extract_session_insights`. New suggestions are classified by risk before they become active:

- **Low / medium risk** (most preferences, lessons, project rules) is auto-verified for next-session use, so the everyday path stays low-friction.
- **High risk** (credential values, executable commands, permission or MCP-config changes) is routed to staging for your review before it becomes active.
- Unsupervised background writeback paths force staging regardless of risk, and LLM-extracted suggestions cannot self-label themselves as verified.

You can review, edit, archive, or reject staged knowledge anytime via `review_staging`; playbook review remains explicit before trusted use. Cold-start `get_resume_brief` surfaces the pending-review count (including high-risk items) so nothing sensitive slips in silently.

This is different from agent-owned memory systems where the agent continuously rewrites its own long-term memory. Engram treats durable memory as a user-owned asset and keeps the sensitive writes behind your review.

Playbooks follow the same trust boundary. Engram normalizes Playbooks into a versioned structural contract and can track execution outcomes, but it does not silently execute workflows. Host AI tools receive Playbooks as passive references, walk through steps in their own runtime, and can report step status back so the result is visible as `pending`, `partial`, `succeeded`, or `failed`.

Playbooks may declare tool dependencies through `required_tools`, but Engram does not store resolved local tool paths in the Playbook. `playbook_execution(action="prepare")` resolves those dependencies against the local tools registry at runtime and returns `resolved_tools` only to the owner-gated caller; it does not store resolved local tool paths in execution-plan files.

Each knowledge entry can carry trust-mode metadata:

| Field | Meaning |
|---|---|
| `memory_state` | Canonical lifecycle state: `staging`, `verified`, `rejected`, or `deprecated` |
| `approval_status` | User-facing approval state derived from the memory state |
| `provenance` | Metadata such as `source_tool`, `entry_type`, `created_at`, `domain`, and `project` |
| `risk_level` / `risk_flags` | A conservative local signal for risky memory text, such as credentials, executable commands, MCP config, permissions, or external URLs |
| `approval_required` | True when the entry is staged or high-risk |

These fields are additive. Existing `tier` and `status` values remain supported for backward compatibility.

## Recovery and retention dry-runs

If a JSON knowledge file becomes unreadable, `engram recover-json lessons` reports recovery candidates without restoring anything automatically. The retention plan compares valid backups by metadata, counts overlap/primary-only/secondary-only IDs, and warns when an active merge would exceed the active knowledge cap.

The dry-run is intentionally content-free: it does not print lesson bodies, details, or raw IDs. Restoring the live `lessons.json` still requires an explicit user decision outside the dry-run report.

## Public vs private reports

Use different outputs for sharing and debugging.

Public or shareable outputs should contain:

- Engram version.
- Tool connection status.
- Counts of lessons, decisions, playbooks, and pending review items.
- Health status from `engram doctor`.
- Redacted storage labels such as `<engram-root>`.
- Whether telemetry is off or enabled.
- Metadata-only config integrity counts and short hashes, when useful.

Private diagnostic outputs may contain:

- Local paths.
- Tool configuration paths.
- Stack traces.
- Project names.
- Local usernames.
- Audit details.
- Session IDs or context file names.

`engram doctor` treats the config integrity report as local diagnostic metadata. It can include local paths and file hashes, but it does not include MCP config contents, Claude hook commands, instruction bodies, or project rule lines.

Do not publish private diagnostic outputs without reviewing and sanitizing them first. Release evidence, issue comments, screenshots, and feedback bundles should use public-style summaries unless the recipient explicitly needs private diagnostics.

## MCP security boundaries

MCP gives AI tools a way to call local capabilities. That is powerful, so Engram treats trust as a product feature: local files, minimal default tools, user-reviewed memory, a default-on local audit log, and documented limits.

Important boundaries:

- Any local process with filesystem access to `~/.engram/` may be able to read your Engram files.
- MCP caller identity is client-provided and should not be treated as cryptographic identity.
- `restricted_fields` and governance filters control what Engram returns through tools; they are not file-level ACLs.
- Optional encryption protects supported fields only when configured. It is not full-disk encryption.
- Engram is not a sandbox for untrusted MCP clients or tools.

Use operating-system permissions, disk encryption, and careful MCP client configuration for stronger isolation.

## What not to store

Engram is for personal AI context, not secret management.

Do not store:

- Passwords.
- API keys.
- OAuth tokens.
- Private keys.
- Customer PII.
- Regulated data that your policies do not allow in local AI tooling.
- Content you would not want any locally authorized AI tool to read.

If a lesson or decision requires sensitive context, store the non-sensitive reasoning and keep the secret itself in a proper secret manager.

## User controls

You can inspect and control your data:

- View and edit local JSON/Markdown files under `~/.engram/`.
- Export a portable identity card with `get_identity_card`.
- Review proposed knowledge before promoting it.
- Archive or update stale knowledge.
- Turn telemetry off with `engram telemetry off`.
- Inspect telemetry payloads with `engram telemetry preview`.
- Local audit logging is on by default; opt out with `ENGRAM_AUDIT=0`.
- Enable optional field encryption for supported fields with `piia-engram[secure]` and `ENGRAM_SECRET`.

## Known limitations

Engram is intentionally local-first and transparent, but it is not magic security infrastructure.

- Plain local files are easy to inspect and migrate, but they are also readable to local processes with the right permissions.
- Governance filters reduce disclosure through Engram tools; they do not prevent direct filesystem reads.
- Optional encryption is field-level, not whole-store encryption.
- Public reports must be designed and reviewed separately from private diagnostics.
- Cross-tool memory quality depends on what the AI suggests and what the user approves.

The goal is not to promise perfect safety. The goal is to make memory ownership, review, deletion, export, and boundaries visible enough that users can make informed decisions.
