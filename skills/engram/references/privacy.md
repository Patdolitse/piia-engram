# Engram privacy & ownership reference

Read this before persisting anything. These are the honest boundaries Engram
operates within today.

## Local-first, user-owned

- Knowledge is stored as **local JSON files the user owns** (under the user's
  Engram directory). There is **no cloud account** and **no vendor lock-in**.
- The user can inspect, edit, export, and delete their store directly. There is
  no hidden memory the user cannot see.

## The user decides what is remembered

- Engram **suggests**; the user **decides**. The AI may propose a lesson,
  decision, or playbook, but promoting it to verified knowledge is a
  **user-controlled** action via explicit write tools (`add_lesson`,
  `add_decision`, `add_playbook`, `memory_store`, `wrap_up_session`).
- Some MCP clients run session hooks that capture context to the
  **user-visible daily log and the staging tier**. Staged entries are **not
  silently promoted to verified/trusted knowledge**, and nothing is written to a
  place the user cannot inspect.

## Staging → verified governance

- Knowledge moves through a **staging → verified** path. Unreviewed entries are
  not silently promoted to trusted facts. Review/verify steps exist so the user
  controls what becomes authoritative.

## Telemetry

- Telemetry is **off by default**. If enabled, it writes a **local log only**
  with no network requests; **remote sending is a separate, explicit opt-in**.
- Do not overstate the network boundary or call Engram unconditionally secure.
  State the honest boundary instead: local-first storage with opt-in telemetry.

## What not to store

- Do **not** store secrets: passwords, API keys, tokens, or client PII.
- Keep memory to durable, generalizable knowledge — preferences, decisions,
  lessons, project context — not transient debugging detail.

## External tool configs

- Engram treats external client configs as **read-only by default**. Writing to
  another tool's configuration is an explicit, opt-in action, never automatic.
