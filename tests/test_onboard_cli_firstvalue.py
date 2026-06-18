"""C2: onboard CLI first-value fixes (dogfood follow-up).

C1 dogfood on 3 real repos proved the engine works but the CLI first-value loop
was broken at the payoff:
  G1  `engram recall` never surfaced the owner trust block (why-trustworthy /
      anchor / validated-at / expires) — the whole 4.5.0 payoff was invisible
      from the CLI (only the MCP get_recall path showed it).
  G2  `onboard-accept` was one-at-a-time; real repos have 50-70 candidates
      (claude-mem 69 / mem0 54), so a batch accept is required to be usable.

Boundaries preserved (Codex review): CLI recall = owner/private-self; batch
accept stays owner-explicit, per-item anchor-verified, cross-repo-skipped,
invalid-anchor-refused, per-item atomic with partial success + dry-run preview.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram import setup_wizard  # noqa: F401 — import first: resolves the setup_wizard<->cli_commands import cycle
from piia_engram import cli_commands, recall, recall_service
from piia_engram.core import Engram

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "onboard_repo_golden"


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "engram"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    return Engram(root=root)


# --- G1: CLI trusted recall renders the trust block --------------------------


def _trusted_payload() -> dict:
    item = {
        "id": "x",
        "summary": "This project depends on `react`.",
        "tier": "verified",
        "provenance": {
            "confirmation_source": "anchor",
            "anchor_ref": "dep:react",
            "anchor_status": "valid",
            "last_validated_at": "2026-06-18T10:00:00",
        },
    }
    return recall.build_recall_payload(relevant_knowledge=[item], include_trust=True)


def test_render_recall_text_shows_trust_block():
    text = recall_service.render_recall_text(_trusted_payload())
    assert "dep:react" in text          # anchor
    assert "anchor" in text             # why-trustworthy = anchor
    assert "valid" in text              # anchor status
    assert "2026-06-18" in text         # validated-at


def test_render_recall_text_omits_trust_when_absent():
    payload = recall.build_recall_payload(
        relevant_knowledge=[{"id": "x", "summary": "s", "tier": "verified", "provenance": {}}]
    )
    text = recall_service.render_recall_text(payload)
    assert "trust:" not in text         # no trust block -> no trust line


def test_cli_recall_surfaces_trust_for_owner(eng, monkeypatch, capsys):
    monkeypatch.setattr(
        "piia_engram.freshness_anchors.read_project_id", lambda root: "github.com/acme/app"
    )
    eng.create_onboard_candidate(
        "This project depends on `react` (^18.2.0).",
        anchor_ref="dep:react",
        anchor_detail={"version": "^18.2.0"},
        anchor_project_id="github.com/acme/app",
        extractor="onboard-repo@test",
    )
    item_id = next(
        e["id"] for e in eng.get_lessons(limit=None, _update_access=False)
        if e.get("provenance", {}).get("anchor_ref") == "dep:react"
    )
    eng.accept_onboard_candidate(item_id)  # owner-accept -> verified

    rc = cli_commands._run_recall(["--query", "react"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "dep:react" in out            # trust block surfaced in CLI text digest


def test_cli_recall_no_trust_flag_opts_out(eng, monkeypatch, capsys):
    monkeypatch.setattr(
        "piia_engram.freshness_anchors.read_project_id", lambda root: "github.com/acme/app"
    )
    eng.create_onboard_candidate(
        "This project depends on `react`.",
        anchor_ref="dep:react",
        anchor_project_id="github.com/acme/app",
        extractor="t",
    )
    item_id = next(
        e["id"] for e in eng.get_lessons(limit=None, _update_access=False)
        if e.get("provenance", {}).get("anchor_ref") == "dep:react"
    )
    eng.accept_onboard_candidate(item_id)

    rc = cli_commands._run_recall(["--query", "react", "--no-trust", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert all("trust" not in i for i in payload.get("knowledge", []))


# --- G2: batch accept (core) -------------------------------------------------


def test_accept_onboard_candidates_batch_accepts_all(eng, monkeypatch):
    monkeypatch.setattr(
        "piia_engram.freshness_anchors.read_project_id", lambda root: "github.com/acme/app"
    )
    summary = eng.onboard_repo(str(GOLDEN), repo_id="github.com/acme/app")
    assert summary["created"] >= 5

    res = eng.accept_onboard_candidates(project_root=str(GOLDEN))
    assert res["accepted"] >= 5
    assert res["rejected"] == 0
    assert res["dry_run"] is False
    left = [
        e for e in eng.get_lessons(limit=None, _update_access=False)
        if e.get("domain") == "repo-fact" and e.get("tier") == "staging"
    ]
    assert left == []                    # all promoted


def test_accept_onboard_candidates_dry_run_writes_nothing(eng, monkeypatch):
    monkeypatch.setattr(
        "piia_engram.freshness_anchors.read_project_id", lambda root: "github.com/acme/app"
    )
    eng.onboard_repo(str(GOLDEN), repo_id="github.com/acme/app")

    res = eng.accept_onboard_candidates(project_root=str(GOLDEN), dry_run=True)
    assert res["dry_run"] is True
    assert res["would_accept"] >= 5
    assert res["repo_id"] == "github.com/acme/app"
    verified = [
        e for e in eng.get_lessons(limit=None, _update_access=False)
        if e.get("domain") == "repo-fact" and e.get("tier") == "verified"
    ]
    assert verified == []                # dry-run promoted nothing


def test_accept_onboard_candidates_skips_cross_repo(eng, monkeypatch):
    monkeypatch.setattr(
        "piia_engram.freshness_anchors.read_project_id", lambda root: "github.com/acme/app"
    )
    eng.create_onboard_candidate(
        "dep a", anchor_ref="dep:a", anchor_project_id="github.com/acme/app", extractor="t"
    )
    eng.create_onboard_candidate(
        "dep b", anchor_ref="dep:b", anchor_project_id="github.com/other/repo", extractor="t"
    )

    res = eng.accept_onboard_candidates(project_root="/whatever")
    assert res["accepted"] == 1          # only acme/app in scope
    assert res["skipped"] >= 1           # other/repo candidate skipped, not accepted
    other = next(
        e for e in eng.get_lessons(limit=None, _update_access=False)
        if e.get("provenance", {}).get("anchor_ref") == "dep:b"
    )
    assert other["tier"] == "staging"    # untouched


def test_accept_onboard_candidates_refuses_invalid_anchor(eng, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text('{"dependencies": {}}', encoding="utf-8")  # no react
    eng.create_onboard_candidate(
        "dep react", anchor_ref="dep:react", anchor_detail={"version": "^18"}, extractor="t"
    )

    res = eng.accept_onboard_candidates(project_root=str(repo))
    assert res["accepted"] == 0
    assert res["rejected"] >= 1          # invalid anchor refused, not promoted


# --- G2: batch accept (CLI) --------------------------------------------------


def test_cli_onboard_accept_all_dry_run_then_yes(eng, monkeypatch, capsys):
    monkeypatch.setattr(
        "piia_engram.freshness_anchors.read_project_id", lambda root: "github.com/acme/app"
    )
    eng.onboard_repo(str(GOLDEN), repo_id="github.com/acme/app")

    # no --yes -> dry-run preview, writes nothing
    rc = cli_commands.run_onboard_accept(["--all", "--root", str(GOLDEN), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["dry_run"] is True
    assert data["would_accept"] >= 5
    still = [
        e for e in eng.get_lessons(limit=None, _update_access=False)
        if e.get("domain") == "repo-fact" and e.get("tier") == "staging"
    ]
    assert len(still) >= 5

    # --yes -> executes the batch
    rc = cli_commands.run_onboard_accept(["--all", "--yes", "--root", str(GOLDEN), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["accepted"] >= 5
    assert data["dry_run"] is False
