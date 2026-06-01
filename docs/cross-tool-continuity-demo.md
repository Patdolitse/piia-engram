# Cross-tool continuity demo

This demo shows the core piia-engram promise without using a real Claude, Codex, Cursor, or Windsurf login:

> One local identity layer can be written by one AI coding tool and read by another.

The demo uses a synthetic Engram store with fake user data. It does not read or write your real `~/.engram/` directory unless you intentionally change the script.

## What it proves

Scenario:

1. A simulated Claude Code client records a lesson and a decision after a payment webhook refactor.
2. A simulated Codex client opens a new session and gets a resume brief from the same local store.
3. A simulated Cursor/Windsurf client searches for webhook knowledge and finds the item written by Claude Code.

Expected signal:

- the same lesson is visible across tools;
- `source_tool=claude_code_demo` is preserved;
- no cloud account or sync service is involved;
- the store is an isolated demo root, not a real user profile.

## Run the safe command-line demo

From the repository root:

```bash
export PYTHONIOENCODING=utf-8
python demos/cross_tool_continuity_demo.py
```

On Windows, use the Python interpreter from your environment:

```powershell
$env:PYTHONIOENCODING='utf-8'
python demos\cross_tool_continuity_demo.py
```

Expected output:

```text
piia-engram cross-tool continuity demo
Store: <demo-root> (isolated temporary data, not ~/.engram)

== Claude Code records the handoff ==
[Claude Code] wrote:
  lesson: ... source_tool=claude_code_demo
  decision: ... source_tool=claude_code_demo
  session: ... tool=claude_code_demo

== Codex opens a new session ==
[Codex] resume brief includes:
  identity: yes
  recent payment context: yes
  source provenance: yes
  suggested next step: add retry tests before changing handler logic

== Cursor/Windsurf searches the same memory ==
[Cursor] search_knowledge('payment webhooks signature replayable'):
  found: yes
  source_tool: claude_code_demo
```

To inspect the temporary demo store after the run:

```bash
python demos/cross_tool_continuity_demo.py --keep
```

The `--keep` mode prints the temporary path. Do not use real user data in screenshots from this mode.

## Machine-readable proof

For CI, audits, or release evidence, use JSON mode:

```bash
python demos/cross_tool_continuity_demo.py --json
```

This emits a metadata-only payload with:

- `loop_checks`: booleans for the synthetic write -> resume -> search -> provenance loop;
- `loop_passed`: `true` only when all loop checks pass;
- `continuity.readiness_level`: the metadata readiness level from `engram continuity`.

The JSON output does not include memory bodies, local paths, temporary root names, session IDs, or raw telemetry payloads.

Important distinction:

- `engram continuity` reports readiness metadata from an Engram store.
- `cross_tool_continuity_demo.py --json` proves a synthetic isolated A -> B -> C loop.

Readiness is useful operational evidence, but the demo JSON is the bounded proof that a simulated Claude Code write can be resumed by a simulated Codex session and found by a simulated Cursor/Windsurf search.

## Public screenshot walkthrough

Use this version for README images, blog posts, or MCP directory screenshots.

### Screenshot 1: Claude Code writes the handoff

Show:

```text
[Claude Code] wrote:
  lesson: lesson_xxx source_tool=claude_code_demo
  decision: decision_xxx source_tool=claude_code_demo
  session: 2026-... tool=claude_code_demo
```

Caption:

> A simulated Claude Code client records the durable lesson and decision. The source is preserved.

### Screenshot 2: Codex resumes

Show:

```text
[Codex] resume brief includes:
  identity: yes
  recent payment context: yes
  source provenance: yes
```

Caption:

> A simulated Codex client starts from the same approved identity and recent context.

### Screenshot 3: Cursor/Windsurf searches

Show:

```text
[Cursor] search_knowledge('payment webhooks signature replayable'):
  found: yes
  source_tool: claude_code_demo
```

Caption:

> A third MCP-compatible client finds the same lesson. The memory belongs to the user, not to one tool.

## Synthetic demo data

Identity:

```json
{
  "role": "solo SaaS developer",
  "language": "zh-CN",
  "work_style": "Lead with the conclusion, then give the smallest runnable next step.",
  "quality_bar": "Run relevant tests before changing public behavior."
}
```

Lesson:

```json
{
  "domain": "payments",
  "summary": "For payment webhooks, verify the signature before writing business state; failed events must be replayable.",
  "source_tool": "claude_code_demo",
  "tier": "verified"
}
```

Decision:

```json
{
  "question": "Should payment webhook side effects run inline or through a queue?",
  "choice": "Validate and persist synchronously, then process side effects in a background job.",
  "source_tool": "claude_code_demo",
  "tier": "verified"
}
```

## Failure handling

If the demo fails:

- Import error: run it from the repository root, or install the package with `pip install -e .`.
- Resume brief does not include the payment context: confirm the same temporary root is used for all simulated clients.
- Search finds no lesson: confirm `source_tool=claude_code_demo` was printed in the first section.
- Console mojibake: set `PYTHONIOENCODING=utf-8` before running the script.

## Privacy rules for public material

Use only synthetic data in public screenshots.

Do not show:

- real `~/.engram/` contents;
- local usernames or absolute paths;
- emails;
- tokens or API keys;
- customer names;
- real project names;
- raw session summaries from real work.

Use `<demo-root>` and `<demo-project>` in public materials.

Engram is not a secrets manager. Do not store API keys, private keys, customer PII, or regulated data in lessons or decisions.
