"""Tests for the pure version-chain read/report scaffold (Phase 6)."""

from __future__ import annotations

from piia_engram import version_chain as vc


def _edge(src, rel, dst):
    return {"src": src, "rel": rel, "dst": dst}


class TestResolveHeads:
    def test_supersede_chain_head_is_newest(self):
        # v3 supersedes v2 supersedes v1 -> head is v3.
        edges = [_edge("v3", "supersedes", "v2"), _edge("v2", "supersedes", "v1")]
        assert vc.resolve_heads("v1", edges) == ["v3"]

    def test_seed_with_no_edges_has_no_head(self):
        assert vc.resolve_heads("lonely", []) == []

    def test_led_to_chain_head_is_terminal(self):
        edges = [_edge("a", "led_to", "b"), _edge("b", "implemented_by", "c")]
        assert vc.resolve_heads("a", edges) == ["c"]


class TestCollapseToHeads:
    def test_drops_superseded_items_preserves_order(self):
        edges = [_edge("v2", "supersedes", "v1")]
        items = [{"id": "v1", "summary": "old"}, {"id": "v2", "summary": "new"},
                 {"id": "x", "summary": "unrelated"}]
        kept, collapsed = vc.collapse_to_heads(items, edges)
        assert [i["id"] for i in kept] == ["v2", "x"]
        assert collapsed == ["v1"]

    def test_items_without_id_are_kept(self):
        edges = [_edge("v2", "supersedes", "v1")]
        items = [{"summary": "no id here"}]
        kept, collapsed = vc.collapse_to_heads(items, edges)
        assert kept == items
        assert collapsed == []

    def test_malformed_items_ignored_no_crash(self):
        kept, collapsed = vc.collapse_to_heads([None, "str", 5, {"id": "ok"}], [])
        assert [i["id"] for i in kept] == ["ok"]
        assert collapsed == []

    def test_no_edges_keeps_everything(self):
        items = [{"id": "a"}, {"id": "b"}]
        kept, collapsed = vc.collapse_to_heads(items, [])
        assert kept == items
        assert collapsed == []


class TestLineage:
    def test_lineage_orders_old_to_new(self):
        edges = [_edge("v2", "supersedes", "v1")]
        entries = {"v1": {"summary": "first"}, "v2": {"summary": "second"}}
        out = vc.lineage("v1", edges, entries)
        assert out["found"] is True
        assert [row["id"] for row in out["order"]] == ["v1", "v2"]
        assert out["heads"] == ["v2"]
        # v1 is marked superseded, v2 active.
        statuses = {row["id"]: row["status"] for row in out["order"]}
        assert statuses == {"v1": "superseded", "v2": "active"}


class TestVersionReport:
    def test_groups_disjoint_topics(self):
        edges = [
            _edge("v2", "supersedes", "v1"),  # topic A
            _edge("p", "led_to", "q"),         # topic B
        ]
        report = vc.build_version_report(edges)
        assert report["totals"]["topics"] == 2
        assert report["totals"]["nodes"] == 4
        seeds = [t["seed"] for t in report["topics"]]
        assert seeds == sorted(seeds)  # deterministic ordering by seed

    def test_superseded_and_head_counts(self):
        edges = [_edge("v3", "supersedes", "v2"), _edge("v2", "supersedes", "v1")]
        report = vc.build_version_report(edges)
        topic = report["topics"][0]
        assert topic["heads"] == ["v3"]
        assert topic["superseded_count"] == 2
        assert topic["active_count"] == 1
        assert topic["has_cycle"] is False

    def test_cycle_flagged_not_infinite(self):
        edges = [_edge("a", "led_to", "b"), _edge("b", "led_to", "a")]
        report = vc.build_version_report(edges)
        assert report["totals"]["cycles"] == 1
        assert report["topics"][0]["has_cycle"] is True

    def test_empty_edges_empty_report(self):
        report = vc.build_version_report([])
        assert report["totals"] == {
            "topics": 0, "nodes": 0, "heads": 0, "superseded": 0, "cycles": 0,
        }

    def test_report_contains_no_content(self):
        # Even with entries supplied, the report rows expose ids/counts only.
        edges = [_edge("v2", "supersedes", "v1")]
        entries = {"v1": {"summary": "SECRET-OLD"}, "v2": {"summary": "SECRET-NEW"}}
        report = vc.build_version_report(edges, entries)
        text = repr(report)
        assert "SECRET" not in text
