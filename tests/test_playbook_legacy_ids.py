"""Safe legacy underscore ids remain visible; actual internal files do not."""

import json
from pathlib import Path

import pytest

from piia_engram import review_cli
from piia_engram.core import Engram
from piia_engram.playbooks import valid_playbook_id


@pytest.mark.parametrize("item_id", ["_custom", "_internal", "_index_custom"])
def test_safe_underscore_ids_can_still_be_inserted(tmp_path, item_id):
    assert valid_playbook_id(item_id)
    eng = Engram(root=tmp_path / "store")
    result = eng.add_playbook({"id": item_id, "title": "Custom operations checklist", "steps": [{"action": "check"}]})
    assert result["id"] == item_id
    assert eng._read_playbook_by_id(item_id)


@pytest.mark.parametrize("pending", [False, True])
def test_existing_custom_playbook_is_readable_listed_reviewed_exported_and_backed_up(tmp_path, monkeypatch, pending):
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    row = eng._ensure_playbook_fields({"id": "_custom", "title": "Legacy custom checklist",
                                      "steps": [{"action": "check"}]})
    if pending:
        row.update(tier="staging", approval_status="pending")
    # Simulate an older store, without invoking the new-id insertion validator.
    eng._playbooks_dir.mkdir(parents=True, exist_ok=True)
    path = eng._playbooks_dir / "_custom.json"
    eng._write_playbook_file(path, row)
    eng._write_playbook_index([eng._playbook_index_entry(row)])
    before = path.read_bytes()
    assert eng._read_playbook_by_id("_custom")["title"] == row["title"]
    assert "_custom" in {r["id"] for r in eng.list_playbooks_for_management(include_content=True)["items"]}
    if not pending:
        assert "_custom" in {r["id"] for r in eng.get_playbooks(_update_access=False)}
    assert "_custom" in {r["id"] for r in eng._export_playbooks()}
    portable = json.loads(Path(eng.export_all(str(tmp_path / "backup.json"))).read_text(encoding="utf-8"))
    assert "_custom" in {r["id"] for r in portable["knowledge"]["playbooks"]}
    backup = eng._backup_store("legacy-test")
    assert (backup / "playbooks" / "_custom.json").read_bytes() == before
    if pending:
        marks = tmp_path / "marks.json"
        marks.write_text(json.dumps([{"id": "_custom", "mark": "approve", "expected_version": 1}]), encoding="utf-8")
        assert review_cli.run_apply([str(marks), "--operator", "owner", "--yes"]) == 0
        assert eng._read_playbook_by_id("_custom")["approval_status"] == "approved"


@pytest.mark.parametrize("item_id", ["_index", "_INDEX"])
def test_internal_index_is_never_a_playbook(tmp_path, item_id):
    eng = Engram(root=tmp_path / "store")
    row = eng.add_playbook({"title": "Ordinary checklist", "steps": [{"action": "check"}]})
    before = (eng._playbooks_dir / "_index.json").read_bytes()
    assert not valid_playbook_id(item_id)
    assert eng._read_playbook_by_id(item_id) is None
    with pytest.raises(ValueError):
        eng._write_playbook_and_index({"id": item_id, "title": "Index collision"}, create=True)
    assert (eng._playbooks_dir / "_index.json").read_bytes() == before
    assert eng._read_playbook_by_id(row["id"])
