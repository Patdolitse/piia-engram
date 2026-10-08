"""Identity changes proposed by agents wait for local review in every mode."""
import asyncio
import io
import json

import pytest

from piia_engram import mcp_server as M, review_cli as R, review_interactive as I
from piia_engram import write_provenance as P, tombstones
from piia_engram.cli_commands import run_review as _run_review
from piia_engram.compat import import_from_openclaw
from piia_engram.core import Engram


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setattr(M, "_engram", eng)
    monkeypatch.setattr(M, "_session", M._SessionTracker())
    return eng


FIELDS = [("profile", {"role": "pending-role"}),
          ("preferences", {"communication": "pending-style"}),
          ("work_style", {"communication": "pending-style"}),
          ("quality_standards", {"rules": ["pending-rule"]}),
          ("trust_boundaries", {"restricted_fields": ["role"]})]


@pytest.mark.parametrize("mode", ["default", "strict"])
@pytest.mark.parametrize("field,updates", FIELDS)
def test_mcp_always_queues(eng, monkeypatch, mode, field, updates):
    before = getattr(eng, "get_" + field)()
    monkeypatch.setenv("ENGRAM_APPROVAL", mode)
    result = json.loads(asyncio.run(M.update_identity(field, json.dumps(updates))))
    assert result["status"] == "pending"
    assert result["success"] is True and result["changed"] is False
    assert getattr(eng, "get_" + field)() == before
    row = eng.get_identity_proposals()[0]
    assert row["id"] == result["id"] and row["field"] == field
    assert row["updates"] == updates


@pytest.mark.parametrize("field,updates", FIELDS)
def test_core_mcp_origin_cannot_bypass_queue(eng, field, updates):
    before = getattr(eng, "get_" + field)()
    with P.origin_scope("mcp", client_name="claude-code"):
        getattr(eng, "update_" + field)(updates)
    assert getattr(eng, "get_" + field)() == before
    assert eng.get_identity_proposals()[0]["provenance"]["client"] == "claude_code"


def marks(row, action="approve", **kwargs):
    return [{"id": row["id"], "mark": action, "expected_version": row["version"], **kwargs}]


def apply(eng, row, action="approve"):
    return R.apply_marks(eng, marks(row, action), {"operator": "owner", "isatty": True})


def test_approve_preview_conflict_and_replay(eng):
    eng.update_profile({"role": "old", "description": "keep"})
    row = eng.propose_identity("profile", {"role": "new", "description": "add"})
    assert row["before"]["role"] == "old" and row["after"]["description"] == "keep add"
    assert R.preview_marks(eng, marks(row))["items"][0]["status"] == "planned"
    assert eng.get_profile()["role"] == "old"
    eng.update_profile({"language": "English"})  # unrelated change is preserved
    assert apply(eng, row)["items"][0]["status"] == "applied"
    assert eng.get_profile()["role"] == "new" and eng.get_profile()["language"] == "English"
    assert apply(eng, row)["items"][0]["status"] == "already_applied"
    row = eng.propose_identity("profile", {"role": "later"})
    eng.update_profile({"role": "owner-local"})
    assert apply(eng, row)["items"][0]["status"] == "identity_conflict"
    assert eng.get_profile()["role"] == "owner-local"


def test_reject_has_text_free_tombstone(eng):
    before = eng.get_profile()
    row = eng.propose_identity("profile", {"role": "rejected-private-text"})
    assert apply(eng, row, "reject")["items"][0]["status"] == "applied"
    assert eng.get_profile() == before and eng.get_identity_proposals() == []
    assert "rejected-private-text" not in json.dumps(tombstones.load(eng.root))
    assert "rejected-private-text" not in (eng.root / "identity" / "proposals.json").read_text()
    assert apply(eng, row, "reject")["items"][0]["status"] == "already_applied"
    assert eng.propose_identity("profile", row["updates"])["status"] == "rejected_before"


def test_queue_excluded_from_recall_and_review_displays_diff(eng, tmp_path, capsys):
    eng.update_profile({"role": "approved-role"})
    row = eng.propose_identity("profile", {"role": "not-yet-approved-role"})
    context = asyncio.run(M.get_user_context())
    assert "not-yet-approved-role" not in context
    assert _run_review([]) == 0
    assert row["id"] in capsys.readouterr().out
    assert _run_review(["show", row["id"]]) == 0
    text = capsys.readouterr().out
    assert "approved-role" in text and "not-yet-approved-role" in text and "old" in text and "new" in text
    out = tmp_path / "review"
    assert R.run_export(["--out", str(out)]) == 0
    text = (out / "review.md").read_text()
    assert "old" in text and "new" in text and row["id"] in text
    screen, _ = I.card(1, 1, "identity", row, eng=eng, lookup={}, edges=[])
    assert "approved-role" in "\n".join(screen) and "not-yet-approved-role" in "\n".join(screen)


