"""Transport-neutral knowledge search application service.

Contract matrix for the dock-search / MCP search_knowledge pilot:

- Application service owns only explicit delegation to an already-constructed
  Engram-like store. It preserves the raw ``Engram.search_knowledge`` dict,
  including any extra metadata, and lets core exceptions propagate unchanged.
- CLI and MCP adapters own transport concerns: argument/filter parsing,
  caller/index policy, governance, freshness, truncation, usage-policy and
  permission injection, telemetry, and JSON/text rendering.
"""

from __future__ import annotations

from typing import Any


def search_knowledge(
    eng,
    *,
    query: str,
    scope: str = "all",
    limit: int = 10,
    filters: dict[str, Any] | None = None,
    project_folder: str | None = None,
    allow_hybrid_index: bool = True,
) -> dict[str, Any]:
    """Run the core knowledge search through a narrow application boundary."""
    return eng.search_knowledge(
        query,
        scope=scope,
        limit=limit,
        filters=filters,
        allow_hybrid_index=allow_hybrid_index,
        project_folder=project_folder,
    )
