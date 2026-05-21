from __future__ import annotations

from .router_tool import engram_action, resolve_intent


class FakeCore:
    def __init__(self) -> None:
        self.calls = []

    def search_knowledge(self, query: str, limit: int = 5):
        self.calls.append(("search_knowledge", query, limit))
        return [{"id": "k1", "summary": query}]

    def add_lesson(self, content: str, domain: str = "general"):
        self.calls.append(("add_lesson", content, domain))
        return {"id": "l1", "content": content, "domain": domain}

    def add_decision(self, decision: str, reasoning: str = ""):
        self.calls.append(("add_decision", decision, reasoning))
        return {"id": "d1", "decision": decision, "reasoning": reasoning}

    def get_user_context(self):
        self.calls.append(("get_user_context",))
        return {"user": "context"}

    def get_project_context(self, project: str):
        self.calls.append(("get_project_context", project))
        return {"project": project}

    def get_knowledge_overview(self, scope: str = "all"):
        self.calls.append(("get_knowledge_overview", scope))
        return {"items": [{"id": "a"}, {"id": "b"}]}

    def find_similar_knowledge(self, item_id: str):
        self.calls.append(("find_similar_knowledge", item_id))
        return [{"id": "a2"}] if item_id == "a" else []


def test_direct_search_routes_to_search_knowledge():
    core = FakeCore()
    result = engram_action("search", {"query": "pricing", "limit": 3}, core)

    assert result["ok"] is True
    assert result["intent"] == "search"
    assert core.calls == [("search_knowledge", "pricing", 3)]


def test_chinese_search_intent_is_recognized():
    core = FakeCore()
    result = engram_action("帮我搜索项目记忆", {"text": "asset graph"}, core)

    assert result["ok"] is True
    assert core.calls == [("search_knowledge", "asset graph", 5)]


def test_remember_chinese_intent_routes_to_lesson():
    core = FakeCore()
    result = engram_action("请记住这条经验", {"content": "prefer local files", "domain": "piia"}, core)

    assert result["intent"] == "add_lesson"
    assert core.calls == [("add_lesson", "prefer local files", "piia")]


def test_decision_intent_routes_to_add_decision():
    core = FakeCore()
    result = engram_action("decision", {"decision": "Use coordinator", "reasoning": "Lower risk"}, core)

    assert result["result"]["id"] == "d1"
    assert core.calls == [("add_decision", "Use coordinator", "Lower risk")]


def test_get_context_uses_user_context_without_project():
    core = FakeCore()
    result = engram_action("get_context", {}, core)

    assert result["result"] == {"user": "context"}
    assert core.calls == [("get_user_context",)]


def test_get_context_uses_project_context_when_project_given():
    core = FakeCore()
    result = engram_action("同步上下文", {"project": "Engram"}, core)

    assert result["result"] == {"project": "Engram"}
    assert core.calls == [("get_project_context", "Engram")]


def test_cleanup_collects_dedupe_candidates_without_merging():
    core = FakeCore()
    result = engram_action("cleanup", {"scope": "lessons"}, core)

    assert result["result"]["dedupe_candidates"] == [{"source": "a", "similar": [{"id": "a2"}]}]
    assert core.calls == [
        ("get_knowledge_overview", "lessons"),
        ("find_similar_knowledge", "a"),
        ("find_similar_knowledge", "b"),
    ]


def test_unknown_intent_returns_supported_intents():
    result = engram_action("launch rockets", {}, FakeCore())

    assert result["ok"] is False
    assert result["error"] == "unknown_intent"
    assert "cleanup" in result["supported_intents"]


def test_none_params_are_safe():
    core = FakeCore()
    result = engram_action("serch", None, core)

    assert result["ok"] is True
    assert core.calls == [("search_knowledge", "", 5)]


def test_resolve_intent_handles_typo_aliases():
    assert resolve_intent("remeber") == "add_lesson"
    assert resolve_intent("searchh") == "search"

