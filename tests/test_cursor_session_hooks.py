"""Tests for the Cursor session hooks (inject / save) and their shared helpers.

Covers src/piia_engram/hooks/_cursor_payload.py,
cursor_inject_resume_brief.py and cursor_save_on_stop.py.

Isolation rules: ENGRAM_DIR always points into tmp_path, and the Engram core
class is replaced by a stub, so no test ever touches a real store.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from piia_engram.hooks import _cursor_payload as payload_mod
from piia_engram.hooks import cursor_inject_resume_brief as inject_mod
from piia_engram.hooks import cursor_save_on_stop as save_mod


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path: Path):
    """Every test gets a throwaway ENGRAM_DIR and a clean hook-env slate."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram-state"))
    for var in (
        "ENGRAM_HOOK_DEBUG",
        "ENGRAM_CURSOR_SAVE_DEBOUNCE",
        "ENGRAM_CURSOR_INJECT_ACTIVE",
        "ENGRAM_CURSOR_SAVE_ACTIVE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "argv", ["cursor-hook-test"])


@pytest.fixture()
def fake_engram(monkeypatch):
    """Replace piia_engram.core.Engram with a recording stub."""
    calls: list[tuple[str, dict]] = []

    class FakeEngram:
        markdown = "# 接续简报\n上次会话进展……"
        raise_on: set[str] = set()

        def get_resume_brief(self, **kwargs):
            calls.append(("get_resume_brief", kwargs))
            if "get_resume_brief" in self.raise_on:
                raise RuntimeError("boom")
            return {"markdown": self.markdown}

        def save_agent_context(self, **kwargs):
            calls.append(("save_agent_context", kwargs))
            if "save_agent_context" in self.raise_on:
                raise RuntimeError("boom")
            return {"ok": True}

        def wrap_up_session(self, **kwargs):  # must never be hit by these hooks
            calls.append(("wrap_up_session", kwargs))

        def extract_session_insights(self, *args, **kwargs):  # ditto
            calls.append(("extract_session_insights", kwargs))

    monkeypatch.setattr("piia_engram.core.Engram", FakeEngram)
    return SimpleNamespace(cls=FakeEngram, calls=calls)


def _stdin(monkeypatch, payload) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


def _calls(fake, name: str) -> list[dict]:
    return [kwargs for method, kwargs in fake.calls if method == name]


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def test_apply_argv_env_promotes_pairs_with_setdefault(monkeypatch):
    monkeypatch.delenv("X_CURSOR_HOOK_A", raising=False)
    monkeypatch.setenv("X_CURSOR_HOOK_B", "keep-me")
    payload_mod.apply_argv_env(
        ["--env", "X_CURSOR_HOOK_A=1", "--env", "X_CURSOR_HOOK_B=clobber", "--env", "=bad"]
    )
    import os

    assert os.environ["X_CURSOR_HOOK_A"] == "1"
    assert os.environ["X_CURSOR_HOOK_B"] == "keep-me"  # setdefault semantics
    monkeypatch.delenv("X_CURSOR_HOOK_A", raising=False)


def test_parse_event():
    assert payload_mod.parse_event(["--event", "sessionEnd"]) == "sessionEnd"
    assert payload_mod.parse_event(["--env", "A=1"]) == ""
    assert payload_mod.parse_event([]) == ""


def test_coerce_text_shapes():
    assert payload_mod.coerce_text("hi") == "hi"
    assert payload_mod.coerce_text(["a", {"text": "b"}, {"content": "c"}, 7]) == "a\nb\nc"
    assert payload_mod.coerce_text({"content": "inner"}) == "inner"
    assert payload_mod.coerce_text(42) == ""


def test_extract_summary_field_priority_and_tail_truncation():
    hook_input = {"summary": "S" * 50, "text": "should-not-win"}
    assert payload_mod.extract_summary(hook_input, 10) == "S" * 10

    tail = payload_mod.extract_summary({"content": "abcdef"}, 3)
    assert tail == "def"  # keeps the tail, not the head


def test_extract_summary_transcript_fallback(tmp_path: Path):
    transcript = tmp_path / "t.jsonl"
    lines = [
        json.dumps({"summary": "第一段"}, ensure_ascii=False),
        json.dumps({"text": "second"}, ensure_ascii=False),
        "plain non-json line",
        json.dumps({"irrelevant": True}),
    ]
    transcript.write_text("\n".join(lines), encoding="utf-8")

    out = payload_mod.extract_summary({"transcript_path": str(transcript)}, 4000)

    assert "第一段" in out
    assert "second" in out
    assert "plain non-json line" in out


def test_extract_summary_missing_transcript_is_empty():
    assert payload_mod.extract_summary({"transcript_path": "Z:/nope/missing.jsonl"}, 100) == ""


def test_extract_project_folder_candidates():
    assert payload_mod.extract_project_folder({"workspace_roots": [{"path": "/w1"}]}) == "/w1"
    assert payload_mod.extract_project_folder({"workspace_roots": ["/w2"]}) == "/w2"
    assert payload_mod.extract_project_folder({"cwd": "/w3"}) == "/w3"
    assert payload_mod.extract_project_folder({"workspace_path": "/w4"}) == "/w4"
    assert payload_mod.extract_project_folder({}) == ""


def test_extract_session_id_candidates():
    assert payload_mod.extract_session_id({"conversation_id": "c-1"}) == "c-1"
    assert payload_mod.extract_session_id({"sessionId": "s-2"}) == "s-2"
    assert payload_mod.extract_session_id({"chat_id": 99}) == "99"
    assert payload_mod.extract_session_id({}) == ""


def test_recently_saved_and_mark_saved_roundtrip():
    assert payload_mod.recently_saved("k1", 10) is False  # nothing recorded yet
    payload_mod.mark_saved("k1")
    assert payload_mod.recently_saved("k1", 10) is True
    assert payload_mod.recently_saved("k1", 0) is False  # window 0 disables
    assert payload_mod.recently_saved("other", 10) is False


def test_recently_saved_tolerates_corrupt_state_file():
    state_file = payload_mod.state_dir() / "cursor_save_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("{not json", encoding="utf-8")

    assert payload_mod.recently_saved("k1", 10) is False
    payload_mod.mark_saved("k1")  # must overwrite the corrupt file, not raise
    assert payload_mod.recently_saved("k1", 10) is True


def test_debug_log_only_when_enabled(monkeypatch):
    log_file = payload_mod.state_dir() / "cursor_hooks_debug.log"

    payload_mod.debug_log("stop", {"summary": "x"})
    assert not log_file.exists()

    monkeypatch.setenv("ENGRAM_HOOK_DEBUG", "1")
    payload_mod.debug_log("stop", {"summary": "很长" * 300, "n": 5, "obj": {"a": 1}})

    record = json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])
    assert record["event"] == "stop"
    assert sorted(record["keys"]) == ["n", "obj", "summary"]
    assert len(record["previews"]["summary"]) <= 200  # preview is capped


