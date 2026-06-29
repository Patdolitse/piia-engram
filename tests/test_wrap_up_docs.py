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
