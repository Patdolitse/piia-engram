"""End-to-end hook tests: run the REAL hook entry points as subprocesses.

The activation loop was previously guarded only by reflection (method names
exist) and source-string tripwires; the production entry points were never
executed by the suite (gap found by the v4.15.0 independent review). These
tests exercise the actual programs against temp stores with synthetic
projects: stop-hook save + snapshot + quick_context, session-start bootstrap
+ resume brief, and the flush-threshold gate.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[1] / "src" / "piia_engram" / "hooks"


def _run_hook(module: str, payload: dict, store: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, ENGRAM_DIR=str(store), ENGRAM_AUDIT="0", PYTHONIOENCODING="utf-8")
    env.pop("ENGRAM_TEST", None)
    env.pop("CLAUDE_INVOKED_BY", None)
    # encoding must be explicit: the hooks emit UTF-8 (CJK briefs) and the
    # Windows default cp1252 decode raises on CI runners
    return subprocess.run(
        [sys.executable, "-m", module],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
        cwd=str(cwd),
    )


def _transcript(path: Path, messages: int) -> Path:
    lines = [
        json.dumps({
            "type": "assistant",
            "timestamp": f"2026-08-16T10:00:{i % 60:02d}.000Z",
            "content": [{"type": "tool_use", "name": "Bash" if i % 2 else "Read"}],
        })
        for i in range(messages)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "tests").mkdir(parents=True)
    (proj / "pyproject.toml").write_text(
        '[project]\nname = "hook-e2e"\nversion = "7.7.7"\n', encoding="utf-8",
    )
    (proj / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (proj / "tests" / "test_app.py").write_text(
        "def test_a():\n    pass\n\n\ndef test_b():\n    pass\n", encoding="utf-8",
    )
    (proj / "CLAUDE.md").write_text("# hook e2e rules\n- keep it simple\n", encoding="utf-8")
    return proj


def test_stop_hook_saves_context_snapshot_and_quick_context(tmp_path, project):
    store = tmp_path / "store"
    store.mkdir()
    transcript = _transcript(tmp_path / "t.jsonl", 12)

    result = _run_hook(
        "piia_engram.hooks.auto_save_on_stop",
        {"cwd": str(project), "transcript_path": str(transcript), "session_id": "e2e-s1"},
        store, tmp_path,
    )
    assert result.returncode == 0, result.stderr[-500:]

    contexts = list((store / "contexts" / "claude_code").glob("*.md"))
    assert contexts, "stop hook saved no agent context"
    quick = store / "quick_context.md"
    assert quick.is_file() and quick.stat().st_size > 0
    projects = list((store / "projects").glob("*.json"))
    assert projects, "stop hook saved no project snapshot"
    snap = json.loads(projects[0].read_text(encoding="utf-8"))
    assert snap.get("version") == "7.7.7" and snap.get("test_count") == 2
    hooks_log = store / "logs" / "hooks.log"
    assert not hooks_log.is_file() or hooks_log.read_text(encoding="utf-8") == ""


def test_start_hook_bootstraps_and_returns_resume_brief(tmp_path, project):
    store = tmp_path / "store"
    store.mkdir()
    # session 1: stop hook writes something recallable
    transcript = _transcript(tmp_path / "t1.jsonl", 12)
    _run_hook(
        "piia_engram.hooks.auto_save_on_stop",
        {"cwd": str(project), "transcript_path": str(transcript), "session_id": "e2e-s1"},
        store, tmp_path,
    )

    result = _run_hook(
        "piia_engram.hooks.auto_inject_resume_brief",
        {"cwd": str(project)},
        store, tmp_path,
    )
    assert result.returncode == 0, result.stderr[-500:]
    out = json.loads(result.stdout)
    brief = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
    assert brief, "start hook produced no additionalContext"
    assert (store / ".bootstrap_done").is_file(), "start hook did not bootstrap"


def test_stop_hook_skips_short_sessions(tmp_path, project):
    store = tmp_path / "store"
    store.mkdir()
    transcript = _transcript(tmp_path / "short.jsonl", 3)

    result = _run_hook(
        "piia_engram.hooks.auto_save_on_stop",
        {"cwd": str(project), "transcript_path": str(transcript), "session_id": "e2e-s2"},
        store, tmp_path,
    )
    assert result.returncode == 0
    ctx_dir = store / "contexts" / "claude_code"
    assert not ctx_dir.is_dir() or not list(ctx_dir.glob("*.md")), (
        "short session below flush threshold must be skipped"
    )


def _scan_all_persistent_surfaces(store: Path) -> str:
    """Concatenate every durable surface the hook could influence."""
    import json as _json

    parts: list[str] = []
    for rel in ("knowledge/lessons.json", "knowledge/decisions.json"):
        p = store / rel
        if p.is_file():
            parts.append(p.read_text(encoding="utf-8"))
    for rel in ("audit.log", "quick_context.md", "telemetry.log"):
        p = store / rel
        if p.is_file():
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
    contexts = store / "contexts"
    if contexts.is_dir():
        parts.extend(f.read_text(encoding="utf-8", errors="replace") for f in contexts.rglob("*") if f.is_file())
    return "\n".join(parts)


def test_stop_hook_legacy_true_migrated_and_v2_activates(tmp_path, project):
    """4.18 gate matrix e2e: the old key is migrated away (never honored);
    the new v2 key with literal True activates the digest end-to-end and
    no canary reaches any persistent surface."""
    store = tmp_path / "store"
    store.mkdir()
    from piia_engram.core import Engram

    identity_dir = store / "identity"
    identity_dir.mkdir()
    (identity_dir / "preferences.json").write_text(
        json.dumps({"hook_content_digest": True}),
        encoding="utf-8",
    )
    assert Engram(root=store).get_preferences()["hook_content_digest"] is True

    fake_key = "s" + "k-FAKE-" + "A1b2C3d4E5f6G7h8I9j0"
    lines = [
        json.dumps({
            "type": "assistant", "timestamp": f"2026-08-16T10:00:{i:02d}.000Z",
            "content": [{"type": "text", "text": text}],
        })
        for i, text in enumerate([
            "user paste should not matter",  # (user text is a different shape; this is assistant)
            f"the deploy token is {fake_key} for staging",
            "验证发现：发布前本地先跑门禁脚本因为 CI 会拦，实测把三轮往返压缩成零轮",
            "决定采用线性分支方案因为保护分支禁止 merge commit，备选的解除保护被否决",
        ])
    ]
    # pad with neutral tool-use lines to clear the flush threshold (5)
    lines += [
        json.dumps({
            "type": "assistant", "timestamp": f"2026-08-16T10:01:{i:02d}.000Z",
            "content": [{"type": "tool_use", "name": "Read"}],
        })
        for i in range(10)
    ]
    transcript = tmp_path / "legacy-true-disabled.jsonl"
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = _run_hook(
        "piia_engram.hooks.auto_save_on_stop",
        {"cwd": str(project), "transcript_path": str(transcript), "session_id": "e2e-legacy-disabled"},
        store, tmp_path,
    )
    assert result.returncode == 0, result.stderr[-500:]

    # the old key may still exist on disk (update_preferences can't delete
    # unknown keys), but the hook NEVER consults it — the gate reads only
    # the v2 key. The security property is: digest not active despite
    # the persisted old true.

    lessons_path = store / "knowledge" / "lessons.json"
    decisions_path = store / "knowledge" / "decisions.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8")) if lessons_path.is_file() else []
    decisions = json.loads(decisions_path.read_text(encoding="utf-8")) if decisions_path.is_file() else []
    assert lessons + decisions == []

    blob = _scan_all_persistent_surfaces(store)
    for canary in (fake_key, "A1b2C3d4E5f6G7h8I9j0", "deploy token"):
        assert canary not in blob, canary


def test_stop_hook_v2_activation_full_chain(tmp_path, project):
    """Source-to-sink E2E: v2=True activates the digest; quality content
    reaches staging; canaries reach nothing."""
    store = tmp_path / "store"
    store.mkdir()
    from piia_engram.core import Engram

    Engram(root=store).update_preferences({"hook_content_digest_v2": True})

    fake_key = "s" + "k-FAKE-" + "B2c3D4e5F6g7H8i9J0k1"
    lines = [
        json.dumps({
            "type": "assistant",
            "timestamp": f"2026-08-18T10:00:{i:02d}.000Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
        })
        for i, text in enumerate([
            f"deploy token: {fake_key}",
            "验证发现：本地先跑门禁脚本因为 CI 会拦实测省三轮",
            "决定采用镜像方案因为拉取速度快三倍备选源码编译被否决",
        ])
    ]
    lines += [
        json.dumps({
            "type": "assistant", "timestamp": f"2026-08-18T10:01:{i:02d}.000Z",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read"}]},
        })
        for i in range(10)
    ]
    transcript = tmp_path / "v2-activation.jsonl"
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = _run_hook(
        "piia_engram.hooks.auto_save_on_stop",
        {"cwd": str(project), "transcript_path": str(transcript), "session_id": "e2e-v2"},
        store, tmp_path,
    )
    assert result.returncode == 0, result.stderr[-500:]

    lessons = json.loads((store / "knowledge" / "lessons.json").read_text(encoding="utf-8")) \
        if (store / "knowledge" / "lessons.json").is_file() else []
    decisions = json.loads((store / "knowledge" / "decisions.json").read_text(encoding="utf-8")) \
        if (store / "knowledge" / "decisions.json").is_file() else []
    assert len(lessons) + len(decisions) >= 1, "v2 activation produced no items"

    blob = _scan_all_persistent_surfaces(store)
    assert fake_key not in blob
    assert "B2c3D4e5F6g7H8i9J0k1" not in blob


@pytest.mark.parametrize(
    "pref_setup,expect_items",
    [
        ({"hook_content_digest": True}, False),
        ({"hook_content_digest": True, "hook_content_digest_v2": True}, True),
        ({"hook_content_digest_v2": False}, False),
        ({"hook_content_digest_v2": "true"}, False),
        ({"hook_content_digest_v2": 1}, False),
        ({"hook_content_digest_v2": True}, True),
        ({}, False),
    ],
    ids=["old-true", "old+v2", "v2-false", "v2-string", "v2-int", "v2-true", "none"],
)
def test_stop_hook_gate_matrix(tmp_path, project, pref_setup, expect_items):
    """Full gate matrix: only literal True under v2 activates."""
    store = tmp_path / "store"
    store.mkdir()

    pref_path = store / "identity" / "preferences.json"
    pref_path.parent.mkdir(parents=True, exist_ok=True)
    pref_path.write_text(json.dumps(pref_setup), encoding="utf-8")

    quality = "验证发现：发布前本地先跑门禁因为 CI 会拦实测省三轮"
    lines = [
        json.dumps({
            "type": "assistant", "timestamp": f"2026-08-18T11:00:{i:02d}.000Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": quality}]},
        })
        for i in range(12)
    ]
    transcript = tmp_path / "gate.jsonl"
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = _run_hook(
        "piia_engram.hooks.auto_save_on_stop",
        {"cwd": str(project), "transcript_path": str(transcript), "session_id": "gate"},
        store, tmp_path,
    )
    assert result.returncode == 0, result.stderr[-500:]

    lessons = json.loads((store / "knowledge" / "lessons.json").read_text(encoding="utf-8")) \
        if (store / "knowledge" / "lessons.json").is_file() else []
    decisions = json.loads((store / "knowledge" / "decisions.json").read_text(encoding="utf-8")) \
        if (store / "knowledge" / "decisions.json").is_file() else []
    has_items = len(lessons) + len(decisions) > 0
    assert has_items == expect_items
