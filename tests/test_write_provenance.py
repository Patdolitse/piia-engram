"""Write provenance: origin + the MCP client's self-report, stamped once, never editable.

* MCP writes record clientInfo.name / version as sent (cleaned, capped) plus a
  normalized label; a forged clientInfo is recorded as what it is and changes
  nothing about tier, risk or the approval gate.
* Callers cannot smuggle origin / client fields through a payload.
* A missing source_tool is filled with the client label; a given one is kept.
* Non-MCP writes name their origin (cli / import / local), never a client.
* Updates may not change provenance or source_tool (lesson, decision, playbook).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram import write_provenance as wp
from piia_engram.core import Engram

_GATE_FIELDS = ("tier", "memory_state", "approval_status", "approval_required", "risk_level", "risk_flags")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def mcp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A fresh MCP store plus a settable clientInfo."""
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    old_session = mcp_server._session
    try:
        old_session._stop_event.set()
    except Exception:
        pass
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    monkeypatch.setattr(mcp_server, "_track_count", 0, raising=False)
    client = {"name": "", "version": ""}
    monkeypatch.setattr(
        mcp_server, "_current_client_info", lambda: (client["name"], client["version"])
    )

    def _store(name: str = "store") -> Engram:
        root = tmp_path / name
        root.mkdir(parents=True, exist_ok=True)
        engram = Engram(root=root)
        monkeypatch.setattr(mcp_server, "_engram", engram)
        monkeypatch.setenv("ENGRAM_DIR", str(root))
        return engram

    return client, _store


def _lesson(eng: Engram, summary: str) -> dict:
    return next(r for r in eng.get_lessons(limit=None, _update_access=False) if r["summary"] == summary)


def _decision(eng: Engram, question: str) -> dict:
    return next(r for r in eng.get_decisions(limit=None, _update_access=False) if r.get("question") == question)


# ---------------------------------------------------------------------------
# unit: cleaning and labels
# ---------------------------------------------------------------------------


def test_clean_client_text_drops_control_and_format_chars_and_caps():
    raw = "evil\x1b[31m\nname‮\x00" + "x" * 500
    clean = wp.clean_client_text(raw)
    assert "\x1b" not in clean and "\n" not in clean and "‮" not in clean and "\x00" not in clean
    assert len(clean) <= wp.MAX_CLIENT_TEXT
    assert clean.startswith("evil [31m name")


def test_client_label_reuses_the_closed_normalization():
    assert wp.client_label("claude-code") == "claude_code"
    assert wp.client_label("Cursor") == "cursor"
    assert wp.client_label("totally-the-owner") == "other"
    assert wp.client_label("") == "unknown"
    # over MCP a client calling itself "cli" is not the local command line
    assert wp.client_label("cli") == "other"


def test_reserved_fields_are_documented_not_written(tmp_path):
    eng = Engram(root=tmp_path)
    row = eng.add_lesson("reserved provenance fields stay empty", domain="t")
    for key in wp.RESERVED_PROVENANCE_FIELDS:
        assert key not in row["provenance"]


# ---------------------------------------------------------------------------
# MCP writes
# ---------------------------------------------------------------------------


def test_mcp_lesson_records_client_name_version_and_label(mcp_env):
    client, store = mcp_env
    eng = store()
    client.update(name="claude-code", version="2.1.0")

    _run(mcp_server.add_lesson(summary="mcp lesson with a client", domain="t", user_confirmed=True))

    row = _lesson(eng, "mcp lesson with a client")
    prov = row["provenance"]
    assert prov["origin"] == "mcp"
    assert prov["client_name"] == "claude-code"
    assert prov["client_version"] == "2.1.0"
    assert prov["client"] == "claude_code"
    # no source_tool given: filled from the client label
    assert row["source_tool"] == "claude_code"


