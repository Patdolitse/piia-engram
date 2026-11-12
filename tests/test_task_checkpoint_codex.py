"""Codex rollout task-checkpoint parity; every rollout and store is synthetic.

Covers the opt-in watcher path (codex_adapter -> watcher core ->
save_agent_context) and its read-side contract: a five-label block in the last
completed assistant message of a segment produces the same task_checkpoint
digest as the Claude Stop hook, and get_resume_brief carries all five fields.
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from piia_engram.resume_selection import select_resume_fields
from piia_engram.watcher import claude_code_adapter, codex_adapter, core

OPT_IN = "ENGRAM_HOOK_TASK_CHECKPOINT_V1"
VALUES = ("Ship the sample parity feature", "Keep the sample budget intact",
          "The sample reviewer is offline", "The sample check failed",
          "Run the sample check again")
GOAL, CONSTRAINT, RISK, FAILURE, NEXT = VALUES
BLOCK = ("Wrap-up of the synthetic turn.\n\n" +
         "\n".join(f"{label}: {value}" for label, value in zip(
             ("Goal", "Constraint", "Risk", "Last failure reason", "Next"), VALUES)))
PROJECT = "E:\\Synthetic Project"
UUID = "0000aaaa-bbbb-cccc-dddd-eeeeffff0001"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram-store"))
    monkeypatch.setenv(codex_adapter.ENV_SESSIONS_ROOT, str(tmp_path / "codex-sessions"))
    monkeypatch.setenv(claude_code_adapter.ENV_PROJECTS_ROOT, str(tmp_path / "claude-projects"))
    monkeypatch.setenv(claude_code_adapter.ENV_SETTINGS_FILE, str(tmp_path / "claude-settings.json"))
    monkeypatch.delenv("ENGRAM_WATCHER_DEBOUNCE", raising=False)
    monkeypatch.delenv("ENGRAM_WATCHER_WRITEBACK", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv(OPT_IN, raising=False)


@pytest.fixture()
def opt_in(monkeypatch):
    monkeypatch.setenv(OPT_IN, "1")


@pytest.fixture()
def fake_engram(monkeypatch):
    calls: list[dict] = []

    class FakeEngram:
        def save_agent_context(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True}

    monkeypatch.setattr("piia_engram.core.Engram", FakeEngram)
    return SimpleNamespace(calls=calls)


def _day_dir(root: Path) -> Path:
    when = datetime.now()
    d = root / f"{when.year:04d}" / f"{when.month:02d}" / f"{when.day:02d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _completed_line(role: str, text: str) -> str:
    return json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "UserMessage" if role == "user" else "AgentMessage",
        "content": [{"type": "text" if role == "user" else "Text", "text": text}],
    }}}, ensure_ascii=False)


def _response_line(role: str, text: str) -> str:
    return json.dumps({"type": "response_item", "payload": {
        "type": "message", "role": role,
        "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
    }}, ensure_ascii=False)


def _legacy_line(role: str, text: str) -> str:
    return json.dumps({"type": "event_msg", "payload": {
        "type": "user_message" if role == "user" else "agent_message", "message": text}},
        ensure_ascii=False)


def _rollout(lines: list[str], *, uuid: str = UUID) -> Path:
    head = json.dumps({"type": "session_meta", "payload": {"id": uuid, "cwd": PROJECT}})
    path = _day_dir(codex_adapter.sessions_root()) / f"rollout-2026-11-13T10-00-00-{uuid}.jsonl"
    path.write_text("\n".join([head, *lines]) + "\n", encoding="utf-8")
    return path


def _captured_task():
    from piia_engram.task_checkpoint import EVENTS, ROLLOUT_EVENT, parse_text
    task, reason = parse_text(BLOCK, capture_event=ROLLOUT_EVENT,
                              allowed_events=EVENTS | {ROLLOUT_EVENT})
    assert reason == "captured" and task is not None
    return task


def _sidecar_path():
    from piia_engram.core import Engram
    return Engram()._session_digest_path("codex", UUID)


def _sidecar():
    return json.loads(_sidecar_path().read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# adapter: which message may become a checkpoint
# ---------------------------------------------------------------------------


def test_valid_block_in_last_completed_assistant_yields_five_fields(opt_in):
    path = _rollout([_completed_line("user", "synthetic request"),
                     _completed_line("assistant", BLOCK)])
    parsed = codex_adapter.parse(path)
    task = parsed["task_checkpoint"]
    assert tuple(task[field] for field in
                 ("goal", "constraint", "blocker", "last_failure_reason", "next_action")) == VALUES
    assert task["schema"] == "task_checkpoint.v1"
    assert task["capture_event"] == "CodexRollout"
    assert task["omissions"] == {}


def test_missing_label_yields_no_checkpoint(opt_in):
    broken = "\n".join(line for line in BLOCK.splitlines() if not line.startswith("Risk:"))
    path = _rollout([_completed_line("assistant", broken)])
    assert "task_checkpoint" not in codex_adapter.parse(path)


def test_fenced_block_yields_no_checkpoint(opt_in):
    fenced = "Wrap-up.\n\n```\n" + BLOCK.split("\n\n", 1)[1] + "\n```"
    path = _rollout([_completed_line("assistant", fenced)])
    assert "task_checkpoint" not in codex_adapter.parse(path)


@pytest.mark.parametrize("lines", [
    # Mirror stream only: a response_item duplicate is never a candidate.
    [_response_line("assistant", BLOCK)],
    # Mirror carries the block, but the last completed assistant is plain.
    [_response_line("assistant", BLOCK), _completed_line("assistant", "plain close")],
    # A completed user message is never parsed.
    [_completed_line("assistant", "plain answer"), _completed_line("user", BLOCK)],
    # Legacy agent_message events are not candidates either.
    [_legacy_line("assistant", BLOCK)],
])
def test_block_outside_completed_assistant_stream_yields_no_checkpoint(opt_in, lines):
    assert "task_checkpoint" not in codex_adapter.parse(_rollout(lines))


def test_opt_in_off_yields_no_checkpoint(monkeypatch):
    monkeypatch.setenv(OPT_IN, "0")
    path = _rollout([_completed_line("assistant", BLOCK)])
    assert "task_checkpoint" not in codex_adapter.parse(path)


def test_only_the_last_completed_assistant_is_a_candidate(opt_in):
    path = _rollout([_completed_line("assistant", BLOCK),
                     _completed_line("assistant", "plain final words")])
    assert "task_checkpoint" not in codex_adapter.parse(path)


# ---------------------------------------------------------------------------
# validation gate
# ---------------------------------------------------------------------------


def test_parse_text_and_validate_refuse_rollout_event_without_explicit_allow():
    from piia_engram.task_checkpoint import EVENTS, ROLLOUT_EVENT, parse_text, validate
    assert parse_text(BLOCK, capture_event=ROLLOUT_EVENT) == (None, "event-refused")
    task = _captured_task()
    with pytest.raises(ValueError):
        validate(task)
    assert validate(task, allowed_events=EVENTS | {ROLLOUT_EVENT}) == task


def test_validate_event_universe_stays_closed():
    from piia_engram.task_checkpoint import validate
    task = dict(_captured_task(), capture_event="SessionEnd")
    for allowed in ({"SessionEnd"}, {"Stop", "PreCompact", "CodexRollout", "SessionEnd"}):
        with pytest.raises(ValueError):
            validate(task, allowed_events=allowed)


def test_claude_spool_still_rejects_rollout_checkpoint():
    from piia_engram.hooks import _processor
    from piia_engram.task_checkpoint import parse_text
    payload = {"transcript_path": "E:\\synthetic\\transcript.jsonl"}
    stop_task = parse_text(BLOCK)[0]  # control: a hook capture is accepted
    assert _processor.validate_payload({"kind": "claude_stop", "payload": {
        **payload, "task_checkpoint": stop_task}}) is None
    with pytest.raises(ValueError):
        _processor.validate_payload({"kind": "claude_stop", "payload": {
            **payload, "task_checkpoint": _captured_task()}})


# ---------------------------------------------------------------------------
# read side: sidecar survives selection, unknown events do not
# ---------------------------------------------------------------------------


def _handoff_digest(task):
    return {
        "schema": "session_digest.v1",
        "goal": task["goal"] or "",
        "next_actions": [task["next_action"]] if task["next_action"] else [],
        "risks": [task["blocker"]] if task["blocker"] else [],
        "completed": [], "decisions": [], "lessons": [],
        "source": {"tool": "codex", "session_ref": "synthetic",
                   "capture_event": task["capture_event"], "captured_at": task["captured_at"]},
        "task_checkpoint": task,
    }


def test_select_resume_fields_accepts_rollout_and_discards_unknown_events():
    handoff = {"current_focus": "unknown", "last_completed": [],
               "next_actions": [], "blocked_on": []}
    freshness = {"authoritative_source": "session_digest"}
    rollout, _, _ = select_resume_fields(
        dict(handoff), dict(freshness), {}, [_handoff_digest(_captured_task())], 2000)
    assert rollout["current_focus"] == GOAL
    assert rollout["next_actions"] == [NEXT]
    assert rollout["blocked_on"] == [RISK]
    assert rollout["last_failure_reason"] == FAILURE
    unknown = dict(_captured_task(), capture_event="SessionEnd")
    stale, constraints, _ = select_resume_fields(
        dict(handoff), dict(freshness), {}, [_handoff_digest(unknown)], 2000)
    assert "last_failure_reason" not in stale
    assert stale["blocked_on"] == []
    assert constraints == []
    assert stale["current_focus"] == NEXT  # heuristic fallback, not the goal


# ---------------------------------------------------------------------------
# watcher wiring and save_agent_context attachment
# ---------------------------------------------------------------------------


def test_scan_passes_checkpoint_to_save_agent_context(fake_engram, opt_in, monkeypatch):
    monkeypatch.setenv("ENGRAM_WATCHER_DEBOUNCE", "0")
    path = _rollout([_completed_line("user", "synthetic request"),
                     _completed_line("assistant", BLOCK)])
    core.scan_once(["codex"], baseline_existing=False)
    assert fake_engram.calls[0]["task_checkpoint"]["goal"] == GOAL
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_completed_line("assistant", "plain follow-up") + "\n")
    core.scan_once(["codex"], baseline_existing=False)
    assert fake_engram.calls[-1]["task_checkpoint"] is None  # no block -> none


def test_save_agent_context_attaches_checkpoint_even_without_signal(tmp_path, opt_in):
    from piia_engram.core import Engram
    engram = Engram()
    task = _captured_task()
    plain = "[assistant] plain synthetic words without heuristic labels"
    engram.save_agent_context(tool="codex", content=plain, session_id=UUID,
                              project_folder=PROJECT)
    assert not _sidecar_path().exists()  # control: no heuristic signal, no sidecar
    engram.save_agent_context(tool="codex", content=plain, session_id=UUID,
                              project_folder=PROJECT, task_checkpoint=task)
    digest = _sidecar()
    assert digest["task_checkpoint"] == task
    assert digest["goal"] == GOAL
    assert digest["next_actions"] == [NEXT]
    assert digest["risks"] == [RISK]
    assert digest["decisions"] == digest["lessons"] == []
    assert digest["source"]["capture_event"] == "CodexRollout"
    assert digest["source"]["captured_at"] == task["captured_at"]


def test_save_agent_context_rejects_invalid_checkpoint_before_writing(opt_in):
    from piia_engram.core import Engram
    engram = Engram()
    with pytest.raises(ValueError):
        engram.save_agent_context(tool="codex", content="synthetic", session_id=UUID,
                                  project_folder=PROJECT, task_checkpoint={"bogus": True})
    assert not (Path(engram.root) / "contexts" / "codex" / f"{UUID}.md").exists()


def test_mcp_surface_cannot_supply_checkpoint():
    import asyncio

    from piia_engram import mcp_server
    assert "task_checkpoint" not in inspect.signature(
        mcp_server.save_agent_context).parameters
    with pytest.raises(TypeError):
        asyncio.run(mcp_server.save_agent_context(
            tool="codex", content="synthetic",
            task_checkpoint={"schema": "task_checkpoint.v1"}))


# ---------------------------------------------------------------------------
# end to end: rollout -> scan_once -> get_resume_brief
# ---------------------------------------------------------------------------


def _handoff_section(markdown: str) -> str:
    start = markdown.index("## 30-second handoff")
    return markdown[start:markdown.index("##", start + 5)]


def test_scan_once_to_resume_brief_carries_all_five_fields(opt_in, monkeypatch):
    monkeypatch.setenv("ENGRAM_WATCHER_DEBOUNCE", "0")
    _rollout([_completed_line("user", "synthetic request"),
              _completed_line("assistant", BLOCK)])
    assert core.scan_once(["codex"], baseline_existing=False)["saved"] == 1
    from piia_engram.core import Engram
    brief = Engram().get_resume_brief(project_folder=PROJECT, token_budget=2000)
    section = _handoff_section(brief["markdown"])
    for value in VALUES:
        assert value in section
    assert brief["handoff_meta"]["last_failure_reason"] == FAILURE
    assert brief["freshness"]["authoritative_source"] == "session_digest"
    digest = _sidecar()
    assert digest["task_checkpoint"]["goal"] == GOAL


def test_later_segment_without_block_saves_as_today(opt_in, monkeypatch):
    monkeypatch.setenv("ENGRAM_WATCHER_DEBOUNCE", "0")
    path = _rollout([_completed_line("user", "synthetic request"),
                     _completed_line("assistant", BLOCK)])
    core.scan_once(["codex"], baseline_existing=False)
    assert "task_checkpoint" in _sidecar()
    # The later segment carries heuristic signal (a decision sentence) but no
    # block, so its save behaves exactly as before the feature: heuristic
    # digest and no checkpoint attached.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_completed_line("user", "next synthetic request") + "\n")
        handle.write(_completed_line(
            "assistant", "We decided to rename the sample helper for clarity.") + "\n")
    assert core.scan_once(["codex"], baseline_existing=False)["saved"] == 1
    digest = _sidecar()  # the heuristic digest replaced the checkpoint sidecar
    assert "task_checkpoint" not in digest
    assert "capture_event" not in digest["source"]
    assert "captured_at" not in digest["source"]
    assert digest["decisions"]  # heuristic signal really was present
    session_md = _sidecar_path().with_name(f"{UUID}.md")
    assert "rename the sample helper" in session_md.read_text(encoding="utf-8")