def test_mcp_cannot_decide_identity(eng):
    row = eng.propose_identity("profile", {"role": "proposal"})
    with P.origin_scope("mcp", client_name="claude-code"):
        for action in ("approve", "reject"):
            assert eng.review_identity_proposal(row["id"], action)["status"] == "local_review_only"
    assert len(eng.get_identity_proposals()) == 1


def test_openclaw_queues_all_identity(eng, tmp_path):
    user, soul = tmp_path / "USER.md", tmp_path / "SOUL.md"
    user.write_text("- Role: imported-role\n- Language: English\n", encoding="utf-8")
    soul.write_text("## Work preferences\n- pace: slow\n## Quality standards\n- imported-rule\n", encoding="utf-8")
    before = [getattr(eng, "get_" + f)() for f in ("profile", "preferences", "quality_standards")]
    result = import_from_openclaw(eng, user_path=str(user), soul_path=str(soul))
    assert not result.get("error")
    assert [getattr(eng, "get_" + f)() for f in ("profile", "preferences", "quality_standards")] == before
    rows = eng.get_identity_proposals()
    assert {r["field"] for r in rows} == {"profile", "preferences", "quality_standards"}
    for row in rows:
        assert apply(eng, row)["items"][0]["status"] == "applied"
    assert eng.get_profile()["role"] == "imported-role"


def test_local_updates_remain_direct(eng):
    for field, updates in FIELDS:
        getattr(eng, "update_" + field)(updates)
        assert all(getattr(eng, "get_" + field)().get(k) == v for k, v in updates.items())
    assert eng.get_identity_proposals() == []


def test_read_only_preview_and_version_guard(eng):
    row = eng.propose_identity("profile", {"role": "new"})
    readonly = Engram(root=eng.root, read_only=True)
    assert R.preview_marks(readonly, marks(row))["items"][0]["status"] == "planned"
    assert eng.review_identity_proposal(row["id"], "approve", expected_version=2)["status"] == "version_conflict"
    assert eng.get_profile().get("role") != "new"


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_interrupted_decision_replays(eng, monkeypatch, action):
    from piia_engram import identity_review as Q
    row = eng.propose_identity("profile", {"role": "new"})
    original = Q._save
    def fail(*args):
        raise RuntimeError("interrupted after identity or rejection write")
    monkeypatch.setattr(Q, "_save", fail)
    with pytest.raises(RuntimeError):
        apply(eng, row, action)
    monkeypatch.setattr(Q, "_save", original)
    assert apply(eng, row, action)["items"][0]["status"] == "applied"
    assert eng.get_identity_proposals() == []
    assert (eng.get_profile().get("role") == "new") == (action == "approve")


def test_invalid_updates_and_marks_do_not_write(eng):
    for updates in ([], {}, {"_provenance": {}}, {"updated_at": "fake"}):
        result = json.loads(asyncio.run(M.update_identity("profile", json.dumps(updates))))
        assert result.get("error")
    row = eng.propose_identity("profile", {"role": "new"})
    assert R.apply_marks(eng, [{"id": row["id"], "mark": "supersede", "target": "other"}],
                         {"operator": "owner"})["items"][0]["status"] == "invalid_action"
    assert len(eng.get_identity_proposals()) == 1


def test_preferences_approval_preserves_legacy_fallback(eng):
    eng.update_work_style({"preferences": {"pace": "steady"}, "communication": "old"})
    row = eng.propose_identity("preferences", {"communication": "new"})
    assert apply(eng, row)["items"][0]["status"] == "applied"
    assert eng.get_preferences()["work_patterns"] == {"pace": "steady"}
    assert eng.get_preferences()["communication"] == "new"


def test_proposal_body_encrypted_when_identity_encryption_enabled(eng):
    from piia_engram.crypto import EncryptionEngine, HAS_CRYPTO
    if not HAS_CRYPTO:
        pytest.skip("cryptography not installed")
    eng._crypto = EncryptionEngine(secret="test-passphrase")
    eng.update_profile({"email": "approved@example.invalid"})
    row = eng.propose_identity("profile", {"email": "proposed@example.invalid"})
    stored = (eng.root / "identity" / "proposals.json").read_text(encoding="utf-8")
    assert "approved@example.invalid" not in stored and "proposed@example.invalid" not in stored
    assert eng.get_identity_proposals()[0]["after"]["email"] == "proposed@example.invalid"
    assert apply(eng, row)["items"][0]["status"] == "applied"
    assert eng.get_profile()["email"] == "proposed@example.invalid"


def test_interactive_approval_uses_shared_review(eng, monkeypatch):
    row = eng.propose_identity("profile", {"role": "new"})
    class Terminal(io.StringIO):
        def isatty(self):
            return True
    screen = Terminal()
    monkeypatch.setattr(I, "_engram", lambda read_only: Engram(root=eng.root, read_only=read_only))
    assert I.run([], stdin=Terminal("a\ny\n"), stdout=screen) == 0
    assert "old" in screen.getvalue() and "new" in screen.getvalue()
    assert eng.get_profile()["role"] == "new" and eng.get_identity_proposals() == []
