"""Tests for governance persistence (src/piia_engram/governance_store.py)."""

from __future__ import annotations

import pytest

from piia_engram.governance_store import GrantStore, RelationStore


# ── GrantStore ───────────────────────────────────────────────────────────


def test_default_classification_when_no_explicit_grant(tmp_path):
    gs = GrantStore(tmp_path)
    assert gs.trust_level_for("codex", client_type="codex") == "trusted-local"
    assert gs.trust_level_for("x", client_type="unknown-tool") == "read-only-external"
    assert gs.trust_level_for("self", client_type="self") == "private-self"


def test_explicit_grant_overrides_classification(tmp_path):
    gs = GrantStore(tmp_path)
    gs.set_grant("some-agent", "private-self")
    assert gs.trust_level_for("some-agent", client_type="unknown") == "private-self"


def test_set_grant_rejects_unknown_level(tmp_path):
    gs = GrantStore(tmp_path)
    with pytest.raises(ValueError):
        gs.set_grant("a", "super-admin")


def test_revoke_and_is_revoked(tmp_path):
    gs = GrantStore(tmp_path)
    assert gs.is_revoked("codex") is False
    gs.revoke("codex")
    assert gs.is_revoked("codex") is True


def test_granting_clears_revocation(tmp_path):
    gs = GrantStore(tmp_path)
    gs.revoke("codex")
    gs.set_grant("codex", "trusted-local")
    assert gs.is_revoked("codex") is False


def test_grants_persist_across_instances(tmp_path):
    GrantStore(tmp_path).set_grant("cursor", "trusted-local")
    assert GrantStore(tmp_path).trust_level_for("cursor") == "trusted-local"


def test_set_grant_fails_closed_on_corrupt_file(tmp_path):
    # Codex round-4 P1: a corrupt grants.json must NOT be silently overwritten
    # with default (which would wipe real revoked/grants state). Fail closed.
    from piia_engram.storage import DataCorruptionError
    gs = GrantStore(tmp_path)
    gs.set_grant("a", "trusted-local")
    gs.revoke("codex")
    gs.path.write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(DataCorruptionError):
        gs.set_grant("c", "trusted-local")
    # original file NOT clobbered with a default {grants:{c:...}} — corrupt
    # content remains (a backup was made for recovery), revoked not wiped.
    assert "not valid json" in gs.path.read_text(encoding="utf-8")


def test_add_relation_fails_closed_on_corrupt_file(tmp_path):
    from piia_engram.storage import DataCorruptionError
    rs = RelationStore(tmp_path)
    rs.add_relation("a", "led_to", "b")
    rs.path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(DataCorruptionError):
        rs.add_relation("c", "led_to", "d")
    assert "not json at all" in rs.path.read_text(encoding="utf-8")


def test_list_grants_shape(tmp_path):
    gs = GrantStore(tmp_path)
    gs.set_grant("a", "trusted-local")
    gs.revoke("b")
    out = gs.list_grants()
    assert out["grants"] == {"a": "trusted-local"} and out["revoked"] == ["b"]


# ── RelationStore ────────────────────────────────────────────────────────


def test_add_and_read_relations(tmp_path):
    rs = RelationStore(tmp_path)
    assert rs.add_relation("idea", "led_to", "plan") is True
    assert rs.add_relation("plan", "implemented_by", "pr1") is True
    edges = rs.all_edges()
    assert {"src": "idea", "rel": "led_to", "dst": "plan"} in edges
    assert len(edges) == 2


def test_add_relation_rejects_invalid(tmp_path):
    rs = RelationStore(tmp_path)
    assert rs.add_relation("a", "bogus", "b") is False     # bad rel
    assert rs.add_relation("a", "led_to", "a") is False     # self loop
    assert rs.all_edges() == []


def test_add_relation_is_idempotent(tmp_path):
    rs = RelationStore(tmp_path)
    assert rs.add_relation("a", "led_to", "b") is True
    assert rs.add_relation("a", "led_to", "b") is False     # duplicate
    assert len(rs.all_edges()) == 1


def test_remove_relation(tmp_path):
    rs = RelationStore(tmp_path)
    rs.add_relation("a", "led_to", "b")
    assert rs.remove_relation("a", "led_to", "b") is True
    assert rs.remove_relation("a", "led_to", "b") is False  # already gone
    assert rs.all_edges() == []


def test_edges_for_node(tmp_path):
    rs = RelationStore(tmp_path)
    rs.add_relation("a", "led_to", "b")
    rs.add_relation("b", "led_to", "c")
    rs.add_relation("x", "led_to", "y")
    touching_b = rs.edges_for("b")
    assert len(touching_b) == 2
    assert all("b" in (e["src"], e["dst"]) for e in touching_b)


def test_concurrent_add_relation_no_lost_updates(tmp_path):
    # Codex round-3 P1: the invariant is NO LOST UPDATES — every write that
    # SUCCEEDS must be persisted. (Under an artificial thundering herd a write
    # may hit the lock timeout and raise; that's correct fail-closed behavior,
    # not data loss — so we assert against successes, not against N.)
    import threading
    rs = RelationStore(tmp_path)
    N = 20
    successes, errors = [], []

    def worker(i):
        try:
            if rs.add_relation(f"n{i}", "led_to", f"m{i}"):
                successes.append(i)
        except RuntimeError:
            pass  # lock-timeout under contention is acceptable (no data loss)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    # no lost updates: persisted count == successful count
    assert len(rs.all_edges()) == len(successes)
    assert len(successes) >= 1


def test_concurrent_set_grant_no_lost_updates(tmp_path):
    import threading
    gs = GrantStore(tmp_path)
    N = 20
    successes, errors = [], []

    def worker(i):
        try:
            gs.set_grant(f"agent{i}", "trusted-local")
            successes.append(i)
        except RuntimeError:
            pass  # lock-timeout under contention is acceptable
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    # no lost updates: every successful grant is present
    grants = gs.list_grants()["grants"]
    assert len(grants) == len(successes)
    for i in successes:
        assert grants[f"agent{i}"] == "trusted-local"
    assert len(successes) >= 1


def test_relations_feed_build_thread(tmp_path):
    # end-to-end: stored relations reconstruct a thread
    from piia_engram.decision_thread import build_thread
    rs = RelationStore(tmp_path)
    rs.add_relation("idea", "led_to", "decision")
    rs.add_relation("decision", "implemented_by", "pr1")
    t = build_thread("idea", rs.all_edges())
    assert [r["id"] for r in t["order"]] == ["idea", "decision", "pr1"]
    assert t["heads"] == ["pr1"]
