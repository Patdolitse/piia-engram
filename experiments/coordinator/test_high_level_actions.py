from __future__ import annotations

from .high_level_actions import HighLevelActions, infer_domain, looks_like_decision


class FakeCore:
    def __init__(self, search_results=None) -> None:
        self.calls = []
        self.search_results = search_results if search_results is not None else []

    def search_knowledge(self, query: str, limit: int = 5):
        self.calls.append(("search_knowledge", query, limit))
        return self.search_results

    def add_lesson(self, content: str, domain: str = "general"):
        self.calls.append(("add_lesson", content, domain))
        return {"id": "lesson-1", "content": content, "domain": domain}

    def add_decision(self, decision: str, reasoning: str = ""):
        self.calls.append(("add_decision", decision, reasoning))
        return {"id": "decision-1", "decision": decision, "reasoning": reasoning}

    def get_project_context(self, project: str):
        self.calls.append(("get_project_context", project))
        return {"project": project}

    def get_user_context(self):
        self.calls.append(("get_user_context",))
        return {"identity": "user"}

    def get_knowledge_overview(self, scope: str = "all"):
        self.calls.append(("get_knowledge_overview", scope))
        return {"items": [{"id": "k1"}, {"id": "k2"}]}

    def find_similar_knowledge(self, item_id: str):
        self.calls.append(("find_similar_knowledge", item_id))
        return [{"id": "k1b"}] if item_id == "k1" else []

    def get_knowledge_inheritance(self, project_description: str):
        self.calls.append(("get_knowledge_inheritance", project_description))
        return {"lessons": ["reuse local-first graph"]}


def test_remember_stores_lesson_with_inferred_domain():
    core = FakeCore()
    result = HighLevelActions(core).remember("MCP tools should stay small")

    assert result["kind"] == "lesson"
    assert result["domain"] == "mcp"
    assert core.calls == [
        ("search_knowledge", "MCP tools should stay small", 3),
        ("add_lesson", "MCP tools should stay small", "mcp"),
    ]


def test_remember_stores_decision_when_content_looks_like_decision():
    core = FakeCore()
    result = HighLevelActions(core).remember(
        "We decided to prefer coordinator tools",
        {"reasoning": "Better model affordance"},
    )

    assert result["kind"] == "decision"
    assert core.calls == [
        ("search_knowledge", "We decided to prefer coordinator tools", 3),
        ("add_decision", "We decided to prefer coordinator tools", "Better model affordance"),
    ]


def test_remember_skips_exact_duplicate():
    core = FakeCore(search_results=[{"id": "old", "summary": "prefer local files"}])
    result = HighLevelActions(core).remember("prefer local files")

    assert result["stored"] is False
    assert result["reason"] == "duplicate_exact_match"
    assert core.calls == [("search_knowledge", "prefer local files", 3)]


def test_recall_combines_search_and_project_context():
    core = FakeCore(search_results=[{"id": "k"}])
    result = HighLevelActions(core).recall("asset graph", project="Engram")

    assert result["results"] == [{"id": "k"}]
    assert result["project_context"] == {"project": "Engram"}
    assert core.calls == [
        ("search_knowledge", "asset graph", 8),
        ("get_project_context", "Engram"),
    ]


def test_cleanup_builds_merge_plan_without_auto_applying():
    core = FakeCore()
    result = HighLevelActions(core).cleanup(scope="lessons")

    assert result["auto_applied"] is False
    assert result["merge_plan"] == [{"source": "k1", "candidates": [{"id": "k1b"}]}]
    assert core.calls == [
        ("get_knowledge_overview", "lessons"),
        ("find_similar_knowledge", "k1"),
        ("find_similar_knowledge", "k2"),
    ]


def test_inherit_delegates_to_inheritance_api():
    core = FakeCore()
    result = HighLevelActions(core).inherit("A local-first project map")

    assert result["result"] == {"lessons": ["reuse local-first graph"]}
    assert core.calls == [("get_knowledge_inheritance", "A local-first project map")]


def test_sync_collects_user_and_project_context():
    core = FakeCore()
    result = HighLevelActions(core).sync(project="Engram")

    assert result["user_context"] == {"identity": "user"}
    assert result["project_context"] == {"project": "Engram"}
    assert core.calls == [
        ("get_user_context",),
        ("get_project_context", "Engram"),
    ]


def test_classifiers_are_predictable():
    assert looks_like_decision("我们决定使用 coordinator")
    assert infer_domain("本地项目资产图谱需要记住路径") == "project_context"

