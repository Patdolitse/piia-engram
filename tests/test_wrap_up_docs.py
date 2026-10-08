from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cross_tool_guide_documents_lightweight_wrap_up_boundary() -> None:
    text = (ROOT / "docs" / "cross-tool-guide.md").read_text(encoding="utf-8")

    assert "wrap_up_session" in text
    assert "lightweight session-end save" in text
    assert "does not run full reconciliation by default" in text
    assert "run_reconcile=True" in text


def test_wrap_up_docs_do_not_claim_background_autonomy() -> None:
    text = (ROOT / "docs" / "cross-tool-guide.md").read_text(encoding="utf-8").lower()

    forbidden = [
        "autonomous background sync",
        "always-on reconciliation",
        "guaranteed live sync",
        "provider-backed cleanup",
    ]
    for phrase in forbidden:
        assert phrase not in text


def test_public_docs_do_not_describe_default_wrap_up_full_reconcile() -> None:
    docs = [
        ROOT / "docs" / "architecture.md",
        ROOT / "docs" / "cross-tool-guide.md",
        ROOT / "docs" / "user-guide.md",
        ROOT / "docs" / "user-guide.zh-CN.md",
    ]
    draft = ROOT / "docs" / "_drafts" / "user_guide_draft.md"
    if draft.exists():
        docs.append(draft)
    forbidden = [
        "wrap_up_session | Save insights + sync at session end",
        "wrap_up_session automatically syncs external memories by default",
        "wrap_up_session runs full reconciliation by default",
        "default full sync",
        "always-on reconciliation",
        "guaranteed live sync",
        "`wrap_up_session` 收尾时再扫一次兜底",
        "reconcile_memories 默认就跑",
    ]
    for path in docs:
        text = path.read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in text, f"{path} contains stale phrase: {phrase}"


def test_public_docs_name_explicit_reconcile_boundary() -> None:
    text = "\n".join(
        (ROOT / "docs" / name).read_text(encoding="utf-8")
        for name in [
            "architecture.md",
            "cross-tool-guide.md",
            "user-guide.md",
            "user-guide.zh-CN.md",
        ]
    )

    assert "lightweight session-end save" in text
    assert "run_reconcile=True" in text
    assert "does not run full reconciliation by default" in text
    assert "`wrap_up_session` 是轻量的会话结束保存" in text
    # run_reconcile=True no longer imports; the explicit command is named instead.
    assert "engram import-memories" in text


def test_public_docs_do_not_claim_automatic_import() -> None:
    docs = [
        "README.md",
        "README.zh-CN.md",
        "docs/architecture.md",
        "docs/cross-tool-guide.md",
        "docs/operator-mcp-cheatsheet.md",
        "docs/quickstart-first-value.md",
        "docs/quickstart-first-value.zh-CN.md",
        "docs/user-guide.md",
        "docs/user-guide.zh-CN.md",
        "CONTRIBUTING.md",
        ".mcp/server.json",
    ]
    for name in docs:
        text = (ROOT / name).read_text(encoding="utf-8")
        for phrase in AUTOMATIC_IMPORT_CLAIMS:
            assert phrase.lower() not in text.lower(), f"{name} still claims automatic import: {phrase}"
        if name.startswith(("README", "docs/user-guide", "docs/quickstart")):
            assert "engram import-memories" in text, name


# Phrases that describe the removed automatic import (startup sync, first-call
# bootstrap, reconcile side effects of cold start).
AUTOMATIC_IMPORT_CLAIMS = [
    "auto-bootstrap",
    "imports your preferences and project rules automatically",
    "reconciles memories/config snippets from local AI tools when an MCP server starts",
    "ENGRAM_MCP_STARTUP_SYNC=eager",
    "startup reconciliation",
    "set to background for local cross-tool sync",
    "cross-tool memory/config sync",
    "auto-sync side effects",
    "skips expensive reconciliation",
    "自动引导",
    "启动同步",
    "自动同步副作用",
    "跳过昂贵的 reconcile",
]


def test_mcp_tool_descriptions_do_not_claim_automatic_import() -> None:
    import asyncio

    from piia_engram import mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert tools
    for tool in tools:
        text = (tool.description or "").lower()
        for phrase in AUTOMATIC_IMPORT_CLAIMS:
            assert phrase.lower() not in text, f"{tool.name} description: {phrase}"
    by_name = {tool.name: tool.description or "" for tool in tools}
    assert "engram import-memories" in by_name["get_user_context"]


def test_cross_tool_guide_documents_telemetry_as_separate_opt_in_boundary() -> None:
    text = (ROOT / "docs" / "cross-tool-guide.md").read_text(encoding="utf-8")

    assert "Telemetry and feedback are separate opt-in metadata paths" in text
    assert "They are not reconciliation" in text
    assert "Default non-opt-in closeout sends no remote feedback" in text


def test_operator_docs_define_reconcile_as_owner_maintenance() -> None:
    text = (ROOT / "docs" / "operator-mcp-cheatsheet.md").read_text(encoding="utf-8")

    assert "Reconciliation is an owner maintenance action" in text
    assert "Default session closeout does not scan external AI memory or config files" in text
    assert "wrap_up_session(..., run_reconcile=True, user_confirmed=True)" in text
    assert "imports nothing" in text
    assert "engram import-memories" in text
    assert "staging-tier candidates" in text


def test_docs_include_bounded_closeout_diagnostics() -> None:
    cross = (ROOT / "docs" / "cross-tool-guide.md").read_text(encoding="utf-8")
    ops = (ROOT / "docs" / "operator-mcp-cheatsheet.md").read_text(encoding="utf-8")

    assert "diagnose_wrap_up_session.py" in cross
    assert "Default diagnostics use an isolated temporary store" in cross
    assert "--live-inspect" in cross
    assert "--live-closeout --allow-write" in cross
    assert "Closeout budget" in ops
    assert "does not change the reconcile boundary" in ops
