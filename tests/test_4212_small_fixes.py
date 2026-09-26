"""4.21.2 small fixes: strict-aware instruction snippets that never overwrite the
Owner's own text, env blocks that survive a config rewrite, a state-aware review
preview, a zero-write doctor, and memory imports that keep the whole body.

Every test points HOME, USERPROFILE, APPDATA, LOCALAPPDATA, ENGRAM_DIR and
ENGRAM_CACHE_DIR at tmp_path; nothing here reads or writes a real store.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)
from piia_engram import doctor, update_check
from piia_engram.core import Engram
from piia_engram.setup_wizard import (
    _INSTRUCTION_MARKER,
    _INSTRUCTION_MARKER_END,
    _INSTRUCTION_SNIPPETS,
    _inject_instruction_snippet,
)

# The v=1 Claude Code zh snippet as shipped in v3.29.0 (setup_wizard.py @ 2bb9a8b).
_V1_DEFAULT_ZH = (
    "\n<!-- piia-engram:auto-injected -->\n"
    "## Engram 记忆层\n\n"
    "本机已安装 PIIA Engram（MCP 记忆层）。\n\n"
    "- **对话开头**：调用 `get_user_context` 了解用户身份和偏好\n"
    "- **学到经验/踩坑**：调用 `add_lesson` 存入\n"
    "- **做出决策**：调用 `add_decision` 记录选择和理由\n"
    "- **对话结束**：调用 `wrap_up_session` 保存上下文\n"
    "- **搜索历史知识**：调用 `search_knowledge`\n"
    "<!-- /piia-engram -->\n"
)

# An Owner-edited v=2 block, shaped like the one on the maintainer's machine.
_OWNER_BLOCK = (
    f"{_INSTRUCTION_MARKER}\n"
    "## Engram 记忆层（严格模式：只读 + 提案，Owner 定）\n\n"
    "读取：会话开始需要接续时调用 `get_resume_brief`。\n"
    "写入（提案）规则：每条提案带 7 项卡片；每个会话最多 3 条提案。\n"
    "不调用 `wrap_up_session` 做自动保存。\n"
    f"{_INSTRUCTION_MARKER_END}\n"
)
_OWNER_CURSOR = (
    "---\ndescription: my own Engram rules\nglobs:\nalwaysApply: true\n---\n\n"
    "Call get_resume_brief when resuming. Propose, never auto-save.\n"
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(h))
    monkeypatch.setenv("APPDATA", str(h / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(h / "AppData" / "Local"))
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("ENGRAM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ENGRAM_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("ENGRAM_NO_AUTO_BACKUP", "1")
    for var in ("ENGRAM_APPROVAL", "ENGRAM_RECONCILE", "ENGRAM_REVIEW_QUEUE_MAX",
                "ENGRAM_REVIEW_QUEUE_CEILING", "ENGRAM_GOVERNANCE", "ENGRAM_CLIENT_TYPE"):
        monkeypatch.delenv(var, raising=False)
    return h


def _point(monkeypatch, tool_id: str, path: Path) -> Path:
    monkeypatch.setitem(_INSTRUCTION_SNIPPETS[tool_id], "path_fn", lambda _home: path)
    return path


def _latch_strict(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "approval_mode.json").write_text('{"strict_first_seen_at": "2026-09-26T00:00:00Z"}', encoding="utf-8")


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


# ---------------------------------------------------------------------------
# 1. strict-aware snippet text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_id", ["claude_code", "codex", "windsurf", "cursor"])
def test_strict_store_gets_the_propose_only_snippet(home, tmp_path, monkeypatch, tool_id):
    target = _point(monkeypatch, tool_id, home / f"{tool_id}.md")
    _latch_strict(tmp_path / "store")

    assert _inject_instruction_snippet(tool_id, lang="en", file_safety_root=None) is not None

    text = target.read_text(encoding="utf-8")
    assert "strict mode" in text
    assert "get_resume_brief" in text
    assert "call `add_lesson`" not in text and "call `wrap_up_session`" not in text
    if tool_id == "cursor":
        assert "alwaysApply: true" in text
    else:
        assert text.count(_INSTRUCTION_MARKER) == 1


def test_non_strict_store_keeps_the_default_snippet(home, monkeypatch):
    target = _point(monkeypatch, "codex", home / "AGENTS.md")

    _inject_instruction_snippet("codex", lang="en")

    assert "wrap_up_session" in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 2. setup / doctor --fix never overwrite the Owner's own text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strict", [False, True])
def test_owner_edited_block_survives_injection_byte_for_byte(home, tmp_path, monkeypatch, strict):
    target = _point(monkeypatch, "claude_code", home / "CLAUDE.md")
    original = "# my rules\r\n\r\nkeep me\r\n\r\n" + _OWNER_BLOCK.replace("\n", "\r\n")
    target.write_bytes(original.encode("utf-8"))
    if strict:
        _latch_strict(tmp_path / "store")

    assert _inject_instruction_snippet("claude_code", lang="zh") is None
    assert _inject_instruction_snippet("claude_code", lang="en") is None

    assert target.read_bytes() == original.encode("utf-8")


def test_owner_edited_cursor_rule_survives_injection(home, monkeypatch):
    target = _point(monkeypatch, "cursor", home / "engram.mdc")
    target.write_text(_OWNER_CURSOR, encoding="utf-8")
    before = target.read_bytes()

    assert _inject_instruction_snippet("cursor", lang="zh") is None

    assert target.read_bytes() == before


def test_doctor_fix_leaves_owner_blocks_alone_and_refreshes_defaults(home, tmp_path, monkeypatch):
    store = tmp_path / "store"
    Engram(root=store)
    _latch_strict(store)
    claude = home / ".claude" / "CLAUDE.md"
    claude.parent.mkdir(parents=True)
    claude.write_text("# rules\n\n" + _OWNER_BLOCK, encoding="utf-8")
    cursor = home / ".cursor" / "rules" / "engram.mdc"
    cursor.parent.mkdir(parents=True)
    cursor.write_text(_OWNER_CURSOR, encoding="utf-8")
    codex = home / ".codex" / "AGENTS.md"
    codex.parent.mkdir(parents=True)
    default_codex = _INSTRUCTION_SNIPPETS["codex"]["snippet_zh"].format(
        marker=_INSTRUCTION_MARKER, marker_end=_INSTRUCTION_MARKER_END)
    codex.write_text("# codex rules\n" + default_codex, encoding="utf-8")
    owned = {claude: claude.read_bytes(), cursor: cursor.read_bytes()}

    buf = io.StringIO()
    with redirect_stdout(buf):
        doctor._run_functional_checks(fix=True)
    out = buf.getvalue()

    for path, before in owned.items():
        assert path.read_bytes() == before, path
    assert "your own Engram block" in out
    refreshed = codex.read_text(encoding="utf-8")
    assert "严格模式" in refreshed and "# codex rules" in refreshed  # profile language: zh
    assert "`wrap_up_session` 保存上下文" not in refreshed
    assert refreshed.count(_INSTRUCTION_MARKER) == 1


def test_default_v1_and_v2_blocks_are_recognised_and_merged(home, monkeypatch):
    target = _point(monkeypatch, "claude_code", home / "CLAUDE.md")
    v2_default = _INSTRUCTION_SNIPPETS["claude_code"]["snippet_zh"].format(
        marker=_INSTRUCTION_MARKER, marker_end=_INSTRUCTION_MARKER_END)
    target.write_text("# top\n" + _V1_DEFAULT_ZH + "\n# middle\n" + v2_default, encoding="utf-8")

    assert _inject_instruction_snippet("claude_code", lang="en") is not None

    text = target.read_text(encoding="utf-8")
    assert "<!-- piia-engram:auto-injected -->" not in text  # the default v=1 block is gone
    assert text.count(_INSTRUCTION_MARKER) == 1
    assert "# top" in text and "# middle" in text
    assert "Memory Layer" in text


def test_owner_edited_v1_block_is_kept(home, monkeypatch):
    target = _point(monkeypatch, "claude_code", home / "CLAUDE.md")
    custom_v1 = "<!-- piia-engram:auto-injected -->\nmy own words\n<!-- /piia-engram -->\n"
    target.write_text(custom_v1, encoding="utf-8")

    assert _inject_instruction_snippet("claude_code", lang="zh") is None
    assert target.read_text(encoding="utf-8") == custom_v1


# ---------------------------------------------------------------------------
# 3. doctor flags the auto-save default under strict
# ---------------------------------------------------------------------------


def test_doctor_reports_default_text_as_stale_under_strict(home, tmp_path):
    store = tmp_path / "store"
    Engram(root=store)
    _latch_strict(store)
    codex = home / ".codex" / "AGENTS.md"
    codex.parent.mkdir(parents=True)
    default_codex = _INSTRUCTION_SNIPPETS["codex"]["snippet_en"].format(
        marker=_INSTRUCTION_MARKER, marker_end=_INSTRUCTION_MARKER_END)
    codex.write_text(default_codex, encoding="utf-8")
    before = codex.read_bytes()

    buf = io.StringIO()
    with redirect_stdout(buf):
        doctor._run_functional_checks(fix=False)
    out = buf.getvalue()

    assert "[stale] codex" in out
    assert codex.read_bytes() == before


# ---------------------------------------------------------------------------
# 4. env blocks: kept on rewrite, checked by doctor
# ---------------------------------------------------------------------------


def test_setup_rewrite_keeps_owner_env_keys_in_json(home, tmp_path):
    cfg = home / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"engram": {"command": "old", "env": {
        "ENGRAM_DIR": str(tmp_path / "store"),
        "ENGRAM_APPROVAL": "strict",
        "ENGRAM_REVIEW_QUEUE_MAX": "30",
        "FASTEMBED_CACHE_PATH": "X:/cache",
    }}}}), encoding="utf-8")

    W._write_mcp_config(cfg, sys.executable, "", authorized_external_write=True)

    env = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["engram"]["env"]
    assert env["ENGRAM_APPROVAL"] == "strict"
    assert env["ENGRAM_REVIEW_QUEUE_MAX"] == "30"
    assert env["FASTEMBED_CACHE_PATH"] == "X:/cache"
    assert env["ENGRAM_DIR"] == str(tmp_path / "store")


def test_setup_rewrite_keeps_owner_env_keys_in_toml_and_adds_strict(home, tmp_path):
    store = tmp_path / "store"
    _latch_strict(store)
    cfg = home / "config.toml"
    cfg.write_text(
        "[mcp_servers.engram]\ncommand = 'old'\n\n[mcp_servers.engram.env]\n"
        f"ENGRAM_DIR = '{store.as_posix()}'\nENGRAM_RECONCILE = \"0\"\nDO_NOT_TRACK = \"0\"\n\n"
        "[mcp_servers.engram.tools.search_knowledge]\napproval_mode = \"approve\"\n",
        encoding="utf-8",
    )

    W._write_mcp_config_toml(cfg, sys.executable, "", authorized_external_write=True)

    data = W._read_mcp_config(cfg, fmt="toml")  # tomllib is 3.11+; this runs on 3.10 too
    env = data["mcp_servers"]["engram"]["env"]
    assert env["ENGRAM_RECONCILE"] == "0"
    assert env["DO_NOT_TRACK"] == "0"
    assert env["ENGRAM_APPROVAL"] == "strict"
    text = cfg.read_text(encoding="utf-8")  # the 3.10 fallback parser skips sub-tables
    assert '[mcp_servers.engram.tools.search_knowledge]\napproval_mode = "approve"' in text


def test_doctor_names_settings_a_client_env_block_misses(home, monkeypatch):
    tools = [
        {"name": "Claude Desktop", "status": "configured", "format": "json", "config_path": "cfg.json",
         "servers": {"engram": {"env": {"ENGRAM_DIR": "x"}}}},
        {"name": "Codex", "status": "configured", "format": "toml", "config_path": "config.toml",
         "servers": {"engram": {"env": {"ENGRAM_APPROVAL": "strict", "ENGRAM_REVIEW_QUEUE_MAX": "30"}}}},
    ]
    user_env = {"ENGRAM_REVIEW_QUEUE_MAX": "30", "ENGRAM_RECONCILE": "0"}

    findings = dict((t["name"], m) for t, m in doctor._client_env_findings(tools, strict=True, user_env=user_env))

    assert findings["Claude Desktop"] == {
        "ENGRAM_APPROVAL": "strict", "ENGRAM_RECONCILE": "0", "ENGRAM_REVIEW_QUEUE_MAX": "30",
    }
    assert findings["Codex"] == {"ENGRAM_RECONCILE": "0"}


# ---------------------------------------------------------------------------
# 5. review apply preview reports what is already done
# ---------------------------------------------------------------------------


def _cli(monkeypatch, capsys, *argv: str) -> dict:
    monkeypatch.setattr(sys, "argv", ["engram", "review", *argv])
    with pytest.raises(SystemExit):
        W.main()
    out = capsys.readouterr().out
    return json.loads(out[out.index("{"):])


def test_rerun_preview_shows_nothing_left_after_apply(home, tmp_path, monkeypatch, capsys):
    store = tmp_path / "store"
    eng = Engram(root=store)
    draft = eng.add_lesson("session log that is not a memory", domain="t", tier="staging")
    kept = eng.add_lesson("never install tools on C", domain="feedback")
    book = eng.add_playbook({"title": "old release flow", "description": "superseded",
                             "steps": [{"action": "build"}, {"action": "publish"}]})
    marks = tmp_path / "marks.json"
    marks.write_text(json.dumps([
        {"id": draft["id"], "mark": "reject"},
        {"id": kept["id"], "mark": "edit-type:rule"},
        {"id": book["id"], "mark": "retire"},
    ]), encoding="utf-8")

    first = _cli(monkeypatch, capsys, "apply", str(marks))
    assert first["counts"]["pending"] == 3
    applied = _cli(monkeypatch, capsys, "apply", str(marks), "--operator", "owner", "--yes")
    assert applied["status"] == "applied"
    after_apply = _snapshot(store)

    again = _cli(monkeypatch, capsys, "apply", str(marks))

    counts = again["counts"]
    assert counts["pending"] == 0
    assert counts["planned"] == 0 and counts["failed"] == 0
    assert counts["edit_type_already_applied"] == 1 and counts["lifecycle_already_applied"] == 1
    assert [i["status"] for i in again["items"]] == ["already_applied"]
    assert _snapshot(store) == after_apply

    # Applying the same file again writes no new versions.
    _cli(monkeypatch, capsys, "apply", str(marks), "--operator", "owner", "--yes")
    lessons = json.loads((store / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    assert [r["domain"] for r in lessons if r["id"] == kept["id"]] == ["feedback,type:rule"]
    assert not any(str(r.get("id", "")).endswith("-prev-v2") for r in lessons)


# ---------------------------------------------------------------------------
# 6. doctor without --fix writes nothing into the store
# ---------------------------------------------------------------------------


def test_doctor_without_fix_leaves_the_store_byte_for_byte(home, tmp_path, monkeypatch):
    store = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    monkeypatch.delenv("ENGRAM_TEST", raising=False)  # the stamps doctor must not write
    monkeypatch.delenv("ENGRAM_NO_UPDATE_CHECK", raising=False)
    for marker in update_check._CI_ENV_MARKERS:
        monkeypatch.delenv(marker, raising=False)
    monkeypatch.setattr(update_check, "_fetch_latest_from_pypi", lambda: "0.0.1")
    eng = Engram(root=store)
    eng.add_lesson("doctor probe lesson", domain="type:lesson")
    eng.save_agent_context(tool="probe", content="checkpoint", project_folder=str(tmp_path))
    eng.refresh_quick_context()
    del eng
    # A configured client, so doctor runs its whole flow (no early "no tools" exit).
    mcp_json = home / ".claude" / ".mcp.json"
    mcp_json.parent.mkdir(parents=True)
    mcp_json.write_text(json.dumps({"mcpServers": {"engram": {
        "command": sys.executable, "args": ["-m", "piia_engram.mcp_server"],
        "env": {"ENGRAM_DIR": str(store)}}}}), encoding="utf-8")
    # Doctor's own (fresh) import of the MCP server module counts too; the module
    # it creates is read-only, so put the suite's module back afterwards.
    import piia_engram

    saved = sys.modules.pop("piia_engram.mcp_server", None)
    before = _snapshot(store)

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            doctor.run_doctor(fix=False)
    finally:
        sys.modules.pop("piia_engram.mcp_server", None)
        if saved is not None:
            sys.modules["piia_engram.mcp_server"] = saved
            piia_engram.mcp_server = saved
        elif hasattr(piia_engram, "mcp_server"):
            delattr(piia_engram, "mcp_server")

    out = buf.getvalue()
    assert _snapshot(store) == before
    assert "Functional Checks" in out and "Version:" in out and "MCP Client Env" in out
    assert (tmp_path / "cache" / ".update_check.json").is_file()


def test_update_cache_lives_outside_the_store(home, tmp_path):
    assert update_check._cache_path() == tmp_path / "cache" / ".update_check.json"
    assert tmp_path / "store" not in update_check._cache_path().parents


# ---------------------------------------------------------------------------
# 7. memory import keeps the whole body
# ---------------------------------------------------------------------------

_LONG_MEMORY = (
    "---\nname: failure-path-message\n"
    "description: A completion message on the failure path must not claim success\n"
    "type: feedback\n---\n\n"
    "When a step fails, the final message\nmust say it failed.\n\n"
    + "**Why:** " + ("the Owner reads only the last line; " * 40) + "\n\n"
    + "**How to apply:** " + ("print the failing step and its exit code; " * 30) + "END-OF-BODY\n"
)


def test_reconcile_imports_the_whole_body_with_the_description_as_summary(tmp_path):
    eng = Engram(root=tmp_path / "store")
    mem = tmp_path / "claude" / "projects" / "p" / "memory"
    mem.mkdir(parents=True)
    (mem / "failure.md").write_text(_LONG_MEMORY, encoding="utf-8")
    eng._CLAUDE_MEMORY_GLOBS = [str(mem / "*.md")]

    assert eng.reconcile_memories()["imported"] == 1

    (row,) = eng.get_lessons(limit=None, _update_access=False)
    assert row["summary"] == "A completion message on the failure path must not claim success"
    assert "END-OF-BODY" in row["detail"]
    assert "**How to apply:**" in row["detail"]
    assert len(row["detail"]) > 1500


def test_reconcile_skips_a_memory_rejected_under_its_old_summary(tmp_path):
    from piia_engram import tombstones

    eng = Engram(root=tmp_path / "store")
    tombstones.append(eng.root, "lesson", {"id": "old1", "summary": "When a step fails, the final message"},
                      via="test")
    mem = tmp_path / "claude" / "projects" / "p" / "memory"
    mem.mkdir(parents=True)
    (mem / "failure.md").write_text(_LONG_MEMORY, encoding="utf-8")
    eng._CLAUDE_MEMORY_GLOBS = [str(mem / "*.md")]

    result = eng.reconcile_memories()
    assert result["imported"] == 0
    assert result["rejected_under_old_summary"] == 1
    assert eng.get_lessons(limit=None, _update_access=False) == []


def test_old_summary_rejection_is_scoped_like_the_insert_guard(tmp_path):
    from piia_engram import tombstones
    from piia_engram.reconcile import _rejected_before
    from piia_engram.storage import _project_id

    root = tmp_path / "store"
    Engram(root=root)
    line = "When a step fails, the final message"
    project_a, project_b = str(tmp_path / "proj-a"), str(tmp_path / "proj-b")
    tombstones.append(root, "lesson", {"id": "a1", "summary": line, "project_id": _project_id(project_a)},
                      via="test")

    assert _rejected_before(root, line, project_folder=project_a)
    assert not _rejected_before(root, line, project_folder=project_b)
    assert not _rejected_before(root, line)  # global scope
    tombstones.append(root, "lesson", {"id": "g1", "summary": "Why:"}, via="test")
    assert not _rejected_before(root, "Why:")  # too short to identify a memory


def test_an_env_var_can_never_make_the_mcp_server_read_only(home, tmp_path, monkeypatch):
    import piia_engram
    from piia_engram import mcp_server

    monkeypatch.setenv("ENGRAM_IMPORT_READ_ONLY", "1")  # e.g. carried in a client env block
    eng, err = mcp_server._init_engram(tmp_path / "store")

    assert err is None and eng._read_only is False
    assert getattr(piia_engram, "_MCP_IMPORT_READ_ONLY", False) is False


def test_doctor_resets_its_read_only_import_switch(home, tmp_path):
    import piia_engram

    Engram(root=tmp_path / "store")
    with redirect_stdout(io.StringIO()):
        doctor._run_functional_checks(fix=False)

    assert piia_engram._MCP_IMPORT_READ_ONLY is False


def test_overlong_detail_is_marked_never_cut_silently():
    from piia_engram.reconcile import _RECONCILE_DETAIL_MAX, _bounded_detail

    text = "x" * (_RECONCILE_DETAIL_MAX + 50)
    out = _bounded_detail(text, source=text)

    assert out.startswith("x" * _RECONCILE_DETAIL_MAX)
    assert f"[truncated: kept {_RECONCILE_DETAIL_MAX} of {len(text)} characters; source sha256 " in out