# ---------------------------------------------------------------------------
# cursor_inject_resume_brief
# ---------------------------------------------------------------------------


def test_inject_happy_path(monkeypatch, capsys, fake_engram):
    _stdin(monkeypatch, {"cwd": "E:/some/project"})

    assert inject_mod.main() == 0

    out = json.loads(capsys.readouterr().out)
    assert out["continue"] is True
    assert out["additional_context"] == fake_engram.cls.markdown
    assert out["hookSpecificOutput"]["additionalContext"] == fake_engram.cls.markdown

    briefs = _calls(fake_engram, "get_resume_brief")
    assert briefs == [{"project_folder": "E:/some/project", "token_budget": 1500}]


def test_inject_output_is_ascii_safe(monkeypatch, capsys, fake_engram):
    _stdin(monkeypatch, {"cwd": ""})

    inject_mod.main()

    raw = capsys.readouterr().out
    assert "简报" not in raw  # Chinese is escaped for codepage safety...
    assert json.loads(raw)["additional_context"] == fake_engram.cls.markdown  # ...losslessly


def test_inject_empty_brief_is_passthrough(monkeypatch, capsys, fake_engram):
    fake_engram.cls.markdown = "   "
    _stdin(monkeypatch, {"cwd": "/p"})

    assert inject_mod.main() == 0
    assert json.loads(capsys.readouterr().out) == {"continue": True}


def test_inject_engram_failure_is_passthrough(monkeypatch, capsys, fake_engram):
    fake_engram.cls.raise_on = {"get_resume_brief"}
    _stdin(monkeypatch, {"cwd": "/p"})

    assert inject_mod.main() == 0
    assert json.loads(capsys.readouterr().out) == {"continue": True}


def test_inject_garbage_stdin_still_emits_valid_json(monkeypatch, capsys, fake_engram):
    _stdin(monkeypatch, "this is not json {{{")

    assert inject_mod.main() == 0

    out = json.loads(capsys.readouterr().out)
    assert out["continue"] is True
    # Unknown payload degrades to project_folder="" but the brief still lands.
    assert _calls(fake_engram, "get_resume_brief") == [
        {"project_folder": "", "token_budget": 1500}
    ]


def test_inject_reentry_guard(monkeypatch, capsys, fake_engram):
    monkeypatch.setenv("ENGRAM_CURSOR_INJECT_ACTIVE", "1")
    _stdin(monkeypatch, {"cwd": "/p"})

    assert inject_mod.main() == 0
    assert json.loads(capsys.readouterr().out) == {"continue": True}
    assert fake_engram.calls == []


