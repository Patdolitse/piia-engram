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
