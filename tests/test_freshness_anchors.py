"""A.5b owner-only anchor revalidation tests."""

from __future__ import annotations

import json
import subprocess
import asyncio
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram import freshness_anchors as A
from piia_engram import provenance as P
from piia_engram.core import Engram, strip_untrusted_trust_fields
from piia_engram.storage import UNTRUSTED_TRUST_FIELDS


PROJECT_ID = "github.com/acme/widget"
UPPER_PROJECT_ID = "github.com/foo/bar"


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "engram"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    return Engram(root=root)


def _stored_lesson(eng: Engram, item_id: str) -> dict:
    return next(
        item
        for item in eng.get_lessons(limit=None, _update_access=False)
        if item["id"] == item_id
    )


def _stored_decision(eng: Engram, item_id: str) -> dict:
    return next(
        item
        for item in eng.get_decisions(limit=None, _update_access=False)
        if item["id"] == item_id
    )


def _stored_playbook(eng: Engram, item_id: str) -> dict:
    return next(
        item
        for item in eng.get_playbooks(limit=None, _update_access=False)
        if item["id"] == item_id
    )


def _run(coro):
    return asyncio.run(coro)


def _write_package_json(root: Path, deps: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps({"dependencies": deps}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_normalize_git_remote_variants_share_stable_project_id() -> None:
    expected = "github.com/foo/bar"

    assert A.normalize_git_remote("git@github.com:foo/bar.git") == expected
    assert A.normalize_git_remote("https://GitHub.com/foo/bar.git") == expected
    assert A.normalize_git_remote("ssh://git@github.com/foo/bar.git") == expected
    assert A.normalize_git_remote("git@GITHUB.com:foo/bar") == expected
    assert A.normalize_git_remote("git@github.com:Foo/Bar.git") == expected
    assert A.normalize_git_remote("ssh://git@example.com/Foo/Bar.git") == "example.com/Foo/Bar"
    assert A.normalize_git_remote("not a remote") is None


def test_read_project_id_uses_git_origin_remote(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:Foo/Bar.git"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"git unavailable for read_project_id smoke test: {exc}")

    assert A.read_project_id(str(repo)) == "github.com/foo/bar"


def test_parse_anchor_ref_accepts_dep_and_file_only() -> None:
    assert A.parse_anchor_ref("dep:jest") == {"kind": "dep", "ref": "jest"}
    assert A.parse_anchor_ref(" file:vitest.config.ts ") == {
        "kind": "file",
        "ref": "vitest.config.ts",
    }
    assert A.parse_anchor_ref("garbage") is None
    assert A.parse_anchor_ref("") is None
    assert A.parse_anchor_ref("url:https://example.com") is None


def test_check_anchor_dep_from_package_json_valid_invalid_unknown(tmp_path: Path) -> None:
    parsed = A.parse_anchor_ref("dep:jest")
    assert parsed is not None

    _write_package_json(tmp_path, {"jest": "^29.0.0"})
    assert A.check_anchor(parsed, str(tmp_path)) == "valid"

    _write_package_json(tmp_path, {"vitest": "^2.0.0"})
    assert A.check_anchor(parsed, str(tmp_path)) == "invalid"

    (tmp_path / "package.json").unlink()
    assert A.check_anchor(parsed, str(tmp_path)) == "unknown"


def test_check_anchor_dep_from_package_json_peer_and_optional(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({
            "peerDependencies": {"react": "^19.0.0"},
            "optionalDependencies": {"fsevents": "^2.3.0"},
        }),
        encoding="utf-8",
    )

    assert A.check_anchor(A.parse_anchor_ref("dep:react"), str(tmp_path)) == "valid"
    assert A.check_anchor(A.parse_anchor_ref("dep:fsevents"), str(tmp_path)) == "valid"


def test_check_anchor_dep_from_python_manifests(tmp_path: Path) -> None:
    req = A.parse_anchor_ref("dep:requests")
    assert req is not None
    (tmp_path / "requirements.txt").write_text(
        "requests>=2\npytest==8.0\n",
        encoding="utf-8",
    )
    assert A.check_anchor(req, str(tmp_path)) == "valid"

    pyproject_dep = A.parse_anchor_ref("dep:ruff")
    assert pyproject_dep is not None
    (tmp_path / "requirements.txt").unlink()
    (tmp_path / "pyproject.toml").write_text(
        "[project]\ndependencies = ['ruff>=0.8']\n"
        "[project.optional-dependencies]\ntest = ['pytest']\n",
        encoding="utf-8",
    )
    assert A.check_anchor(pyproject_dep, str(tmp_path)) == "valid"


def test_check_anchor_unknown_for_unsupported_pyproject_layouts(tmp_path: Path) -> None:
    parsed = A.parse_anchor_ref("dep:django")
    assert parsed is not None

    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry.dependencies]\npython = '^3.11'\ndjango = '^5'\n",
        encoding="utf-8",
    )
    assert A.check_anchor(parsed, str(tmp_path)) == "unknown"

    (tmp_path / "pyproject.toml").write_text(
        "[tool.pdm]\n[tool.pdm.dev-dependencies]\ntest = ['pytest']\n",
        encoding="utf-8",
    )
    assert A.check_anchor(parsed, str(tmp_path)) == "unknown"


def test_check_anchor_unknown_for_indirect_requirements(tmp_path: Path) -> None:
    parsed = A.parse_anchor_ref("dep:django")
    assert parsed is not None
    (tmp_path / "requirements.txt").write_text(
        "-r requirements-base.txt\npytest==8.0\n",
        encoding="utf-8",
    )

    assert A.check_anchor(parsed, str(tmp_path)) == "unknown"


def test_check_anchor_file_ref_is_confined_to_root(tmp_path: Path) -> None:
    (tmp_path / "vitest.config.ts").write_text("export default {}", encoding="utf-8")

    assert A.check_anchor(A.parse_anchor_ref("file:vitest.config.ts"), str(tmp_path)) == "valid"
    assert A.check_anchor(A.parse_anchor_ref("file:missing.txt"), str(tmp_path)) == "invalid"
    assert A.check_anchor(A.parse_anchor_ref("file:../escape.txt"), str(tmp_path)) in {
        "invalid",
        "unknown",
    }


def test_confirm_anchor_can_store_project_id(eng: Engram) -> None:
    lesson = eng.add_lesson("anchor belongs to one project")

    updated = eng.confirm_knowledge(
        lesson["id"],
        by="anchor",
        anchor_ref="dep:jest",
        anchor_project_id=PROJECT_ID,
    )

    assert updated["provenance"]["confirmation_source"] == "anchor"
    assert updated["provenance"]["anchor_status"] == "valid"
    assert updated["provenance"]["anchor_project_id"] == PROJECT_ID


def test_confirm_cli_anchor_stamps_current_git_project_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from piia_engram.setup_wizard import run_confirm

    data_root = tmp_path / "engram"
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:acme/widget.git"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"git unavailable for confirm CLI smoke test: {exc}")

    monkeypatch.setenv("ENGRAM_DIR", str(data_root))
    monkeypatch.chdir(repo)
    lesson = Engram(root=data_root).add_lesson("cli anchor captures project id")

    assert run_confirm([lesson["id"], "--by", "anchor", "--anchor", "dep:jest"]) == 0

    assert "已确认知识" in capsys.readouterr().out
    stored = Engram(root=data_root).get_lessons(limit=None, _update_access=False)[0]
    assert stored["provenance"]["anchor_project_id"] == PROJECT_ID


def test_mcp_confirm_anchor_can_capture_project_id_from_project_root(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:acme/widget.git"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"git unavailable for MCP confirm project-root smoke test: {exc}")
    monkeypatch.setattr(mcp_server, "_engram", eng)
    lesson = eng.add_lesson("mcp confirm captures project id")

    out = _run(
        mcp_server.confirm_knowledge(
            lesson["id"],
            by="anchor",
            anchor_ref="dep:jest",
            project_root=str(repo),
        )
    )

    result = json.loads(out)
    assert result["provenance"]["anchor_project_id"] == PROJECT_ID


def test_mcp_check_anchors_revalidates_when_owner_allowed(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_package_json(repo, {"vitest": "^2.0.0"})
    monkeypatch.setattr(A, "read_project_id", lambda _root: PROJECT_ID)
    monkeypatch.setattr(mcp_server, "_engram", eng)
    lesson = eng.add_lesson("mcp check anchors flips invalid")
    eng.confirm_knowledge(
        lesson["id"],
        by="anchor",
        anchor_ref="dep:jest",
        anchor_project_id=PROJECT_ID,
    )

    out = _run(mcp_server.check_anchors(project_root=str(repo)))

    result = json.loads(out)
    stored = _stored_lesson(eng, lesson["id"])
    assert result["checked"] == 1
    assert result["invalid"] == 1
    assert stored["provenance"]["anchor_status"] == "invalid"


def test_mcp_check_anchors_is_owner_gated(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_package_json(repo, {"vitest": "^2.0.0"})
    monkeypatch.setattr(A, "read_project_id", lambda _root: PROJECT_ID)
    monkeypatch.setattr(mcp_server, "_engram", eng)
    lesson = eng.add_lesson("mcp check anchors owner gate")
    eng.confirm_knowledge(
        lesson["id"],
        by="anchor",
        anchor_ref="dep:jest",
        anchor_project_id=PROJECT_ID,
    )

    def _refuse(*_args, **_kwargs):
        return json.dumps({"error": "owner_only"})

    monkeypatch.setattr(mcp_server._gov_rt, "maybe_refuse_owner_write", _refuse)

    out = _run(mcp_server.check_anchors(project_root=str(repo)))

    assert json.loads(out) == {"error": "owner_only"}
    stored = _stored_lesson(eng, lesson["id"])
    assert stored["provenance"]["anchor_status"] == "valid"


def test_github_project_id_case_normalization_allows_revalidation_match(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_package_json(repo, {"jest": "^29.0.0"})
    upper = A.normalize_git_remote("git@github.com:Foo/Bar.git")
    lower = A.normalize_git_remote("https://github.com/foo/bar.git")
    assert upper == lower == UPPER_PROJECT_ID
    monkeypatch.setattr(A, "read_project_id", lambda _root: lower)

    lesson = eng.add_lesson("case-normalized anchor")
    eng.confirm_knowledge(
        lesson["id"],
        by="anchor",
        anchor_ref="dep:jest",
        anchor_project_id=upper,
    )

    report = eng.revalidate_anchors(str(repo))

    assert report["checked"] == 1
    assert report["valid"] == 1


def test_revalidate_anchors_flips_invalid_without_touching_last_validated_at(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_package_json(repo, {"jest": "^29.0.0"})
    monkeypatch.setattr(A, "read_project_id", lambda _root: PROJECT_ID)
    lesson = eng.add_lesson("jest-backed fact")
    updated = eng.confirm_knowledge(
        lesson["id"],
        by="anchor",
        anchor_ref="dep:jest",
        anchor_project_id=PROJECT_ID,
    )
    last_validated_at = updated["provenance"]["last_validated_at"]

    report = eng.revalidate_anchors(str(repo))
    still_valid = _stored_lesson(eng, lesson["id"])
    assert report["checked"] == 1
    assert report["valid"] == 1
    assert still_valid["provenance"]["anchor_status"] == "valid"
    assert P.compute_freshness(still_valid)["skip_decay"] is True

    _write_package_json(repo, {"vitest": "^2.0.0"})
    report = eng.revalidate_anchors(str(repo))
    invalid = _stored_lesson(eng, lesson["id"])

    assert report["checked"] == 1
    assert report["invalid"] == 1
    assert invalid["provenance"]["anchor_status"] == "invalid"
    assert "anchor_checked_at" in invalid["provenance"]
    assert invalid["provenance"]["last_validated_at"] == last_validated_at
    assert P.compute_freshness(invalid)["skip_decay"] is False


def test_revalidate_invalid_anchor_demotes_to_guess(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deanrie #13: an INVALIDATED anchor (the dep it was tied to is gone) is a
    definitive staleness event — the fact drops straight back to an unconfirmed
    guess, not onto the slow time-decay clock. The anchor_* fields are kept as
    evidence of why it was demoted."""
    repo = tmp_path / "repo"
    _write_package_json(repo, {"jest": "^29.0.0"})
    monkeypatch.setattr(A, "read_project_id", lambda _root: PROJECT_ID)
    lesson = eng.add_lesson("jest-backed fact")
    eng.confirm_knowledge(
        lesson["id"],
        by="anchor",
        anchor_ref="dep:jest",
        anchor_project_id=PROJECT_ID,
    )
    confirmed = _stored_lesson(eng, lesson["id"])
    assert confirmed["tier"] == "verified"
    assert confirmed["provenance"]["confirmation_source"] == "anchor"

    # jest removed -> the dependency anchor is definitively INVALID
    _write_package_json(repo, {"vitest": "^2.0.0"})
    report = eng.revalidate_anchors(str(repo))

    assert report["invalid"] == 1
    assert report["demoted"] == 1
    demoted = _stored_lesson(eng, lesson["id"])
    # dropped back to an unconfirmed guess
    assert demoted["tier"] == "staging"
    assert demoted["memory_state"] == "staging"
    assert "confirmation_source" not in demoted["provenance"]
    assert P.classify_freshness_source(demoted) == P.SOURCE_AGENT
    assert P.compute_freshness(demoted)["skip_decay"] is False
    # evidence of WHY it was demoted is retained
    assert demoted["provenance"]["anchor_status"] == "invalid"
    assert demoted["provenance"]["anchor_ref"] == "dep:jest"
    assert demoted["provenance"]["anchor_project_id"] == PROJECT_ID
    assert "anchor_checked_at" in demoted["provenance"]
    # derived fields are recomputed consistently with the demoted tier
    # (no tier=staging-but-still-approved inconsistency)
    assert demoted["approval_status"] == "pending"
    assert demoted["approval_required"] is True
    assert demoted["labeling"]["validation_state"] == "needs_review"


def test_revalidate_unknown_anchor_does_not_demote(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deanrie #13: an UNRESOLVABLE anchor (couldn't check) must NOT be treated
    as an invalidation. It falls back to time decay but keeps its confirmed tier
    and anchor confirmation, so an unresolvable miss never hides a real one."""
    repo = tmp_path / "repo"
    _write_package_json(repo, {"jest": "^29.0.0"})
    monkeypatch.setattr(A, "read_project_id", lambda _root: PROJECT_ID)
    lesson = eng.add_lesson("jest-backed fact")
    eng.confirm_knowledge(
        lesson["id"],
        by="anchor",
        anchor_ref="dep:jest",
        anchor_project_id=PROJECT_ID,
    )

    # no recognizable manifest left -> the check can't resolve -> UNKNOWN
    (repo / "package.json").unlink()
    report = eng.revalidate_anchors(str(repo))

    assert report["unknown"] == 1
    assert report["invalid"] == 0
    assert report["demoted"] == 0
    still = _stored_lesson(eng, lesson["id"])
    # NOT demoted: confirmed tier + anchor confirmation stay intact
    assert still["tier"] == "verified"
    assert still["provenance"]["confirmation_source"] == "anchor"
    assert still["provenance"]["anchor_status"] == "unknown"
    assert still["approval_status"] == "approved"
    # but freshness still falls back to time decay (anchor not currently valid)
    assert P.compute_freshness(still)["skip_decay"] is False


def test_revalidate_anchors_handles_decisions_and_playbooks(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_package_json(repo, {"jest": "^29.0.0"})
    monkeypatch.setattr(A, "read_project_id", lambda _root: PROJECT_ID)
    decision = eng.add_decision("Is Jest installed?", "yes")
    playbook = eng.add_playbook({
        "title": "Run Jest tests for anchor validation",
        "steps": ["npm test"],
    })
    eng.confirm_knowledge(
        decision["id"],
        by="anchor",
        anchor_ref="dep:jest",
        anchor_project_id=PROJECT_ID,
    )
    eng.confirm_knowledge(
        playbook["id"],
        by="anchor",
        anchor_ref="dep:jest",
        anchor_project_id=PROJECT_ID,
    )

    report = eng.revalidate_anchors(str(repo))
    assert report["checked"] == 2
    assert report["valid"] == 2

    _write_package_json(repo, {"vitest": "^2.0.0"})
    report = eng.revalidate_anchors(str(repo))
    stored_decision = _stored_decision(eng, decision["id"])
    stored_playbook = _stored_playbook(eng, playbook["id"])
    assert report["checked"] == 2
    assert report["invalid"] == 2
    assert stored_decision["provenance"]["anchor_status"] == "invalid"
    assert stored_playbook["provenance"]["anchor_status"] == "invalid"
    assert P.compute_freshness(stored_decision)["skip_decay"] is False
    assert P.compute_freshness(stored_playbook)["skip_decay"] is False
    # the demote applies to every knowledge type, not just lessons
    assert report["demoted"] == 2
    for stored in (stored_decision, stored_playbook):
        assert stored["tier"] == "staging"
        assert stored["approval_status"] == "pending"
        assert "confirmation_source" not in stored["provenance"]


def test_revalidate_file_anchor_invalid_demotes(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The demote path is anchor-type agnostic: a `file:` anchor (a config file
    the fact was tied to) demotes the same way when the file is gone."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "vitest.config.ts").write_text("export default {}\n", encoding="utf-8")
    monkeypatch.setattr(A, "read_project_id", lambda _root: PROJECT_ID)
    lesson = eng.add_lesson("fact tied to the vitest config file")
    eng.confirm_knowledge(
        lesson["id"], by="anchor", anchor_ref="file:vitest.config.ts",
        anchor_project_id=PROJECT_ID,
    )
    assert eng.revalidate_anchors(str(repo))["valid"] == 1

    (repo / "vitest.config.ts").unlink()
    report = eng.revalidate_anchors(str(repo))

    assert report["invalid"] == 1
    assert report["demoted"] == 1
    demoted = _stored_lesson(eng, lesson["id"])
    assert demoted["tier"] == "staging"
    assert "confirmation_source" not in demoted["provenance"]
    assert demoted["provenance"]["anchor_status"] == "invalid"
    assert demoted["provenance"]["anchor_ref"] == "file:vitest.config.ts"


def test_revalidate_mixed_batch_demotes_only_the_invalid(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One revalidate over a mix demotes ONLY the invalid anchor; the valid one
    stays verified and trigger-bound. Selective, not blanket."""
    repo = tmp_path / "repo"
    _write_package_json(repo, {"jest": "^29.0.0"})  # jest present, left-pad absent
    monkeypatch.setattr(A, "read_project_id", lambda _root: PROJECT_ID)
    keep = eng.add_lesson("jest is wired in")
    drop = eng.add_lesson("left-pad is wired in")
    eng.confirm_knowledge(keep["id"], by="anchor", anchor_ref="dep:jest",
                          anchor_project_id=PROJECT_ID)
    eng.confirm_knowledge(drop["id"], by="anchor", anchor_ref="dep:left-pad",
                          anchor_project_id=PROJECT_ID)

    report = eng.revalidate_anchors(str(repo))

    assert report["checked"] == 2
    assert report["valid"] == 1
    assert report["invalid"] == 1
    assert report["demoted"] == 1
    kept = _stored_lesson(eng, keep["id"])
    dropped = _stored_lesson(eng, drop["id"])
    assert kept["tier"] == "verified"
    assert kept["provenance"]["confirmation_source"] == "anchor"
    assert P.compute_freshness(kept)["skip_decay"] is True
    assert dropped["tier"] == "staging"
    assert "confirmation_source" not in dropped["provenance"]


def test_anchor_invalidation_is_one_way_recovery_needs_reconfirm(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Demotion is one-way by design: a dependency reappearing does NOT auto-
    restore trust (a returned dep doesn't re-verify the claim's content). The
    demoted entry is no longer an anchor entry, so future rechecks skip it; only
    an explicit re-confirm restores it. The anti-false-confidence trust gate."""
    repo = tmp_path / "repo"
    _write_package_json(repo, {"jest": "^29.0.0"})
    monkeypatch.setattr(A, "read_project_id", lambda _root: PROJECT_ID)
    lesson = eng.add_lesson("jest-backed fact")
    eng.confirm_knowledge(lesson["id"], by="anchor", anchor_ref="dep:jest",
                          anchor_project_id=PROJECT_ID)

    # dep removed -> demote
    _write_package_json(repo, {"vitest": "^2.0.0"})
    assert eng.revalidate_anchors(str(repo))["demoted"] == 1
    demoted = _stored_lesson(eng, lesson["id"])
    assert demoted["tier"] == "staging"
    assert "confirmation_source" not in demoted["provenance"]

    # dep restored -> NOT auto-restored: the entry is no longer an anchor entry,
    # so revalidate skips it entirely (nothing checked, nothing changed).
    _write_package_json(repo, {"jest": "^29.0.0"})
    report = eng.revalidate_anchors(str(repo))
    assert report["checked"] == 0
    still = _stored_lesson(eng, lesson["id"])
    assert still["tier"] == "staging"
    assert "confirmation_source" not in still["provenance"]
    assert still["provenance"]["anchor_status"] == "invalid"

    # A deliberate owner re-confirm re-establishes the anchor binding and puts
    # the fact back on the trigger (off the clock). Note: confirm stamps the
    # trust source but does NOT itself promote the tier — the entry comes back
    # as staging + anchor-bound, and promotion to verified stays a separate
    # deliberate step (no silent jump back to verified just from re-confirming).
    eng.confirm_knowledge(lesson["id"], by="anchor", anchor_ref="dep:jest",
                          anchor_project_id=PROJECT_ID)
    restored = _stored_lesson(eng, lesson["id"])
    assert restored["provenance"]["confirmation_source"] == "anchor"
    assert restored["provenance"]["anchor_status"] == "valid"
    assert restored["tier"] == "staging"  # confirm re-binds; promote is separate
    assert P.compute_freshness(restored)["skip_decay"] is True


def test_revalidate_anchors_is_idempotent(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_package_json(repo, {"jest": "^29.0.0"})
    monkeypatch.setattr(A, "read_project_id", lambda _root: PROJECT_ID)
    lesson = eng.add_lesson("idempotent anchor check")
    eng.confirm_knowledge(
        lesson["id"],
        by="anchor",
        anchor_ref="dep:jest",
        anchor_project_id=PROJECT_ID,
    )

    first = eng.revalidate_anchors(str(repo))
    second = eng.revalidate_anchors(str(repo))

    comparable_keys = {"checked", "valid", "invalid", "unknown", "skipped_mismatch", "skipped_legacy", "project_id"}
    assert {key: first[key] for key in comparable_keys} == {
        key: second[key] for key in comparable_keys
    }


def test_revalidate_anchors_skips_mismatched_project_without_mutation(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(A, "read_project_id", lambda _root: "github.com/other/repo")
    lesson = eng.add_lesson("different project anchor")
    eng.confirm_knowledge(
        lesson["id"],
        by="anchor",
        anchor_ref="dep:jest",
        anchor_project_id=PROJECT_ID,
    )
    before = dict(_stored_lesson(eng, lesson["id"])["provenance"])

    report = eng.revalidate_anchors(str(repo))

    assert report["checked"] == 0
    assert report["skipped_mismatch"] == 1
    assert _stored_lesson(eng, lesson["id"])["provenance"] == before


def test_revalidate_anchors_skips_or_adopts_legacy_without_checking_status(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(A, "read_project_id", lambda _root: PROJECT_ID)
    lesson = eng.add_lesson("legacy anchor without project id")
    eng.confirm_knowledge(lesson["id"], by="anchor", anchor_ref="dep:jest")

    report = eng.revalidate_anchors(str(repo))
    skipped = _stored_lesson(eng, lesson["id"])
    assert report["checked"] == 0
    assert report["skipped_legacy"] == 1
    assert "anchor_project_id" not in skipped["provenance"]

    report = eng.revalidate_anchors(str(repo), adopt_legacy=True)
    adopted = _stored_lesson(eng, lesson["id"])
    assert report["checked"] == 0
    assert adopted["provenance"]["anchor_project_id"] == PROJECT_ID
    assert adopted["provenance"]["anchor_status"] == "valid"
    assert "anchor_checked_at" not in adopted["provenance"]


def test_anchors_check_cli_reports_revalidation_json(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from piia_engram.setup_wizard import run_anchors

    repo = tmp_path / "repo"
    _write_package_json(repo, {"vitest": "^2.0.0"})
    monkeypatch.setattr(A, "read_project_id", lambda _root: PROJECT_ID)
    lesson = eng.add_lesson("cli anchor check")
    eng.confirm_knowledge(
        lesson["id"],
        by="anchor",
        anchor_ref="dep:jest",
        anchor_project_id=PROJECT_ID,
    )

    assert run_anchors(["check", "--root", str(repo), "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["project_id"] == PROJECT_ID
    assert report["invalid"] == 1


def test_anchors_check_cli_non_git_root_returns_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from piia_engram.setup_wizard import run_anchors

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram"))
    root = tmp_path / "not-a-repo"
    root.mkdir()

    assert run_anchors(["check", "--root", str(root)]) == 1

    assert "不是 git 仓库或没有 origin 远程" in capsys.readouterr().out


def test_anchors_check_cli_non_git_root_json_returns_report_and_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from piia_engram.setup_wizard import run_anchors

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram"))
    root = tmp_path / "not-a-repo"
    root.mkdir()

    assert run_anchors(["check", "--root", str(root), "--json"]) == 1

    report = json.loads(capsys.readouterr().out)
    assert report["project_id"] is None


def test_agent_writes_strip_anchor_project_id_with_other_freshness_claims(
    eng: Engram,
) -> None:
    stored = eng.add_lesson(
        {
            "summary": "agent cannot bind anchor to a project",
            "domain": "freshness",
            "provenance": {
                "source_agent": "codex",
                "confirmation_source": "anchor",
                "anchor_status": "valid",
                "anchor_project_id": PROJECT_ID,
            },
        }
    )

    assert stored["provenance"]["source_agent"] == "codex"
    assert "confirmation_source" not in stored["provenance"]
    assert "anchor_status" not in stored["provenance"]
    assert "anchor_project_id" not in stored["provenance"]

    payload = {
        "summary": "strip helper keeps only non-trust provenance",
        "provenance": {
            "source_agent": "codex",
            "confirmation_source": "anchor",
            "anchor_status": "valid",
            "anchor_project_id": PROJECT_ID,
        },
    }
    strip_untrusted_trust_fields(payload)
    assert payload["provenance"] == {"source_agent": "codex"}
    assert "provenance.anchor_project_id" in UNTRUSTED_TRUST_FIELDS


def test_playbook_writes_strip_untrusted_freshness_provenance(
    eng: Engram,
) -> None:
    stored = eng.add_playbook(
        {
            "title": "agent playbook cannot self-certify anchor",
            "steps": ["run tests"],
            "provenance": {
                "source_agent": "codex",
                "confirmation_source": "anchor",
                "anchor_status": "valid",
                "anchor_project_id": PROJECT_ID,
            },
        }
    )

    assert stored["provenance"]["source_agent"] == "codex"
    assert "confirmation_source" not in stored["provenance"]
    assert "anchor_status" not in stored["provenance"]
    assert "anchor_project_id" not in stored["provenance"]


def test_playbook_internal_provenance_opt_in_preserves_anchor_fields(
    eng: Engram,
) -> None:
    stored = eng.add_playbook(
        {
            "title": "internal playbook anchor stamp",
            "steps": ["run tests"],
            "provenance": {
                "source_agent": "owner",
                "confirmation_source": "anchor",
                "anchor_status": "valid",
                "anchor_project_id": PROJECT_ID,
            },
        },
        _allow_internal_provenance=True,
    )

    assert stored["provenance"]["confirmation_source"] == "anchor"
    assert stored["provenance"]["anchor_status"] == "valid"
    assert stored["provenance"]["anchor_project_id"] == PROJECT_ID
    assert "_allow_internal_provenance" not in stored


def test_public_freshness_status_remains_four_state_after_anchor_revalidation(
    tmp_path: Path,
    eng: Engram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_package_json(repo, {"vitest": "^2.0.0"})
    monkeypatch.setattr(A, "read_project_id", lambda _root: PROJECT_ID)
    lesson = eng.add_lesson("public freshness status remains four-state")
    eng.confirm_knowledge(
        lesson["id"],
        by="anchor",
        anchor_ref="dep:jest",
        anchor_project_id=PROJECT_ID,
    )

    eng.revalidate_anchors(str(repo))
    stored = _stored_lesson(eng, lesson["id"])
    freshness = P.compute_freshness(stored)

    assert freshness["freshness_status"] in {P.FRESH, P.AGING, P.STALE, P.UNKNOWN}
    assert freshness["freshness_status"] not in {"valid", "invalid", "unknown_anchor"}
