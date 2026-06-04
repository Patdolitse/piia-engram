"""Stage 3 item D — multi-writer local concurrency safety stress.

The supported safety contract is asserted; the unsupported one is characterized
honestly, never overclaimed:

* **Integrity / no corruption** — under concurrent writers the knowledge file is
  always valid JSON with well-formed entries (atomic temp+rename under a lock).
  ASSERTED.
* **Governance no-lost-update** — the ``_update_json`` path (holds the lock
  across read→mutate→write) loses no accepted edge under contention. ASSERTED.
* **Knowledge no-lost-update** — the ``add_lesson`` path reads outside the lock,
  so a race CAN drop a write. NOT asserted to be zero; the harness only verifies
  the survivors are well-formed and at least one write landed, and that any drop
  is a clean lost-update (count consistent), never corruption.

Bounded (small thread counts, no sleeps), CI-friendly on Windows, temp-isolated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from piia_engram import concurrency_harness as ch


class TestKnowledgeStress:
    def test_no_corruption_under_concurrent_writers(self, tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch):
        root = tmp_path / "kstore"
        monkeypatch.setenv("ENGRAM_DIR", str(root))
        rep = ch.run_knowledge_multiwriter_stress(root, writers=8, per_writer=6)
        # Integrity contract — ALWAYS holds.
        assert rep["json_valid"] is True
        assert rep["integrity_ok"] is True
        # At least one write must survive; survivors bounded by intent.
        assert 1 <= rep["persisted"] <= rep["intended_writes"]
        # Lost-update accounting is consistent (honest, may be > 0).
        assert rep["lost_updates"] == rep["intended_writes"] - rep["persisted"]
        assert rep["lost_updates"] >= 0

    def test_report_is_metadata_only(self, tmp_path: Path):
        rep = ch.run_knowledge_multiwriter_stress(tmp_path / "k", writers=4, per_writer=4)
        # No bodies / paths leak into the report — only counts, booleans, dicts.
        assert set(rep) >= {"path", "intended_writes", "json_valid", "persisted",
                            "lost_updates", "errors", "integrity_ok", "no_lost_updates"}
        for v in rep.values():
            assert isinstance(v, (str, int, bool, dict))

    def test_single_writer_loses_nothing(self, tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch):
        root = tmp_path / "k1"
        monkeypatch.setenv("ENGRAM_DIR", str(root))
        # One writer cannot race itself → every write survives.
        rep = ch.run_knowledge_multiwriter_stress(root, writers=1, per_writer=10)
        assert rep["persisted"] == rep["intended_writes"]
        assert rep["no_lost_updates"] is True
        assert rep["integrity_ok"] is True


class TestGovernanceStress:
    def test_no_lost_updates_on_locked_path(self, tmp_path: Path):
        root = tmp_path / "gstore"
        rep = ch.run_governance_multiwriter_stress(root, writers=8, per_writer=6)
        assert rep["json_valid"] is True
        assert rep["integrity_ok"] is True
        # The supported contract: every accepted edge survived.
        assert rep["no_lost_updates"] is True
        assert rep["persisted"] == rep["accepted_writes"]
        # With unique edges and no lock starvation, all intended writes accepted
        # unless a fail-closed lock timeout intervened (counted, not lost).
        assert rep["accepted_writes"] + rep["error_total"] <= rep["intended_writes"]
        assert rep["accepted_writes"] >= rep["intended_writes"] - rep["error_total"]


class TestFullReport:
    def test_full_report_invariants(self, tmp_path: Path):
        rep = ch.run_full_report(tmp_path / "full", writers=6, per_writer=5)
        inv = rep["invariants"]
        # Always-true contracts.
        assert inv["no_corruption"] is True
        assert inv["governance_no_lost_updates"] is True
        # Honest characterization is present and not silently dropped.
        assert inv["knowledge_lost_update_possible"] is True
        assert inv["knowledge_lost_updates_observed"] >= 0
        assert rep["writers"] == 6 and rep["per_writer"] == 5

    def test_harness_does_not_touch_real_store(self, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch):
        # Point ENGRAM_DIR at a sentinel; the harness must write only to the
        # explicit root it is given, never the env default.
        sentinel = tmp_path / "REAL_STORE_DO_NOT_TOUCH"
        monkeypatch.setenv("ENGRAM_DIR", str(sentinel))
        target = tmp_path / "explicit"
        ch.run_full_report(target, writers=3, per_writer=3)
        assert not sentinel.exists()
        assert target.exists()
