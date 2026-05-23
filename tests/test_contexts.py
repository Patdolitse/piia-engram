"""Agent context auto-save 功能测试。"""

import time
from pathlib import Path

import pytest

from piia_engram.core import Engram


def make_engram(tmp_path: Path) -> Engram:
    """在临时目录创建一个干净的 Engram 实例。"""
    return Engram(root=tmp_path)


# ---------------------------------------------------------------------------
# save_agent_context
# ---------------------------------------------------------------------------


def test_save_creates_file(tmp_path: Path):
    """首次保存应创建新的会话文件。"""
    engram = make_engram(tmp_path)
    result = engram.save_agent_context(
        tool="claude_code",
        content="正在实现 agent context 功能",
    )
    assert result["tool"] == "claude_code"
    assert result["session_id"]
    assert result["appended"] is False

    fpath = Path(result["file"])
    assert fpath.exists()
    text = fpath.read_text(encoding="utf-8")
    assert "claude_code" in text
    assert "正在实现 agent context 功能" in text


def test_save_append_to_existing(tmp_path: Path):
    """使用相同 session_id 应追加而非覆盖。"""
    engram = make_engram(tmp_path)
    r1 = engram.save_agent_context(
        tool="codex",
        content="第一步：分析需求",
        session_id="test-session",
    )
    assert r1["appended"] is False

    r2 = engram.save_agent_context(
        tool="codex",
        content="第二步：编写代码",
        session_id="test-session",
    )
    assert r2["appended"] is True
    assert r2["session_id"] == "test-session"

    fpath = Path(r2["file"])
    text = fpath.read_text(encoding="utf-8")
    assert "第一步：分析需求" in text
    assert "第二步：编写代码" in text


def test_save_with_project_folder(tmp_path: Path):
    """指定 project_folder 应写入文件头。"""
    engram = make_engram(tmp_path)
    result = engram.save_agent_context(
        tool="cursor",
        content="开始工作",
        project_folder="/home/user/my-project",
    )
    fpath = Path(result["file"])
    text = fpath.read_text(encoding="utf-8")
    assert "/home/user/my-project" in text


def test_save_tool_name_sanitized(tmp_path: Path):
    """工具名中的特殊字符应被规范化。"""
    engram = make_engram(tmp_path)
    result = engram.save_agent_context(
        tool="Claude Code",
        content="test",
    )
    assert result["tool"] == "claude_code"
    assert (tmp_path / "contexts" / "claude_code").is_dir()


# ---------------------------------------------------------------------------
# get_recent_context
# ---------------------------------------------------------------------------


def test_get_recent_empty(tmp_path: Path):
    """空数据库应返回空列表。"""
    engram = make_engram(tmp_path)
    sessions = engram.get_recent_context()
    assert sessions == []


def test_get_recent_returns_latest(tmp_path: Path):
    """应返回最近的会话。"""
    engram = make_engram(tmp_path)
    engram.save_agent_context(tool="claude_code", content="旧会话", session_id="s1")
    time.sleep(0.05)  # 确保文件时间戳不同
    engram.save_agent_context(tool="claude_code", content="新会话", session_id="s2")

    sessions = engram.get_recent_context(tool="claude_code", limit=1)
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "s2"
    assert "新会话" in sessions[0]["content"]


def test_get_recent_cross_tool(tmp_path: Path):
    """不指定 tool 时应搜索所有工具。"""
    engram = make_engram(tmp_path)
    engram.save_agent_context(tool="claude_code", content="cc session")
    time.sleep(0.05)
    engram.save_agent_context(tool="codex", content="codex session")

    sessions = engram.get_recent_context(limit=2)
    assert len(sessions) == 2
    tools = {s["tool"] for s in sessions}
    assert "claude_code" in tools
    assert "codex" in tools


def test_get_recent_with_limit(tmp_path: Path):
    """limit 参数应限制返回数量。"""
    engram = make_engram(tmp_path)
    for i in range(5):
        engram.save_agent_context(
            tool="claude_code",
            content=f"session {i}",
            session_id=f"s{i}",
        )
        time.sleep(0.02)

    sessions = engram.get_recent_context(tool="claude_code", limit=2)
    assert len(sessions) == 2


# ---------------------------------------------------------------------------
# list_agent_sessions
# ---------------------------------------------------------------------------


def test_list_empty(tmp_path: Path):
    """空数据库应返回空列表。"""
    engram = make_engram(tmp_path)
    sessions = engram.list_agent_sessions()
    assert sessions == []


def test_list_returns_metadata(tmp_path: Path):
    """列表应包含元数据但不包含内容。"""
    engram = make_engram(tmp_path)
    engram.save_agent_context(tool="claude_code", content="test content")

    sessions = engram.list_agent_sessions(tool="claude_code")
    assert len(sessions) == 1
    s = sessions[0]
    assert s["tool"] == "claude_code"
    assert "session_id" in s
    assert "modified_at" in s
    assert "size_bytes" in s
    assert "content" not in s


def test_list_cross_tool(tmp_path: Path):
    """不指定 tool 时应列出所有工具的会话。"""
    engram = make_engram(tmp_path)
    engram.save_agent_context(tool="claude_code", content="cc")
    engram.save_agent_context(tool="codex", content="codex")
    engram.save_agent_context(tool="cursor", content="cursor")

    sessions = engram.list_agent_sessions()
    assert len(sessions) == 3
    tools = {s["tool"] for s in sessions}
    assert tools == {"claude_code", "codex", "cursor"}


def test_list_respects_limit(tmp_path: Path):
    """limit 参数应限制返回数量。"""
    engram = make_engram(tmp_path)
    for i in range(10):
        engram.save_agent_context(
            tool="claude_code",
            content=f"s{i}",
            session_id=f"s{i}",
        )

    sessions = engram.list_agent_sessions(tool="claude_code", limit=3)
    assert len(sessions) == 3


# ---------------------------------------------------------------------------
# Directory structure
# ---------------------------------------------------------------------------


def test_contexts_dir_created(tmp_path: Path):
    """Engram 初始化应创建 contexts 目录。"""
    engram = make_engram(tmp_path)
    assert (tmp_path / "contexts").is_dir()


def test_tool_isolation(tmp_path: Path):
    """不同工具的上下文应存在不同子目录。"""
    engram = make_engram(tmp_path)
    engram.save_agent_context(tool="claude_code", content="cc")
    engram.save_agent_context(tool="codex", content="codex")

    assert (tmp_path / "contexts" / "claude_code").is_dir()
    assert (tmp_path / "contexts" / "codex").is_dir()

    cc_files = list((tmp_path / "contexts" / "claude_code").glob("*.md"))
    codex_files = list((tmp_path / "contexts" / "codex").glob("*.md"))
    assert len(cc_files) == 1
    assert len(codex_files) == 1
