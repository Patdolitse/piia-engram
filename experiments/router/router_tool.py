"""Universal router tool prototype.

The experiment compresses many specific Engram tools into a single
``engram_action(intent, params)`` interface. It is deliberately lightweight:
tests inject a fake core so the prototype can evaluate routing behavior
without touching the real knowledge store.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SUPPORTED_INTENTS = [
    "search",
    "add_lesson",
    "add_decision",
    "get_context",
    "cleanup",
]

ALIASES = {
    "search": (
        "search",
        "find",
        "lookup",
        "recall",
        "query",
        "搜索",
        "查找",
        "检索",
        "回忆",
    ),
    "add_lesson": (
        "add_lesson",
        "lesson",
        "remember",
        "learn",
        "preference",
        "记住",
        "经验",
        "偏好",
        "教训",
        "规则",
    ),
    "add_decision": (
        "add_decision",
        "decision",
        "decide",
        "choose",
        "选择",
        "决定",
        "决策",
    ),
    "get_context": (
        "get_context",
        "context",
        "sync",
        "bootstrap",
        "cold_start",
        "上下文",
        "冷启动",
        "同步",
    ),
    "cleanup": (
        "cleanup",
        "clean",
        "dedupe",
        "merge",
        "整理",
        "清理",
        "去重",
        "合并",
    ),
}

FUZZY_ALIASES = {
    "serch": "search",
    "searchh": "search",
    "remeber": "add_lesson",
    "rember": "add_lesson",
}


class NullCore:
    """Small default core that fails explicitly when no real core is injected."""

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"No Engram core was provided for {name}")


def engram_action(
    intent: str,
    params: Mapping[str, Any] | None = None,
    core: Any | None = None,
) -> dict[str, Any]:
    """Route one universal MCP call to a specific Engram action."""
    normalized_params = dict(params or {})
    resolved = resolve_intent(intent)
    if resolved is None:
        return {
            "ok": False,
            "intent": intent,
            "error": "unknown_intent",
            "supported_intents": SUPPORTED_INTENTS,
        }

    target = core or NullCore()
    handlers = {
        "search": _handle_search,
        "add_lesson": _handle_add_lesson,
        "add_decision": _handle_add_decision,
        "get_context": _handle_get_context,
        "cleanup": _handle_cleanup,
    }
    return handlers[resolved](target, normalized_params)


def resolve_intent(intent: str) -> str | None:
    """Resolve English/Chinese/free-form intent text to a supported action."""
    text = (intent or "").strip().lower()
    if not text:
        return None

    if text in SUPPORTED_INTENTS:
        return text
    if text in FUZZY_ALIASES:
        return FUZZY_ALIASES[text]

    for canonical, aliases in ALIASES.items():
        if any(alias in text for alias in aliases):
            return canonical
    return None


def _handle_search(core: Any, params: dict[str, Any]) -> dict[str, Any]:
    query = _first(params, "query", "text", "content", default="")
    limit = int(params.get("limit", 5))
    result = core.search_knowledge(query, limit=limit)
    return {"ok": True, "intent": "search", "result": result}


def _handle_add_lesson(core: Any, params: dict[str, Any]) -> dict[str, Any]:
    content = _first(params, "content", "text", "lesson", default="")
    domain = params.get("domain", "general")
    result = core.add_lesson(content, domain=domain)
    return {"ok": True, "intent": "add_lesson", "result": result}


def _handle_add_decision(core: Any, params: dict[str, Any]) -> dict[str, Any]:
    decision = _first(params, "decision", "content", "text", default="")
    reasoning = params.get("reasoning", "")
    result = core.add_decision(decision, reasoning=reasoning)
    return {"ok": True, "intent": "add_decision", "result": result}


def _handle_get_context(core: Any, params: dict[str, Any]) -> dict[str, Any]:
    project = params.get("project")
    if project and hasattr(core, "get_project_context"):
        result = core.get_project_context(project)
    else:
        result = core.get_user_context()
    return {"ok": True, "intent": "get_context", "result": result}


def _handle_cleanup(core: Any, params: dict[str, Any]) -> dict[str, Any]:
    scope = params.get("scope", "all")
    overview = core.get_knowledge_overview(scope=scope)
    candidates = []

    for item in overview.get("items", []):
        item_id = item.get("id")
        if not item_id or not hasattr(core, "find_similar_knowledge"):
            continue
        similar = core.find_similar_knowledge(item_id)
        if similar:
            candidates.append({"source": item_id, "similar": similar})

    return {
        "ok": True,
        "intent": "cleanup",
        "result": {
            "scope": scope,
            "overview": overview,
            "dedupe_candidates": candidates,
        },
    }


def _first(params: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = params.get(key)
        if value not in (None, ""):
            return value
    return default

