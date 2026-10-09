"""Portable reviewed lineage excludes immutable revision snapshots."""
import json
import pytest
from piia_engram import Engram
from knowledge_seed import raw_write_json


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("merge", [False, True])
def test_three_generation_portable_backup_keeps_retired_predecessor(tmp_path, archived, merge):
    from test_r1_review_fixes import _historical_three_generations
    eng, a, b, c = _historical_three_generations(tmp_path)
    path = eng._knowledge_dir / "decisions.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    retired = next(row for row in rows if row["id"] == b["id"])
    snapshot = {**retired, "id": "immutable-revision", "snapshot_of": c["id"], "snapshot_version": 1}
    if archived:
        from piia_engram.storage import _append_jsonl_lines
        _append_jsonl_lines(eng._overflow_archive_path("decision"), [json.dumps(row) for row in (retired, snapshot)])
        rows = [row for row in rows if row["id"] != b["id"]]
    else:
        rows.append(snapshot)
    raw_write_json(path, rows)
    assert {row["id"] for row in eng.get_decisions(limit=None, _update_access=False)} == {c["id"]}
    backup = eng.export_all_with_summary(str(tmp_path / "portable.json"))
    exported = json.loads((tmp_path / "portable.json").read_text(encoding="utf-8"))
    records = exported["knowledge"]["decisions"] + exported["overflow_archive"]["decisions"]
    assert all(row["id"] != snapshot["id"] for row in records)
    restored = Engram(root=tmp_path / "restored")
    restored.import_all(backup["path"], merge=merge)
    assert {row["id"] for row in restored.get_decisions(limit=None, _update_access=False)} == {c["id"]}
    assert restored._recall_supersede_index().successor(a["id"]) == b["id"]
    assert any(row["id"] == b["id"] and row["status"] == "superseded" for row in records)


