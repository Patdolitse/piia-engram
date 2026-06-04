"""Tests for live-client validation evidence scaffolding."""

from __future__ import annotations

from pathlib import Path

from piia_engram import client_validation as cv


def test_run_meta_contains_required_keys():
    meta = cv.build_run_meta(
        client_id="hermes",
        client_version="0.15.2",
        surface="CLI",
        model="deepseek-v4-flash",
        engram_mode="MCP read-only",
        environment_arm="Engram-isolated",
        workspace_isolated=True,
        home_isolated=True,
        write_tools_allowed=False,
        known_limitations=["CLI only"],
        verified_level="L4",
    )

    assert cv.missing_run_meta_keys(meta) == []
    assert meta["known_limitations"] == ["CLI only"]


def test_evidence_layout_matches_runbook_contract():
    layout = cv.evidence_dir_layout()

    for required in [
        "run_meta.json",
        "tool_locations.json",
        "test-materials/",
        "prompts/",
        "raw/",
        "parsed/",
        "timings.json",
        "zero_pollution.txt",
        "REPORT.md",
        "OPTIMIZATION_NOTES.md",
    ]:
        assert required in layout


def test_zero_pollution_report_passes_for_identical_snapshot(tmp_path: Path):
    target = tmp_path / "lessons.json"
    target.write_text('{"lessons": []}', encoding="utf-8")
    before = cv.snapshot_files([target])
    after = cv.snapshot_files([target])

    report = cv.zero_pollution_report(before, after)

    assert report["clean"] is True
    assert report["changed_files"] == 0
    assert report["files"][0]["status"] == "unchanged"


def test_zero_pollution_report_flags_changed_file(tmp_path: Path):
    target = tmp_path / "lessons.json"
    target.write_text('{"lessons": []}', encoding="utf-8")
    before = cv.snapshot_files([target])
    target.write_text('{"lessons": [{"summary": "polluted"}]}', encoding="utf-8")
    after = cv.snapshot_files([target])

    report = cv.zero_pollution_report(before, after)

    assert report["clean"] is False
    assert report["changed_files"] == 1
    assert report["files"][0]["status"] == "changed"


def test_zero_pollution_report_flags_added_and_removed_files(tmp_path: Path):
    added = tmp_path / "added.json"
    removed = tmp_path / "removed.json"
    removed.write_text("before", encoding="utf-8")

    before = cv.snapshot_files([added, removed])
    added.write_text("after", encoding="utf-8")
    removed.unlink()
    after = cv.snapshot_files([added, removed])

    report = cv.zero_pollution_report(before, after)
    statuses = {item["path"]: item["status"] for item in report["files"]}

    assert report["clean"] is False
    assert statuses[str(added)] == "added"
    assert statuses[str(removed)] == "removed"


def test_zero_pollution_markdown_is_chinese_user_facing(tmp_path: Path):
    target = tmp_path / "decisions.json"
    target.write_text("[]", encoding="utf-8")
    report = cv.zero_pollution_report(cv.snapshot_files([target]), cv.snapshot_files([target]))

    rendered = cv.render_zero_pollution_markdown(report)

    assert "零污染校验" in rendered
    assert "结论：通过" in rendered


def test_openclaw_l4_claim_is_blocked_without_live_agent_evidence():
    result = cv.validate_public_claim(
        client_id="openclaw",
        claimed_level="L4",
        claim="OpenClaw live agent continuity verified",
        evidence_mode="static oc-path",
        live_agent_verified=False,
    )

    assert result["allowed"] is False
    assert any("OpenClaw live/cross-client continuity is not verified" in p for p in result["problems"])


def test_openclaw_l3_static_claim_is_allowed():
    result = cv.validate_public_claim(
        client_id="openclaw",
        claimed_level="L3",
        claim="OpenClaw-compatible static file bridge verified to L3 static snapshot A/B",
        evidence_mode="static file bridge via oc-path",
        live_agent_verified=False,
    )

    assert result["allowed"] is True


def test_openclaw_live_claim_is_allowed_when_live_agent_evidence_exists():
    result = cv.validate_public_claim(
        client_id="openclaw",
        claimed_level="L4",
        claim="OpenClaw live agent continuity verified",
        evidence_mode="live agent A/B",
        live_agent_verified=True,
    )

    assert result["allowed"] is True


def test_openclaw_model_continuity_claim_is_blocked_without_live_evidence():
    result = cv.validate_public_claim(
        client_id="openclaw",
        claimed_level="L3",
        claim="OpenClaw model continuity is verified",
        evidence_mode="static oc-path",
        live_agent_verified=False,
    )

    assert result["allowed"] is False
    assert any("model continuity" in p for p in result["problems"])


def test_universal_tool_claim_is_blocked():
    result = cv.validate_public_claim(
        client_id="all",
        claimed_level="L5",
        claim="Engram works with every AI tool and full context is shared",
        evidence_mode="public summary",
    )

    assert result["allowed"] is False