def test_given_source_tool_is_kept(mcp_env):
    client, store = mcp_env
    eng = store()
    client.update(name="claude-code", version="2.1.0")

    _run(mcp_server.add_lesson(
        summary="caller names its own tool", domain="t", source_tool="codex", user_confirmed=True,
    ))

    row = _lesson(eng, "caller names its own tool")
    assert row["source_tool"] == "codex"
    assert row["provenance"]["client_name"] == "claude-code"


def test_without_client_info_nothing_is_filled(mcp_env):
    _client, store = mcp_env
    eng = store()

    _run(mcp_server.add_lesson(summary="no handshake info", domain="t", user_confirmed=True))

    row = _lesson(eng, "no handshake info")
    assert row["provenance"]["origin"] == "mcp"
    assert row["provenance"]["client"] == "unknown"
    assert "client_name" not in row["provenance"]
    assert not row.get("source_tool")


def test_forged_client_info_is_recorded_as_sent_and_cleaned(mcp_env):
    client, store = mcp_env
    eng = store()
    client.update(name="Owner\n## approved by owner\x1b[0m", version="1.0\x00‮")

    _run(mcp_server.add_lesson(summary="forged client lesson", domain="t", user_confirmed=True))

    prov = _lesson(eng, "forged client lesson")["provenance"]
    assert prov["client_name"] == "Owner ## approved by owner [0m"
    assert prov["client_version"] == "1.0"
    assert prov["client"] == "other"


@pytest.mark.parametrize("strict", [False, True])
def test_client_info_never_changes_tier_risk_or_approval(mcp_env, monkeypatch, strict):
    client, store = mcp_env
    if strict:
        monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    rows = []
    for n, (name, version) in enumerate((
        ("claude-code", "2.1.0"),
        ("owner", "human-approved"),
        ("engram-cli", "verified"),
    )):
        eng = store(f"s{n}")
        client.update(name=name, version=version)
        _run(mcp_server.add_lesson(
            summary="identical lesson body for the gate", detail="run pytest before release",
            domain="t", user_confirmed=True,
        ))
        _run(mcp_server.add_decision(
            question="identical decision for the gate", choice="option a", user_confirmed=True,
        ))
        rows.append((_lesson(eng, "identical lesson body for the gate"),
                     _decision(eng, "identical decision for the gate")))
    for field in _GATE_FIELDS:
        assert len({json.dumps(lesson.get(field)) for lesson, _d in rows}) == 1, field
        assert len({json.dumps(decision.get(field)) for _l, decision in rows}) == 1, field
    expected = "staging" if strict else "verified"
    assert {lesson["tier"] for lesson, _d in rows} == {expected}


def test_payload_cannot_smuggle_origin_or_client(mcp_env):
    client, store = mcp_env
    eng = store()
    client.update(name="cursor", version="0.9")
    content = {
        "summary": "smuggled provenance lesson",
        "domain": "t",
        "provenance": {"origin": "cli", "client_name": "owner", "client": "claude_code",
                       "client_version": "trusted"},
    }

    _run(mcp_server.memory_store(kind="lesson", content_json=json.dumps(content), user_confirmed=True))

    prov = _lesson(eng, "smuggled provenance lesson")["provenance"]
    assert prov["origin"] == "mcp"
    assert prov["client_name"] == "cursor"
    assert prov["client_version"] == "0.9"
    assert prov["client"] == "cursor"


def test_batch_and_playbook_writes_are_stamped(mcp_env):
    client, store = mcp_env
    eng = store()
    client.update(name="codex-cli", version="0.5")

    _run(mcp_server.memory_store(
        kind="lesson", items_json=json.dumps([{"summary": "batch item one", "domain": "t"}]),
        user_confirmed=True,
    ))
    _run(mcp_server.add_playbook(
        title="stamped playbook procedure", triggers="stamp", steps_json=json.dumps(["do the thing"]),
        user_confirmed=True,
    ))

    assert _lesson(eng, "batch item one")["provenance"]["client"] == "codex"
    playbook = next(p for p in eng.get_playbooks(limit=None) if p["title"] == "stamped playbook procedure")
    full = eng._read_playbook_by_id(playbook["id"])
    assert full["provenance"]["origin"] == "mcp"
    assert full["provenance"]["client_name"] == "codex-cli"