# ---------------------------------------------------------------------------
# cursor_save_on_stop
# ---------------------------------------------------------------------------


def test_save_happy_path(monkeypatch, fake_engram):
    _stdin(
        monkeypatch,
        {"summary": "做了 A 和 B", "cwd": "E:/proj", "conversation_id": "conv-7"},
    )

    assert save_mod.main() == 0

    saves = _calls(fake_engram, "save_agent_context")
    assert len(saves) == 1
    save = saves[0]
    assert save["tool"] == "cursor"
    assert save["session_id"] == "conv-7"
    assert save["project_folder"] == "E:/proj"
    assert "做了 A 和 B" in save["content"]
    assert "工作目录: E:/proj" in save["content"]
    # Debounce timestamp must be recorded under the isolated ENGRAM_DIR.
    assert (payload_mod.state_dir() / "cursor_save_state.json").exists()


def test_save_empty_payload_skips(monkeypatch, fake_engram):
    _stdin(monkeypatch, {})

    assert save_mod.main() == 0
    assert fake_engram.calls == []


def test_save_engram_failure_is_silent(monkeypatch, fake_engram):
    fake_engram.cls.raise_on = {"save_agent_context"}
    _stdin(monkeypatch, {"summary": "x", "conversation_id": "c"})

    assert save_mod.main() == 0
    # Failure must not record a debounce timestamp (next attempt may succeed).
    assert payload_mod.recently_saved("c", 10) is False


def test_save_debounces_repeat_stop_events(monkeypatch, fake_engram):
    payload = {"summary": "turn 1", "conversation_id": "conv-d"}
    _stdin(monkeypatch, payload)
    save_mod.main()
    _stdin(monkeypatch, {**payload, "summary": "turn 2"})
    save_mod.main()

    assert len(_calls(fake_engram, "save_agent_context")) == 1  # second debounced


def test_save_session_end_bypasses_debounce(monkeypatch, fake_engram):
    payload = {"summary": "turn", "conversation_id": "conv-e"}
    _stdin(monkeypatch, payload)
    save_mod.main()

    monkeypatch.setattr(sys, "argv", ["cursor-hook-test", "--event", "sessionEnd"])
    _stdin(monkeypatch, {**payload, "summary": "final"})
    save_mod.main()

    saves = _calls(fake_engram, "save_agent_context")
    assert len(saves) == 2
    assert "sessionEnd" in saves[1]["content"]


def test_save_debounce_zero_disables(monkeypatch, fake_engram):
    monkeypatch.setenv("ENGRAM_CURSOR_SAVE_DEBOUNCE", "0")
    payload = {"summary": "turn", "conversation_id": "conv-z"}
    for _ in range(2):
        _stdin(monkeypatch, payload)
        save_mod.main()

    assert len(_calls(fake_engram, "save_agent_context")) == 2


def test_save_never_touches_knowledge_paths(monkeypatch, fake_engram):
    _stdin(monkeypatch, {"summary": "anything", "conversation_id": "c1"})
    save_mod.main()

    methods = {method for method, _ in fake_engram.calls}
    assert "wrap_up_session" not in methods
    assert "extract_session_insights" not in methods
    assert methods == {"save_agent_context"}


def test_save_transcript_fallback(monkeypatch, tmp_path: Path, fake_engram):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({"summary": "来自 transcript 的内容"}, ensure_ascii=False),
        encoding="utf-8",
    )
    _stdin(monkeypatch, {"transcript_path": str(transcript), "session_id": "s9"})

    save_mod.main()

    saves = _calls(fake_engram, "save_agent_context")
    assert len(saves) == 1
    assert "来自 transcript 的内容" in saves[0]["content"]


def test_save_content_keeps_tail_and_is_capped(monkeypatch, fake_engram):
    big = ("早" * 5000) + "结尾标记"
    _stdin(monkeypatch, {"summary": big, "conversation_id": "cap"})

    save_mod.main()

    content = _calls(fake_engram, "save_agent_context")[0]["content"]
    assert content.endswith("结尾标记")
    assert len(content) < 4400  # 4000-char summary cap + small header


def test_save_missing_session_id_gets_hook_fallback(monkeypatch, fake_engram):
    _stdin(monkeypatch, {"summary": "no id here"})

    save_mod.main()

    save = _calls(fake_engram, "save_agent_context")[0]
    assert save["session_id"].startswith("hook-")


def test_save_reentry_guard(monkeypatch, fake_engram):
    monkeypatch.setenv("ENGRAM_CURSOR_SAVE_ACTIVE", "1")
    _stdin(monkeypatch, {"summary": "x"})

    assert save_mod.main() == 0
    assert fake_engram.calls == []
