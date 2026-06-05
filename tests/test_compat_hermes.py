"""Tests for the Hermes-style compatibility handoff payload."""

from __future__ import annotations

import json
from pathlib import Path

from piia_engram import Engram, hermes_handoff_payload
from piia_engram.compat import export_to_openclaw, import_from_openclaw


def test_hermes_handoff_payload_schema_and_counts(tmp_path: Path) -> None:
    eng = Engram(root=tmp_path / "store")
    eng.update_profile({
        "role": "solo founder",
        "language": "zh-CN",
        "technical_level": "learning with AI",
    })
    eng.add_lesson({"summary": "verified lesson can be counted", "domain": "workflow"})
    eng.add_decision({
        "question": "Which release boundary?",
        "choice": "Explicit approval before public actions.",
        "reasoning": "Reasoning stays out of Hermes handoff payload.",
    })

    payload = hermes_handoff_payload(eng)

    assert set(payload) == {
        "schema",
        "source",
        "identity_summary",
        "active_decisions",
        "lessons_count",
    }
    assert payload["schema"] == "hermes_handoff_v1"
    assert payload["source"] == "piia-engram"
    assert payload["identity_summary"]["role"] == "solo founder"
    assert payload["lessons_count"] == 1
    assert payload["active_decisions"] == [
        {
            "question": "Which release boundary?",
            "choice": "Explicit approval before public actions.",
        }
    ]


def test_hermes_handoff_payload_redacts_paths_and_reasoning(tmp_path: Path) -> None:
    eng = Engram(root=tmp_path / "store")
    secret_path = r"E:\SecretProject\private.md"
    eng.update_profile({
        "role": "owner",
        "language": "zh-CN",
        "description": f"Use {secret_path}",
    })
    eng.add_lesson({"summary": f"lesson with {secret_path}", "domain": "private"})
    eng.add_decision({
        "question": f"Where is the private plan? {secret_path}",
        "choice": "Do not expose raw paths.",
        "reasoning": f"Reasoning mentions {secret_path}",
    })

    payload = hermes_handoff_payload(eng)
    blob = json.dumps(payload, ensure_ascii=False)

    assert secret_path not in blob
    assert "reasoning" not in blob
    assert "private.md" not in blob
    assert payload["identity_summary"] == {"role": "owner", "language": "zh-CN"}
    assert payload["active_decisions"] == [
        {"question": "", "choice": "Do not expose raw paths."}
    ]


def test_hermes_handoff_payload_after_openclaw_roundtrip(tmp_path: Path) -> None:
    source = Engram(root=tmp_path / "source")
    source.update_profile({
        "role": "local identity owner",
        "language": "zh-CN",
        "technical_level": "AI-assisted",
    })
    source.add_lesson({"summary": "OpenClaw bridge lesson", "domain": "compat"})

    out_dir = tmp_path / "openclaw"
    export_to_openclaw(source, str(out_dir))

    target = Engram(root=tmp_path / "target")
    result = import_from_openclaw(
        target,
        soul_path=str(out_dir / "SOUL.md"),
        memory_path=str(out_dir / "MEMORY.md"),
        user_path=str(out_dir / "USER.md"),
    )
    payload = hermes_handoff_payload(target)

    assert result["status"] == "success"
    assert payload["schema"] == "hermes_handoff_v1"
    assert payload["identity_summary"]["role"] == "local identity owner"
    assert payload["lessons_count"] == 1
