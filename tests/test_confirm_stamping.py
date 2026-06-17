"""A.5a owner confirmation stamping tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram import provenance as P
from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    engram = Engram(root=tmp_path)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _run(coro):
    return asyncio.run(coro)


def _stored_lesson(eng: Engram, item_id: str) -> dict:
    return next(
        item
        for item in eng.get_lessons(limit=None, _update_access=False)
        if item["id"] == item_id
    )


def test_promote_knowledge_stamps_human_confirmation_without_skip_decay(
    eng: Engram,
) -> None:
    lesson = eng.add_lesson("owner approved staging fact", tier="staging")

    result = eng.promote_knowledge(lesson["id"])

    assert result == {"status": "promoted", "id": lesson["id"]}
    stored = _stored_lesson(eng, lesson["id"])
    assert stored["tier"] == "verified"
    assert stored["provenance"]["confirmation_source"] == "human"
    freshness = P.compute_freshness(stored)
    assert freshness["source_class"] == P.SOURCE_HUMAN
    assert freshness["decay_policy"] == P.DECAY_POLICY_TIME
    assert freshness["skip_decay"] is False


def test_confirm_knowledge_by_test_stamps_test_signal_and_skips_decay(
    eng: Engram,
) -> None:
    lesson = eng.add_lesson("test signal backed fact")

    updated = eng.confirm_knowledge(lesson["id"], by="test")

    assert updated["id"] == lesson["id"]
    assert updated["provenance"]["confirmation_source"] == "test_signal"
    freshness = P.compute_freshness(updated)
    assert freshness["source_class"] == P.SOURCE_SIGNAL
    assert freshness["skip_decay"] is True
    assert freshness["temporal_status"] in {P.FRESH, P.AGING, P.STALE, P.UNKNOWN}


def test_confirm_knowledge_by_anchor_stamps_valid_anchor_and_ref(
    eng: Engram,
) -> None:
    lesson = eng.add_lesson("anchor backed fact")

    updated = eng.confirm_knowledge(lesson["id"], by="anchor", anchor_ref="dep:jest")

    provenance = updated["provenance"]
    assert provenance["confirmation_source"] == "anchor"
    assert provenance["anchor_status"] == "valid"
    assert provenance["anchor_ref"] == "dep:jest"
    freshness = P.compute_freshness(updated)
    assert freshness["source_class"] == P.SOURCE_ANCHOR
    assert freshness["skip_decay"] is True


def test_confirm_knowledge_anchor_requires_nonempty_anchor_ref(eng: Engram) -> None:
    lesson = eng.add_lesson("anchor without ref should not be stamped")
    before = _stored_lesson(eng, lesson["id"])

    result = eng.confirm_knowledge(lesson["id"], by="anchor")

    assert result["error"]
    stored = _stored_lesson(eng, lesson["id"])
    assert stored.get("last_updated") == before.get("last_updated")
    assert "confirmation_source" not in stored.get("provenance", {})
    assert "anchor_status" not in stored.get("provenance", {})
    assert "anchor_ref" not in stored.get("provenance", {})


def test_confirm_knowledge_rejects_unknown_confirmation_mode(eng: Engram) -> None:
    lesson = eng.add_lesson("unknown confirmation mode should not be stamped")

    result = eng.confirm_knowledge(lesson["id"], by="garbage")

    assert result["error"]
    stored = _stored_lesson(eng, lesson["id"])
    assert "confirmation_source" not in stored.get("provenance", {})


def test_agent_facing_add_lesson_still_strips_smuggled_confirmation(
    eng: Engram,
) -> None:
    stored = eng.add_lesson(
        {
            "summary": "agent cannot self-certify as a test signal",
            "domain": "freshness",
            "provenance": {
                "source_agent": "codex",
                "confirmation_source": "test_signal",
                "anchor_status": "valid",
            },
        }
    )

    assert stored["provenance"]["source_agent"] == "codex"
    assert "confirmation_source" not in stored["provenance"]
    assert "anchor_status" not in stored["provenance"]


def test_read_freshness_status_remains_four_state_after_confirmation(
    eng: Engram,
) -> None:
    lesson = eng.add_lesson("public freshness status stays four-state")
    updated = eng.confirm_knowledge(lesson["id"], by="test")

    freshness = P.compute_freshness(updated)

    assert freshness["freshness_status"] in {P.FRESH, P.AGING, P.STALE, P.UNKNOWN}
    assert freshness["freshness_status"] not in {"human", "test_signal", "anchor"}


def test_mcp_confirm_knowledge_is_owner_gated(
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lesson = eng.add_lesson("mcp owner gate protected confirmation")

    def _refuse(*_args, **_kwargs):
        return json.dumps({"error": "owner_only"})

    monkeypatch.setattr(mcp_server._gov_rt, "maybe_refuse_owner_write", _refuse)

    out = _run(mcp_server.confirm_knowledge(lesson["id"], by="test"))

    assert json.loads(out) == {"error": "owner_only"}
    stored = _stored_lesson(eng, lesson["id"])
    assert "confirmation_source" not in stored.get("provenance", {})


def test_mcp_confirm_knowledge_stamps_anchor_when_owner_allowed(eng: Engram) -> None:
    lesson = eng.add_lesson("mcp owner confirms anchor")

    out = _run(
        mcp_server.confirm_knowledge(
            lesson["id"],
            by="anchor",
            anchor_ref="dep:jest",
        )
    )

    result = json.loads(out)
    assert result["provenance"]["confirmation_source"] == "anchor"
    assert result["provenance"]["anchor_status"] == "valid"
    assert result["provenance"]["anchor_ref"] == "dep:jest"


def test_confirm_cli_stamps_test_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from piia_engram.setup_wizard import run_confirm

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    lesson = Engram().add_lesson("cli owner confirms test signal")

    assert run_confirm([lesson["id"], "--by", "test"]) == 0

    out = capsys.readouterr().out
    assert "已确认知识" in out
    stored = Engram(root=tmp_path).get_lessons(limit=None, _update_access=False)[0]
    assert stored["provenance"]["confirmation_source"] == "test_signal"
