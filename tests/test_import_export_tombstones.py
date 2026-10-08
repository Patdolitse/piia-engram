"""JSON backups carry rejection tombstones (hashes only), so a restored store
still refuses what the Owner rejected before."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram import tombstones
from piia_engram.cli_commands import _render_import_result_text
from piia_engram.core import Engram

CLAIMS = {
    "lesson": {"summary": "Never ship a release on a Friday evening"},
    "decision": {"title": "Release cadence", "question": "Ship releases on Friday evenings?",
                 "choice": "No, ship early in the week"},
    "playbook": {"title": "Rotate the release signing key",
                 "steps": [{"action": "Revoke the previous key"}, "Publish the replacement key"]},
}
CLAIM_STRINGS = (
    "Never ship a release on a Friday evening",
    "Ship releases on Friday evenings?",
    "No, ship early in the week",
    "Rotate the release signing key",
    "Revoke the previous key",
    "Publish the replacement key",
)


@pytest.fixture(autouse=True)
def _no_approval_mode(monkeypatch):
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)


def _store(tmp_path: Path, name: str) -> Engram:
    return Engram(root=tmp_path / name)


def _write_stone(root: Path, kind: str, row: dict, *, version: int, item_id: str, **extra) -> None:
    if version == 2:
        h1, h2 = tombstones._hashes_v2(tombstones.claim_text(kind, row))
    else:
        h1, h2 = tombstones.claim_hashes(kind, row)
    path = Path(root) / "knowledge" / tombstones.FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"id": item_id, "kind": kind, "scope": "global", "h1": h1, "h2": h2, "hv": version,
              "rejected_at": "2026-01-01T00:00:00Z", "via": "test", **extra}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _propose(eng: Engram, kind: str) -> dict:
    row = dict(CLAIMS[kind])
    if kind == "lesson":
        return eng.add_lesson(dict(row, domain="t"))
    if kind == "decision":
        return eng.add_decision(row)
    return eng.add_playbook(row)


def _backup(tmp_path: Path, payload: dict, name: str = "backup.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("merge", [True, False], ids=["merge", "replace"])
@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("kind", ["lesson", "decision", "playbook"])
def test_restored_store_still_refuses_rejected_content(tmp_path, kind, version, merge):
    src = _store(tmp_path, "src")
    _write_stone(src.root, kind, CLAIMS[kind], version=version, item_id=f"rej-{kind}-{version}")
    out = src.export_all(str(tmp_path / "backup.json"))

    dst = _store(tmp_path, "dst")
    assert _propose(_store(tmp_path, "control"), kind).get("status") != "rejected_before"
    result = dst.import_all(out, merge=merge)
    assert result["status"] == "success"

    again = _propose(dst, kind)
    assert again["status"] == "rejected_before"
    assert again["rejection_id"] == f"rej-{kind}-{version}"
    restored = tombstones.load(dst.root)
    assert [r["hv"] for r in restored] == [version]


def test_export_carries_hashes_and_metadata_only(tmp_path):
    src = _store(tmp_path, "src")
    for i, kind in enumerate(CLAIMS):
        # An unexpected text field on disk must not travel with the backup.
        _write_stone(src.root, kind, CLAIMS[kind], version=3, item_id=f"r{i}",
                     summary=CLAIMS[kind].get("summary", "stray text"), prior_rejection_id="older")
    out = src.export_all(str(tmp_path / "backup.json"))

    text = Path(out).read_text(encoding="utf-8")
    for claim in CLAIM_STRINGS:
        assert claim not in text
    assert "stray text" not in text
    stones = json.loads(text)["knowledge"]["tombstones"]
    assert len(stones) == 3
    allowed = {"id", "kind", "scope", "h1", "h2", "hv", "rejected_at", "via", "prior_rejection_id"}
    for stone in stones:
        assert set(stone) <= allowed
        assert stone["hv"] == 3 and stone["prior_rejection_id"] == "older"


def test_import_drops_text_fields_and_malformed_records(tmp_path):
    good = {"id": "ok1", "kind": "lesson", "scope": "global", "h1": "a" * 64, "h2": "b" * 64,
            "hv": 3, "summary": "claim text that must not land"}
    payload = {"schema_version": "2.0", "knowledge": {"tombstones": [
        good, "not a record", {"kind": "lesson", "h1": "c" * 64}, {"id": "no-hash"},
    ]}}
    dst = _store(tmp_path, "dst")
    dst.import_all(_backup(tmp_path, payload), merge=True)

    restored = tombstones.load(dst.root)
    assert [r["id"] for r in restored] == ["ok1"]
    assert "summary" not in restored[0]
    raw = (dst.root / "knowledge" / tombstones.FILENAME).read_text(encoding="utf-8")
    assert "claim text" not in raw


def test_merge_dedupes_by_id_and_by_hash(tmp_path):
    src = _store(tmp_path, "src")
    _write_stone(src.root, "lesson", CLAIMS["lesson"], version=3, item_id="same-id")
    _write_stone(src.root, "playbook", CLAIMS["playbook"], version=2, item_id="pb-src")
    _write_stone(src.root, "decision", CLAIMS["decision"], version=3, item_id="dec-src")
    out = src.export_all(str(tmp_path / "backup.json"))

    dst = _store(tmp_path, "dst")
    _write_stone(dst.root, "lesson", {"summary": "something else entirely"}, version=3, item_id="same-id")
    _write_stone(dst.root, "playbook", CLAIMS["playbook"], version=2, item_id="pb-local")
    preview = dst.import_all(out, merge=True, dry_run=True)
    assert preview["summary"]["tombstones"] == {
        "incoming": 3, "would_add": 1, "would_skip": 2, "conflicts": 0, "kept": 2, "invalid": 0}

    first = dst.import_all(out, merge=True)
    assert "tombstones(+1)" in first["imported"]
    second = dst.import_all(out, merge=True)
    assert "tombstones(+0)" in second["imported"]
    assert sorted(r["id"] for r in tombstones.load(dst.root)) == ["dec-src", "pb-local", "same-id"]


def test_replace_mode_keeps_local_tombstones_and_adds_the_backups(tmp_path):
    src = _store(tmp_path, "src")
    _write_stone(src.root, "decision", CLAIMS["decision"], version=2, item_id="from-backup")
    _write_stone(src.root, "playbook", CLAIMS["playbook"], version=3, item_id="pb-backup")
    out = src.export_all(str(tmp_path / "backup.json"))

    dst = _store(tmp_path, "dst")
    _write_stone(dst.root, "lesson", CLAIMS["lesson"], version=3, item_id="local-only")
    _write_stone(dst.root, "playbook", CLAIMS["playbook"], version=3, item_id="pb-local")  # same hash
    preview = dst.import_all(out, merge=False, dry_run=True)
    assert preview["summary"]["tombstones"] == {
        "incoming": 2, "would_add": 1, "would_skip": 1, "conflicts": 0, "kept": 2, "invalid": 0}
    assert "tombstones: incoming=2 add=1 skip=1 conflicts=0 kept=2" in _render_import_result_text(preview)
    result = dst.import_all(out, merge=False)

    # A rejection is the Owner's decision: a replace import keeps the local ones.
    assert "tombstones(+1, kept 2)" in result["imported"]
    assert sorted(r["id"] for r in tombstones.load(dst.root)) == ["from-backup", "local-only", "pb-local"]
    assert _propose(dst, "decision")["status"] == "rejected_before"
    assert _propose(dst, "lesson")["status"] == "rejected_before"
    again = dst.import_all(out, merge=False)
    assert "tombstones(+0, kept 3)" in again["imported"]


@pytest.mark.parametrize("merge", [True, False], ids=["merge", "replace"])
def test_old_export_without_tombstones_still_imports(tmp_path, merge):
    payload = {"schema_version": "2.0", "exported_at": "2026-01-01T00:00:00Z",
               "knowledge": {"lessons": [{"summary": "An older backup row", "domain": "t"}]}}
    path = _backup(tmp_path, payload)
    dst = _store(tmp_path, "dst")
    _write_stone(dst.root, "lesson", CLAIMS["lesson"], version=3, item_id="kept")

    preview = dst.import_all(path, merge=merge, dry_run=True)
    assert "tombstones" not in preview["summary"]
    result = dst.import_all(path, merge=merge)

    assert result["status"] == "success"
    assert not any(str(item).startswith("tombstones") for item in result["imported"])
    # A backup without the section leaves the store's tombstones alone in both modes.
    assert [r["id"] for r in tombstones.load(dst.root)] == ["kept"]
    assert any(r.get("summary") == "An older backup row"
               for r in dst.get_lessons(limit=None, _update_access=False))


def test_dry_run_shows_the_tombstone_count_and_writes_nothing(tmp_path):
    src = _store(tmp_path, "src")
    for kind in CLAIMS:
        _write_stone(src.root, kind, CLAIMS[kind], version=3, item_id=f"r-{kind}")
    out = src.export_all(str(tmp_path / "backup.json"))

    dst = _store(tmp_path, "dst")
    preview = dst.import_all(out, merge=True, dry_run=True)
    assert preview["summary"]["tombstones"] == {
        "incoming": 3, "would_add": 3, "would_skip": 0, "conflicts": 0, "kept": 0, "invalid": 0}
    assert tombstones.load(dst.root) == []
    rendered = _render_import_result_text(preview)
    assert "tombstones: incoming=3 add=3 skip=0" in rendered


# -- with the import's pin protection and the MCP preview-only rule -------------


@pytest.fixture()
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    """A store the MCP server and the pin CLI both act on."""
    from piia_engram import mcp_server

    root = tmp_path / "live"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    engram = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix != ".log" and "sessions" not in p.parts
    }


def _stones_backup(tmp_path: Path, lessons: list[dict], name: str = "pinned.json") -> str:
    src = _store(tmp_path, "stones-src")
    _write_stone(src.root, "decision", CLAIMS["decision"], version=3, item_id="rej-backup")
    stones = json.loads(Path(src.export_all(str(tmp_path / "stones.json"))).read_text(
        encoding="utf-8"))["knowledge"]["tombstones"]
    return _backup(tmp_path, {"schema_version": "2.0", "identity": {}, "knowledge": {
        "lessons": lessons, "decisions": [], "playbooks": [], "tombstones": stones}}, name)


def test_mcp_preview_counts_tombstones_and_writes_nothing(live, tmp_path):
    import asyncio

    from piia_engram import mcp_server

    path = _stones_backup(tmp_path, [])
    before = _snapshot(live.root)
    for merge in (True, False):
        plan = json.loads(asyncio.run(mcp_server.import_engram(input_path=path, merge=merge, dry_run=True)))
        assert plan["dry_run"] is True
        assert plan["summary"]["tombstones"]["incoming"] == 1
        applied = json.loads(asyncio.run(mcp_server.import_engram(input_path=path, merge=merge)))
        assert applied["error"] == "local_only"
    assert _snapshot(live.root) == before
    assert tombstones.load(live.root) == []


@pytest.mark.parametrize("merge", [True, False], ids=["merge", "replace"])
def test_tombstones_restore_next_to_a_pinned_entry(live, tmp_path, merge):
    from piia_engram.cli_commands import run_pin

    pinned = live.add_lesson({"summary": "Pinned lesson next to restored rejections", "domain": "t",
                              "tier": "verified"})
    assert run_pin([pinned["id"]]) == 0
    path = _stones_backup(tmp_path, [{"id": pinned["id"], "summary": "Backup copy, other text",
                                      "tier": "verified", "status": "active"}])

    result = live.import_all(path, merge=merge)

    assert result["status"] == "success"
    assert any(str(item).startswith("tombstones(") for item in result["imported"])
    assert [r["id"] for r in tombstones.load(live.root)] == ["rej-backup"]
    row = live._find_item_by_id(pinned["id"])[1]
    assert row["pinned"] is True and row["summary"] == "Pinned lesson next to restored rejections"


def test_a_refused_pinned_import_restores_no_tombstones(live, tmp_path):
    from piia_engram.cli_commands import run_pin

    pinned = live.add_lesson({"summary": "Pinned lesson an import would supersede", "domain": "t",
                              "tier": "verified"})
    assert run_pin([pinned["id"]]) == 0
    proposal = live.add_lesson({"summary": "Pending revision of the pinned lesson", "domain": "t",
                                "supersedes": pinned["id"]})
    promoted = {**live._find_item_by_id(proposal["id"])[1], "tier": "verified",
                "memory_state": "verified", "approval_status": "approved"}
    path = _stones_backup(tmp_path, [live._find_item_by_id(pinned["id"])[1], promoted])
    before = _snapshot(live.root)

    result = live.import_all(path, merge=False)

    assert result["error"] == "pinned_target"
    assert _snapshot(live.root) == before
    assert tombstones.load(live.root) == []


def test_an_interrupted_import_restores_tombstones_on_resume(live, tmp_path, monkeypatch):
    from piia_engram import pinning

    path = _stones_backup(tmp_path, [{"id": "imp-1", "summary": "A lesson only in the backup",
                                      "tier": "verified", "status": "active"}])
    real = live._update_entries

    def _race(*args, **kwargs):
        raise pinning.PinnedTargetRefused("lesson", ["pinned-x"])

    monkeypatch.setattr(live, "_update_entries", _race)
    refused = live.import_all(path, merge=True)
    assert refused["error"] == "pinned_target"
    assert tombstones.load(live.root) == []
    assert (live.root / "knowledge" / live._IMPORT_PENDING_MARKER).is_file()

    monkeypatch.setattr(live, "_update_entries", real)
    resumed = live.import_all(path, merge=True)
    assert resumed["status"] == "success"
    assert "tombstones(+1)" in resumed["imported"]
    assert not (live.root / "knowledge" / live._IMPORT_PENDING_MARKER).exists()
    again = live.import_all(path, merge=True)
    assert "tombstones(+0)" in again["imported"]
    assert [r["id"] for r in tombstones.load(live.root)] == ["rej-backup"]


# -- forged backups: only well-formed records are restored ----------------------


def _good_stone(**over) -> dict:
    record = {"id": "rej-good", "kind": "lesson", "scope": "global", "h1": "a" * 64, "h2": "b" * 64,
              "hv": 3, "rejected_at": "2026-01-01T00:00:00Z", "via": "cli"}
    record.update(over)
    return {k: v for k, v in record.items() if v is not _DROP}


_DROP = object()

FORGED = {
    "hv_string": _good_stone(id="f1", hv="3"),
    "hv_missing": _good_stone(id="f2", hv=_DROP),
    "hv_unknown": _good_stone(id="f3", hv=999),
    "hv_bool": _good_stone(id="f4", hv=True),
    "h1_too_long": _good_stone(id="f5", h1="a" * 65),
    "h1_not_hex": _good_stone(id="f6", h1="g" * 64),
    "h1_upper": _good_stone(id="f7", h1="A" * 64),
    "h2_bad": _good_stone(id="f8", h2="b" * 10),
    "scope_int": _good_stone(id="f9", scope=7),
    "scope_too_long": _good_stone(id="f10", scope="p" * 200),
    "id_path": _good_stone(id="../../outside/escape"),
    "id_backslash": _good_stone(id=r"..\outside"),
    "id_int": _good_stone(id=12345),
    "id_too_long": _good_stone(id="x" * 129),
    "kind_unknown": _good_stone(id="f11", kind="tool"),
    "via_too_long": _good_stone(id="f12", via="v" * 65),
}


@pytest.mark.parametrize("name", sorted(FORGED))
def test_a_forged_tombstone_is_skipped_and_counted_invalid(tmp_path, name):
    payload = {"schema_version": "2.0", "knowledge": {"tombstones": [_good_stone(), FORGED[name]]}}
    path = _backup(tmp_path, payload)
    dst = _store(tmp_path, "dst")

    for merge in (True, False):
        preview = dst.import_all(path, merge=merge, dry_run=True)
        stones = preview["summary"]["tombstones"]
        assert stones["incoming"] == 1 and stones["invalid"] == 1 and stones["would_add"] == 1
        assert "invalid=1" in _render_import_result_text(preview)
    result = dst.import_all(path, merge=True)

    assert "tombstones(+1, invalid 1)" in result["imported"]
    assert [r["id"] for r in tombstones.load(dst.root)] == ["rej-good"]


def test_a_valid_backup_reports_zero_invalid(tmp_path):
    path = _backup(tmp_path, {"schema_version": "2.0", "knowledge": {"tombstones": [_good_stone()]}})
    dst = _store(tmp_path, "dst")
    preview = dst.import_all(path, merge=True, dry_run=True)
    assert preview["summary"]["tombstones"]["invalid"] == 0
    assert "tombstones(+1)" in dst.import_all(path, merge=True)["imported"]


# -- dedupe keys: scope and hash version count ----------------------------------


def test_a_project_tombstone_with_the_same_hash_as_a_global_one_is_added(tmp_path):
    h1, h2 = tombstones.claim_hashes("lesson", CLAIMS["lesson"])
    dst = _store(tmp_path, "dst")
    _write_stone(dst.root, "lesson", CLAIMS["lesson"], version=3, item_id="local-global")
    path = _backup(tmp_path, {"schema_version": "2.0", "knowledge": {"tombstones": [
        _good_stone(id="rej-proj-a", scope="projA", h1=h1, h2=h2)]}})

    preview = dst.import_all(path, merge=True, dry_run=True)
    assert preview["summary"]["tombstones"]["would_add"] == 1
    assert "tombstones(+1)" in dst.import_all(path, merge=True)["imported"]

    claim = CLAIMS["lesson"]["summary"]
    in_a = dst.add_lesson({"summary": claim, "domain": "t", "project_id": "projA"})
    assert in_a["status"] == "rejected_before" and in_a["rejection_id"] == "rej-proj-a"
    in_b = dst.add_lesson({"summary": claim, "domain": "t", "project_id": "projB"})
    assert in_b.get("status") != "rejected_before" and in_b.get("project_id") == "projB"


def test_dedupe_compares_hashes_within_one_hash_version(tmp_path):
    # A lesson's v2 and v3 h1 are the same string; records of different
    # versions are still distinct (each version is compared on its own).
    v2 = tombstones._hashes_v2(tombstones.claim_text("lesson", CLAIMS["lesson"]))
    v3 = tombstones.claim_hashes("lesson", CLAIMS["lesson"])
    assert v2[0] == v3[0]
    dst = _store(tmp_path, "dst")
    _write_stone(dst.root, "lesson", CLAIMS["lesson"], version=3, item_id="local-v3")
    path = _backup(tmp_path, {"schema_version": "2.0", "knowledge": {"tombstones": [
        _good_stone(id="backup-v2", hv=2, h1=v2[0], h2=v2[1]),
        _good_stone(id="backup-v3", hv=3, h1=v3[0], h2=v3[1]),
    ]}})

    preview = dst.import_all(path, merge=True, dry_run=True)
    assert preview["summary"]["tombstones"]["would_add"] == 1
    assert preview["summary"]["tombstones"]["would_skip"] == 1
    dst.import_all(path, merge=True)
    assert sorted(r["id"] for r in tombstones.load(dst.root)) == ["backup-v2", "local-v3"]


# -- via: only the route travels, never who or which client ---------------------


@pytest.mark.parametrize("raw, expected", [
    ("owner-veto:Alice Example", "owner-veto"),
    ("cli:Alice Example", "cli"),
    ("backfill:abc123:none:op=Alice Example", "backfill"),
    ("mcp:SecretClient", "mcp"),
    ("core:batch_review_staging", "core"),
    ("import", "import"),
    ("somewhere-else:Alice Example", "other"),
    ("", "other"),
])
def test_via_is_reduced_to_its_route(tmp_path, raw, expected):
    src = _store(tmp_path, "src")
    _write_stone(src.root, "lesson", CLAIMS["lesson"], version=3, item_id="r1")
    path = Path(src.root) / "knowledge" / tombstones.FILENAME
    record = json.loads(path.read_text(encoding="utf-8"))
    record["via"] = raw
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    out = src.export_all(str(tmp_path / "backup.json"))
    text = Path(out).read_text(encoding="utf-8")
    assert "Alice" not in text and "SecretClient" not in text
    assert json.loads(text)["knowledge"]["tombstones"][0]["via"] == expected


def test_import_reduces_via_too(tmp_path):
    path = _backup(tmp_path, {"schema_version": "2.0", "knowledge": {"tombstones": [
        _good_stone(via="mcp:SecretClient")]}})
    dst = _store(tmp_path, "dst")
    dst.import_all(path, merge=True)
    raw = (dst.root / "knowledge" / tombstones.FILENAME).read_text(encoding="utf-8")
    assert "SecretClient" not in raw
    assert tombstones.load(dst.root)[0]["via"] == "mcp"
