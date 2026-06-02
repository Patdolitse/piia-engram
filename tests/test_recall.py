"""Tests for the Recall Surface v1 pure aggregator (src/piia_engram/recall.py).

The aggregator is store-free, so these exercise assembly/dedup/projection/trim
with plain fixtures — exactly the unit-test plan in
docs/specs/recall-surface-v1.md §6 step 1.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from piia_engram import recall


def _lesson(**kw):
    base = {"id": kw.pop("id", "L1"), "summary": kw.pop("summary", "a durable lesson")}
    base.update(kw)
    return base


def _decision(**kw):
    base = {
        "id": kw.pop("id", "D1"),
        "question": kw.pop("question", "db choice"),
        "choice": kw.pop("choice", "postgres"),
    }
    base.update(kw)
    return base


class TestAssembly:
    def test_basic_shape_is_stable(self):
        payload = recall.build_recall_payload(
            identity={"role": "dev"},
            recent_activity={"daily_log_digest": ["did x"]},
            relevant_knowledge=[_lesson()],
            project="/proj",
            query="db",
        )
        assert set(payload) == {"identity", "recent_activity", "knowledge", "meta"}
        assert payload["identity"] == {"role": "dev"}
        assert payload["recent_activity"] == {"daily_log_digest": ["did x"]}
        assert payload["meta"]["project"] == "/proj"
        assert payload["meta"]["query"] == "db"
        assert payload["meta"]["governance"]["excluded_count"] == 0

    def test_missing_inputs_default_to_empty(self):
        payload = recall.build_recall_payload()
        assert payload["identity"] == {}
        assert payload["recent_activity"] == {}
        assert payload["knowledge"] == []

    def test_lesson_and_decision_projection(self):
        payload = recall.build_recall_payload(
            relevant_knowledge=[_lesson(summary="s1"), _decision(question="q", choice="c")],
            include_freshness=False,
        )
        kinds = {item["type"] for item in payload["knowledge"]}
        assert kinds == {"lesson", "decision"}
        lesson = next(i for i in payload["knowledge"] if i["type"] == "lesson")
        decision = next(i for i in payload["knowledge"] if i["type"] == "decision")
        assert lesson["summary"] == "s1"
        assert decision["question"] == "q" and decision["choice"] == "c"


class TestProjectionHidesInternals:
    def test_internal_bookkeeping_not_leaked(self):
        entry = _lesson(
            summary="visible",
            access_count=99,
            risk_flags=["danger"],
            tier="verified",
            _score=0.42,
        )
        payload = recall.build_recall_payload(
            relevant_knowledge=[entry], include_freshness=False
        )
        item = payload["knowledge"][0]
        assert "access_count" not in item
        assert "risk_flags" not in item
        assert "_score" not in item
        assert "tier" not in item
        assert item["summary"] == "visible"


class TestDedup:
    def test_dedup_by_id_across_relevant_and_query(self):
        payload = recall.build_recall_payload(
            relevant_knowledge=[_lesson(id="X", summary="from relevant")],
            query_knowledge=[_lesson(id="X", summary="dup from query"), _lesson(id="Y")],
            include_freshness=False,
        )
        ids_seen = [i["summary"] for i in payload["knowledge"]]
        # X appears once (relevant wins), plus Y.
        assert len(payload["knowledge"]) == 2
        assert "from relevant" in ids_seen
        assert "dup from query" not in ids_seen


class TestProvenanceAndFreshness:
    def test_source_agent_falls_back_to_source_tool(self):
        entry = _lesson(source_tool="codex")
        payload = recall.build_recall_payload(
            relevant_knowledge=[entry], include_freshness=False
        )
        assert payload["knowledge"][0]["provenance"]["source_agent"] == "codex"

    def test_provenance_subset_carried(self):
        entry = _lesson(provenance={"source_agent": "claude_code", "run_id": "r1",
                                    "last_validated_at": "2026-05-01T00:00:00+00:00"})
        payload = recall.build_recall_payload(
            relevant_knowledge=[entry], include_freshness=False
        )
        prov = payload["knowledge"][0]["provenance"]
        assert prov["source_agent"] == "claude_code"
        assert prov["run_id"] == "r1"
        assert prov["last_validated_at"].startswith("2026-05-01")

    def test_freshness_attached_only_when_requested(self):
        now = datetime(2026, 6, 3, tzinfo=timezone.utc)
        recent = (now - timedelta(days=2)).isoformat()
        on = recall.build_recall_payload(
            relevant_knowledge=[_lesson(created_at=recent)],
            include_freshness=True,
            now=now,
        )
        off = recall.build_recall_payload(
            relevant_knowledge=[_lesson(created_at=recent)],
            include_freshness=False,
            now=now,
        )
        assert on["knowledge"][0]["freshness"]["freshness_status"] == "fresh"
        assert "freshness" not in off["knowledge"][0]


class TestTokenBudgetTrim:
    def test_trims_and_counts_excluded(self):
        items = [_lesson(id=f"L{i}", summary="x" * 200) for i in range(20)]
        payload = recall.build_recall_payload(
            relevant_knowledge=items, token_budget=60, include_freshness=False
        )
        assert len(payload["knowledge"]) < 20
        assert payload["meta"]["governance"]["excluded_count"] > 0
        assert (len(payload["knowledge"])
                + payload["meta"]["governance"]["excluded_count"]) == 20

    def test_at_least_one_item_survives_tiny_budget(self):
        payload = recall.build_recall_payload(
            relevant_knowledge=[_lesson(summary="y" * 500)],
            token_budget=1,
            include_freshness=False,
        )
        assert len(payload["knowledge"]) == 1


class TestGovernanceMeta:
    def test_trust_level_passthrough(self):
        payload = recall.build_recall_payload(
            relevant_knowledge=[_lesson()],
            governance={"trust_level": "trusted-local"},
        )
        assert payload["meta"]["governance"]["trust_level"] == "trusted-local"

    def test_inputs_not_mutated(self):
        entry = _lesson()
        snapshot = dict(entry)
        recall.build_recall_payload(relevant_knowledge=[entry], include_freshness=True)
        assert entry == snapshot  # projection copies; never mutates source