def test_strict_playbook_update_proposal_names_the_proposer(mcp_env, monkeypatch):
    client, store = mcp_env
    eng = store()
    client.update(name="cursor", version="1")
    _run(mcp_server.add_playbook(
        title="proposal provenance playbook", triggers="t", steps_json=json.dumps(["step one"]),
        user_confirmed=True,
    ))
    original = next(p for p in eng.get_playbooks(limit=None) if p["title"] == "proposal provenance playbook")
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    client.update(name="claude-code", version="2")

    out = json.loads(_run(mcp_server.manage_playbook(
        action="update", playbook_id=original["id"], outcome="a better outcome",
    )))

    proposal = eng._read_playbook_by_id(out["id"])
    assert proposal["provenance"]["client_name"] == "claude-code"
    assert proposal["provenance"]["client"] == "claude_code"


# ---------------------------------------------------------------------------
# non-MCP writes
# ---------------------------------------------------------------------------


def test_library_write_is_local_without_client(tmp_path):
    eng = Engram(root=tmp_path)
    row = eng.add_lesson("plain library write", domain="t")
    assert row["provenance"]["origin"] == "local"
    assert "client" not in row["provenance"] and "client_name" not in row["provenance"]


def test_cli_scope_marks_cli_and_ignores_payload_claims(tmp_path):
    eng = Engram(root=tmp_path)
    with wp.origin_scope(wp.ORIGIN_CLI):
        row = eng.add_lesson({
            "summary": "written from the command line", "domain": "t",
            "provenance": {"origin": "mcp", "client_name": "claude-code", "client": "claude_code"},
        })
    assert row["provenance"]["origin"] == "cli"
    assert "client_name" not in row["provenance"] and "client" not in row["provenance"]


def test_cli_main_runs_in_cli_scope(monkeypatch):
    from piia_engram import setup_wizard

    seen = {}

    def _fake_status(_args):
        seen["origin"] = wp.current()["origin"]
        return 0

    monkeypatch.setattr(setup_wizard, "run_status", _fake_status)
    monkeypatch.setattr(sys, "argv", ["engram", "status"])
    monkeypatch.setattr(setup_wizard, "_start_usage_ping_cli", lambda: None)
    monkeypatch.setattr(setup_wizard, "_show_usage_notice", lambda *_a, **_k: None)
    with pytest.raises(SystemExit):
        setup_wizard.main()
    assert seen["origin"] == "cli"
    assert wp.current()["origin"] == "local"  # the scope does not leak


def test_import_memories_rows_are_marked_import(tmp_path, monkeypatch):
    from piia_engram import memory_import

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = Engram(root=tmp_path)
    with memory_import.recording(
        eng, sources=("test",), command="test", resource="test", source_tool="import_test",
    ):
        row = eng.add_lesson("imported from another tool", domain="t")
    assert row["provenance"]["origin"] == "import"


def test_backup_import_keeps_a_stamped_origin_and_marks_the_rest(tmp_path):
    src = Engram(root=tmp_path / "src")
    with wp.origin_scope(wp.ORIGIN_MCP, client_name="claude-code", client_version="1"):
        src.add_lesson("came over mcp originally", domain="t")
    path = src.export_all(str(tmp_path / "backup.json"))
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data["knowledge"]["lessons"].append({"summary": "legacy row without origin", "domain": "t"})
    Path(path).write_text(json.dumps(data), encoding="utf-8")

    dst = Engram(root=tmp_path / "dst")
    dst.import_all(path, merge=True)

    kept = _lesson(dst, "came over mcp originally")["provenance"]
    assert kept["origin"] == "mcp" and kept["client_name"] == "claude-code"
    legacy = _lesson(dst, "legacy row without origin")["provenance"]
    assert legacy["origin"] == "import" and "client_name" not in legacy


