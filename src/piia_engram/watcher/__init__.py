"""Engram Universal Watcher: file-based session capture for hook-less tools.

AI tools disagree wildly on lifecycle hooks (Claude Code: documented stdin
JSON; Cursor: undocumented env vars; Codex: no user hook slot at all — its
``notify`` config slot is occupied by the Codex App itself). What they *do*
agree on is persisting conversations to local files. The watcher exploits
that common ground:

- one core poller (:mod:`.core`) scans tool-specific transcript locations,
  read-only, with an mtime/size watermark plus a per-file byte ``offset`` so
  each save carries only the turns appended since the last one (incremental
  capture — no duplicated content across checkpoints);
- per-tool adapters (e.g. :mod:`.codex_adapter`) know where a tool keeps its
  transcripts and how to turn one into a checkpoint summary.

Boundary contract (same as the Cursor hooks): the watcher only ever calls
``save_agent_context`` — appends to the ``contexts/`` session log. It never
writes to the knowledge store, and it never writes to the watched tool's own
data. Tools that *do* have working hooks (Claude Code, Cursor) keep using
them; the watcher is the universal fallback for everything else — the
:mod:`.claude_code_adapter` even checks the settings and yields automatically
when the Stop hook is wired.
"""

from __future__ import annotations
