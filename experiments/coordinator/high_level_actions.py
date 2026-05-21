"""High-level Engram action coordinator prototype.

This experiment keeps the MCP surface small while preserving richer internal
orchestration. Instead of one universal router, the model receives a handful
of intent-shaped tools: remember, recall, cleanup, inherit, and sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class NullCore:
    """Default core placeholder for explicit failure in ad-hoc use."""

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"No Engram core was provided for {name}")


@dataclass
class HighLevelActions:
    core: Any

    def remember(self, content: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Store a lesson or decision after lightweight classification."""
        context = context or {}
        domain = context.get("domain") or infer_domain(content)
        existing = self.core.search_knowledge(content, limit=3)
        exact_matches = [
            item
            for item in existing
            if _normalized(item.get("summary") or item.get("content")) == _normalized(content)
        ]
        if exact_matches:
            return {
                "ok": True,
                "action": "remember",
                "stored": False,
                "reason": "duplicate_exact_match",
                "matches": exact_matches,
            }

        if looks_like_decision(content):
            result = self.core.add_decision(content, reasoning=context.get("reasoning", ""))
            kind = "decision"
        else:
            result = self.core.add_lesson(content, domain=domain)
            kind = "lesson"

        return {
            "ok": True,
            "action": "remember",
            "stored": True,
            "kind": kind,
            "domain": domain,
            "result": result,
        }

    def recall(self, query: str, project: str | None = None) -> dict[str, Any]:
        """Retrieve relevant user or project knowledge for a work session."""
        results = self.core.search_knowledge(query, limit=8)
        project_context = None
        if project:
            project_context = self.core.get_project_context(project)

        return {
            "ok": True,
            "action": "recall",
            "query": query,
            "results": results,
            "project_context": project_context,
        }

    def cleanup(self, scope: str = "all") -> dict[str, Any]:
        """Inspect duplicate/stale knowledge and return a review plan."""
        overview = self.core.get_knowledge_overview(scope=scope)
        merge_plan = []
        for item in overview.get("items", []):
            item_id = item.get("id")
            if not item_id:
                continue
            similar = self.core.find_similar_knowledge(item_id)
            if similar:
                merge_plan.append({"source": item_id, "candidates": similar})

        return {
            "ok": True,
            "action": "cleanup",
            "scope": scope,
            "overview": overview,
            "merge_plan": merge_plan,
            "auto_applied": False,
        }

    def inherit(self, project_description: str) -> dict[str, Any]:
        """Find reusable knowledge for a new project or task context."""
        result = self.core.get_knowledge_inheritance(project_description)
        return {
            "ok": True,
            "action": "inherit",
            "project_description": project_description,
            "result": result,
        }

    def sync(self, project: str | None = None) -> dict[str, Any]:
        """Produce startup context for a new agent session."""
        user_context = self.core.get_user_context()
        project_context = self.core.get_project_context(project) if project else None
        return {
            "ok": True,
            "action": "sync",
            "user_context": user_context,
            "project_context": project_context,
        }


def remember(content: str, context: dict[str, Any] | None = None, core: Any | None = None) -> dict[str, Any]:
    """Module-level wrapper for a possible MCP tool."""
    return HighLevelActions(core or NullCore()).remember(content, context=context)


def recall(query: str, project: str | None = None, core: Any | None = None) -> dict[str, Any]:
    """Module-level wrapper for a possible MCP tool."""
    return HighLevelActions(core or NullCore()).recall(query, project=project)


def cleanup(scope: str = "all", core: Any | None = None) -> dict[str, Any]:
    """Module-level wrapper for a possible MCP tool."""
    return HighLevelActions(core or NullCore()).cleanup(scope=scope)


def inherit(project_description: str, core: Any | None = None) -> dict[str, Any]:
    """Module-level wrapper for a possible MCP tool."""
    return HighLevelActions(core or NullCore()).inherit(project_description)


def sync(project: str | None = None, core: Any | None = None) -> dict[str, Any]:
    """Module-level wrapper for a possible MCP tool."""
    return HighLevelActions(core or NullCore()).sync(project=project)


def looks_like_decision(content: str) -> bool:
    text = content.lower()
    markers = ("decided", "decision", "choose", "chosen", "选择", "决定", "决策")
    return any(marker in text for marker in markers)


def infer_domain(content: str) -> str:
    text = content.lower()
    if any(marker in text for marker in ("mcp", "tool", "fastmcp", "工具")):
        return "mcp"
    if any(marker in text for marker in ("project", "asset", "路径", "图谱")):
        return "project_context"
    return "general"


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").lower().split())