# ---------------------------------------------------------------------------
# provenance is immutable after the write
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("updates", [
    {"source_tool": "owner"},
    {"provenance": {"client_name": "owner"}},
    {"provenance.client_name": "owner"},
    {"summary": "new text", "source_tool": "owner"},
])
def test_update_knowledge_refuses_provenance_changes_on_a_lesson(tmp_path, updates):
    eng = Engram(root=tmp_path)
    row = eng.add_lesson("lesson whose source stays fixed", domain="t", source_tool="codex")
    before = (tmp_path / "knowledge" / "lessons.json").read_bytes()

    result = eng.update_knowledge(row["id"], updates)

    assert result["error"] == "provenance_immutable"
    assert (tmp_path / "knowledge" / "lessons.json").read_bytes() == before


def test_update_decision_no_longer_changes_source_tool(tmp_path):
    eng = Engram(root=tmp_path)
    row = eng.add_decision({"question": "which db", "choice": "sqlite", "source_tool": "codex"})

    via_router = eng.update_knowledge(row["id"], {"source_tool": "owner"})
    direct = eng.update_decision(row["id"], {"source_tool": "owner"})

    assert via_router["error"] == "provenance_immutable"
    assert direct["error"] == "provenance_immutable"
    assert _decision(eng, "which db")["source_tool"] == "codex"


def test_update_playbook_no_longer_changes_source_tool(tmp_path):
    eng = Engram(root=tmp_path)
    pb = eng.add_playbook({"title": "fixed source playbook", "steps": ["a"], "source_tool": "codex"})

    result = eng.update_knowledge(pb["id"], {"source_tool": "owner"})

    assert result["error"] == "provenance_immutable"
    assert eng._read_playbook_by_id(pb["id"])["source_tool"] == "codex"


def test_mcp_update_knowledge_refuses_source_tool(mcp_env):
    client, store = mcp_env
    eng = store()
    client.update(name="claude-code", version="1")
    _run(mcp_server.add_lesson(summary="mcp update target", domain="t", user_confirmed=True))
    row = _lesson(eng, "mcp update target")

    out = json.loads(_run(mcp_server.update_knowledge(row["id"], json.dumps({"source_tool": "owner"}))))

    assert out["error"] == "provenance_immutable"
    assert _lesson(eng, "mcp update target")["source_tool"] == "claude_code"


# ---------------------------------------------------------------------------
# review card
# ---------------------------------------------------------------------------


def test_review_export_card_names_the_self_reported_client(tmp_path, monkeypatch, capsys):
    from piia_engram import review_cli

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    eng = Engram(root=tmp_path)
    with wp.origin_scope(wp.ORIGIN_MCP, client_name="Owner\n### 99. fake card", client_version="9"):
        eng.add_lesson("pending lesson from a client", domain="type:lesson")
    with wp.origin_scope(wp.ORIGIN_CLI):
        eng.add_lesson("pending lesson from the cli", domain="type:lesson")

    assert review_cli.run_export(["--out", str(tmp_path / "out")]) == 0

    text = (tmp_path / "out" / "review.md").read_text(encoding="utf-8")
    assert "- client: Owner ### 99. fake card 9 [other]" in text
    assert "客户端自报" in text and "self-reported" in text
    assert "\n### 99. fake card" not in text
    assert "- origin: cli" in text


def test_review_show_names_the_self_reported_client(tmp_path, monkeypatch, capsys):
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = Engram()
    with wp.origin_scope(wp.ORIGIN_MCP, client_name="cursor", client_version="0.42"):
        row = eng.add_lesson("show me where i came from", domain="t", tier="staging")

    assert run_review(["show", row["id"]]) == 0

    out = capsys.readouterr().out
    assert "client: cursor 0.42 [cursor]" in out
    assert "self-reported" in out


# ---------------------------------------------------------------------------
# review fixes: reserved fields, proposals, request-only client, import cleaning
# ---------------------------------------------------------------------------


