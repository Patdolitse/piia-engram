"""Activation loop: hooks reach real core APIs; second session sees first session."""
import json
import re
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parents[1] / "src" / "piia_engram" / "hooks"

# A summary that reliably clears the extraction quality gate (evidence signal
# for the lesson, explicit choice-vs-alternative signal for the decision).
# Probed 2026-08-06: yields 3 staged lessons + 1 staged decision.
QUALITY_SUMMARY = """本次会话经验总结：

教训：发布 hotfix 时 CI 的 Release commit guard 连续拦了三轮。经过实际验证发现：\
证据文件必须在版本变更提交的 HEAD tree 里、marker 值必须严格等于 passed 或 n/a、\
且文件必须保持 marker-only 格式。验证方式：本地先跑 check_release_gate 和 \
check_release_preflight 两个脚本再推送，实测可以把三轮 CI 往返压缩成零轮，节省约三十分钟。

决策：热修发布路径选择了线性 cherry-pick 分支 + PR 的方案，而不是直接 merge commit 推 main。\
原因：main 是保护分支禁止 merge commit 且要求七项状态检查，直接推送会被拒；\
对比过的备选方案是临时解除分支保护，被否决因为破坏审计链。
"""


def _fresh_root(tmp_path, monkeypatch):
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "0")
    return root


def _load(root: Path, name: str) -> list:
    p = root / "knowledge" / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []


def test_hooks_only_call_real_engram_methods():
    """Reflection guard: every `engram.<name>(` in hooks/ must exist on Engram.

    Regression class: auto_save_on_stop called engram.wrap_up_session() — a
    method that only ever existed as MCP-tool-layer orchestration — and the
    AttributeError was silently swallowed into the failure log on every
    substantial session, so structured extraction never ran via the hook.
    """
    from piia_engram.core import Engram

    referenced: dict[str, set[str]] = {}
    for py in HOOK_DIR.glob("*.py"):
        names = set(
            re.findall(r"\bengram\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", py.read_text(encoding="utf-8"))
        )
        if names:
            referenced[py.name] = names
    assert referenced, "expected at least one hook to call engram methods"
    missing = sorted(
        f"{fname}: {name}"
        for fname, names in referenced.items()
        for name in names
        if not hasattr(Engram, name)
    )
    assert missing == [], f"hooks reference nonexistent Engram methods: {missing}"


def test_hook_style_extraction_lands_staged_only(tmp_path, monkeypatch):
    """The Stop-hook flush path is unsupervised background writeback: its
    extraction must land in staging, never auto-verified."""
    root = _fresh_root(tmp_path, monkeypatch)
    from piia_engram.core import Engram

    engram = Engram(root=root)
    result = engram.extract_session_insights(
        QUALITY_SUMMARY,
        source_tool="claude_code",
        source_ref="hook-test-session",
        force_staging=True,
        project_folder=str(tmp_path),
    )

    assert result.get("saved_lessons", 0) + result.get("saved_decisions", 0) >= 1, (
        f"quality gate rejected the pinned fixture: {result}"
    )
    for name in ("lessons", "decisions"):
        for item in _load(root, name):
            state = item.get("memory_state") or item.get("tier")
            assert state == "staging", (
                f"unsupervised extraction must stage every item; {name} entry "
                f"has state {state!r}"
            )


def test_inject_hook_path_bootstraps_fresh_store(tmp_path, monkeypatch):
    """A fresh store must get bootstrapped on the SessionStart hook path, same
    as the MCP get_resume_brief wrapper does (mirrors the hook's new guard)."""
    root = _fresh_root(tmp_path, monkeypatch)
    from piia_engram.bootstrap import needs_bootstrap, run_bootstrap
    from piia_engram.core import Engram

    engram = Engram(root=root)
    if needs_bootstrap(engram):
        run_bootstrap(engram)
    assert not needs_bootstrap(engram)  # marker written, idempotent
    assert (root / ".bootstrap_done").is_file()


def test_inject_hook_source_contains_bootstrap_guard():
    src = (HOOK_DIR / "auto_inject_resume_brief.py").read_text(encoding="utf-8")
    assert "needs_bootstrap" in src and "run_bootstrap" in src, (
        "SessionStart hook must trigger the same bootstrap as the MCP "
        "get_resume_brief wrapper; a fresh store with a discoverable CLAUDE.md "
        "should auto-import on the hook path too"
    )


def test_second_session_new_instance_sees_first_session(tmp_path, monkeypatch):
    """Session 1 writes via real entry points; session 2 = a brand-new Engram
    instance on the same root; recall must surface the memory."""
    root = _fresh_root(tmp_path, monkeypatch)
    from piia_engram.core import Engram

    # --- session 1 ---
    s1 = Engram(root=root)
    s1.add_lesson(
        "激活回环测试教训：第二次会话必须能看到第一次的记忆",
        domain="testing",
        source_tool="claude_code",
    )
    s1.save_agent_context(
        tool="claude_code",
        content="完成激活回环第一阶段，下一步验证第二会话召回。",
        project_folder=str(tmp_path),
    )
    del s1

    # --- session 2: fresh instance, same store ---
    s2 = Engram(root=root)
    hits = s2.search_knowledge("激活回环 第二次会话", scope="lessons", limit=5)
    assert hits["lessons"], "lesson saved in session 1 must be searchable in session 2"

    brief = s2.get_resume_brief(project_folder=str(tmp_path), token_budget=2000)
    md = brief.get("markdown", "")
    assert md, "second session must render a non-empty resume brief"
    assert "激活回环" in md or "第一阶段" in md, (
        "second session's resume brief must surface first-session traces"
    )


def test_second_process_sees_first_process_write(tmp_path):
    """Real process boundary: process 1 writes, process 2 recalls."""
    import os
    import subprocess
    import sys

    root = tmp_path / "store2"
    root.mkdir()
    env = dict(
        os.environ,
        ENGRAM_DIR=str(root),
        ENGRAM_AUDIT="0",
        PYTHONIOENCODING="utf-8",
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    write_code = (
        "from piia_engram.core import Engram;"
        "e = Engram();"
        "e.add_lesson('跨进程激活教训：进程一写入的记忆', domain='testing', source_tool='t')"
    )
    read_code = (
        "from piia_engram.core import Engram;"
        "r = Engram().search_knowledge('跨进程激活', scope='lessons', limit=3);"
        "print('FOUND' if r['lessons'] else 'MISSING')"
    )
    subprocess.run([sys.executable, "-c", write_code], env=env, check=True, timeout=120)
    out = subprocess.run(
        [sys.executable, "-c", read_code], env=env, check=True,
        timeout=120, capture_output=True, text=True,
    )
    assert "FOUND" in out.stdout


def test_quick_context_refresh_writes_snapshot(tmp_path, monkeypatch):
    root = _fresh_root(tmp_path, monkeypatch)
    from piia_engram.core import Engram

    engram = Engram(root=root)
    engram.add_lesson("刷新快照测试教训", domain="testing", source_tool="t")
    engram.refresh_quick_context()
    qc = root / "quick_context.md"
    assert qc.is_file() and qc.stat().st_size > 0


def test_stop_hook_source_refreshes_quick_context():
    """The Layer-1 cold-start snapshot must not go stale the moment a session
    ends: the Stop hook refreshes it after saving."""
    src = (HOOK_DIR / "auto_save_on_stop.py").read_text(encoding="utf-8")
    assert "refresh_quick_context" in src