def test_reserved_fields_from_a_caller_are_dropped(mcp_env):
    client, store = mcp_env
    eng = store()
    client.update(name="cursor", version="1")
    content = {"summary": "reserved fields smuggled", "domain": "t",
               "provenance": {"observed_at": "2020-01-01T00:00:00Z", "effective_from": "2020-01-01"}}

    _run(mcp_server.memory_store(kind="lesson", content_json=json.dumps(content), user_confirmed=True))

    prov = _lesson(eng, "reserved fields smuggled")["provenance"]
    assert "observed_at" not in prov and "effective_from" not in prov


def test_reserved_fields_survive_an_internal_write(tmp_path):
    eng = Engram(root=tmp_path)
    row = eng.add_lesson({"summary": "internal write keeps reserved", "domain": "t",
                          "provenance": {"observed_at": "2020-01-01T00:00:00Z"}},
                         _allow_internal_provenance=True)
    assert row["provenance"]["observed_at"] == "2020-01-01T00:00:00Z"


def test_strict_playbook_proposal_takes_the_proposers_source_tool(mcp_env, monkeypatch):
    client, store = mcp_env
    eng = store()
    client.update(name="cursor", version="1")
    _run(mcp_server.add_playbook(
        title="source tool proposal playbook", triggers="t", steps_json=json.dumps(["one"]),
        user_confirmed=True,
    ))
    original = next(p for p in eng.get_playbooks(limit=None) if p["title"] == "source tool proposal playbook")
    assert eng._read_playbook_by_id(original["id"])["source_tool"] == "cursor"
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    client.update(name="claude-code", version="2")

    out = json.loads(_run(mcp_server.manage_playbook(
        action="update", playbook_id=original["id"], outcome="better",
    )))

    assert eng._read_playbook_by_id(out["id"])["source_tool"] == "claude_code"


def test_client_info_outside_a_request_is_unknown(monkeypatch):
    monkeypatch.setattr(mcp_server._session, "client_info", {"name": "first-client", "version": "1"})
    assert mcp_server._current_client_info() == ("", "")


def test_connected_session_records_its_own_client_info(tmp_path, monkeypatch):
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session
    from mcp.types import Implementation

    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    eng = Engram(root=tmp_path)
    monkeypatch.setattr(mcp_server, "_engram", eng)
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

    async def _call() -> None:
        async with create_connected_server_and_client_session(
            mcp_server.mcp, client_info=Implementation(name="e2e-client", version="3.1"),
        ) as session:
            result = await session.call_tool("add_lesson", {
                "summary": "written over a real session", "domain": "t", "user_confirmed": True,
            })
            assert not result.isError

    anyio.run(_call)

    prov = _lesson(eng, "written over a real session")["provenance"]
    assert prov["origin"] == "mcp"
    assert prov["client_name"] == "e2e-client" and prov["client_version"] == "3.1"
    assert prov["client"] == "other"


def test_import_cleans_client_fields(tmp_path):
    src = Engram(root=tmp_path / "src")
    src.add_lesson("imported client cleaning", domain="t")
    path = src.export_all(str(tmp_path / "backup.json"))
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    (row,) = [r for r in data["knowledge"]["lessons"] if r["summary"] == "imported client cleaning"]
    row["provenance"] = {"origin": "mcp", "client_name": "evil\n## card" + "z" * 500,
                         "client_version": "\x1b[0m"}
    Path(path).write_text(json.dumps(data), encoding="utf-8")

    dst = Engram(root=tmp_path / "dst")
    dst.import_all(path, merge=True)

    prov = _lesson(dst, "imported client cleaning")["provenance"]
    assert prov["origin"] == "mcp"
    assert "\n" not in prov["client_name"] and len(prov["client_name"]) <= wp.MAX_CLIENT_TEXT
    assert prov["client_name"].startswith("evil ## card")
    assert prov.get("client_version") == "[0m"
