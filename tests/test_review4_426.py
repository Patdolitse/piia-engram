"""Final repair regressions using synthetic stores and local path aliases."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from piia_engram.core import Engram
from piia_engram.generated_artifacts import CANONICAL_INDEX, MANIFEST
from piia_engram.isolated_store import GuardRefused
from piia_engram.replay_row_repair import PENDING, repair
from test_review3_426 import CANARY, _mark, _ordinary


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("existing_salt", [False, True])
def test_encrypted_repair_uses_real_store_policy(tmp_path, monkeypatch, legacy, existing_salt):
    pytest.importorskip("cryptography")
    root = tmp_path / "store"
    eng, path, rows = _ordinary(root)
    eng.refresh_quick_context()
    eng.rebuild_index()
    for name in CANONICAL_INDEX[1:]:
        (root / name).write_bytes(CANARY.encode("utf-8"))
    monkeypatch.setenv("ENGRAM_SECRET", "synthetic-review-four-secret")
    # Legacy plaintext can predate encryption and have no salt yet. Encrypted
    # rows always have a valid original salt, as in the reviewer's witness.
    if existing_salt or not legacy:
        salt = bytes(range(16))
        (root / ".corpus_salt").write_bytes(salt)
    if not legacy:
        from piia_engram.crypto import EncryptionEngine

        crypto = EncryptionEngine(os.environ["ENGRAM_SECRET"])
        key = crypto.derive_corpus_key(salt)
        for row in rows:
            row["summary"] = crypto.corpus_encrypt(row["summary"], key)
        assert all(row["summary"].startswith("enc:v2c:") for row in rows)
    _mark(path, rows)
    with pytest.raises(GuardRefused):
        Engram(root=root, read_only=True)
    salt_before = (root / ".corpus_salt").read_bytes() if (root / ".corpus_salt").exists() else None
    result = repair(root, apply=True)
    assert result["quarantined"] == 1
    assert not (root / PENDING).exists()
    assert result["skipped_artifacts"] == [{"path": "search_index.db", "reason": "corpus_encrypted"}]
    assert all(not (root / name).exists() for name in CANONICAL_INDEX)
    assert ((root / ".corpus_salt").read_bytes() if (root / ".corpus_salt").exists() else None) == salt_before
    retained = json.loads(path.read_text(encoding="utf-8"))
    assert retained == rows[1:]
    quick = (root / "quick_context.md").read_text(encoding="utf-8")
    card = (root / "exports/identity_card.md").read_text(encoding="utf-8")
    assert CANARY not in quick and CANARY not in card
    assert "clean observation" in quick and "enc:v2c:" not in quick
    reopened = Engram(root=root)
    assert len(reopened.get_lessons(_update_access=False)) == 9
    assert reopened.rebuild_index()["skipped"] == "corpus_encrypted"
    assert all(not (root / name).exists() for name in CANONICAL_INDEX)
    assert repair(root, apply=True)["quarantined"] == 0


def test_repair_builder_uses_canonical_real_store_under_pending(tmp_path, monkeypatch):
    from piia_engram.search_index import SearchIndex

    eng, path, rows = _ordinary(tmp_path / "store")
    _mark(path, rows)
    rebuild = SearchIndex.rebuild
    seen = []

    def checked(index, entries, **kwargs):
        assert index.db_path == eng.root / "search_index.db"
        assert (eng.root / PENDING).exists()
        with pytest.raises(GuardRefused):
            Engram(root=eng.root, read_only=True)
        seen.append(True)
        return rebuild(index, entries, **kwargs)

    monkeypatch.setattr(SearchIndex, "rebuild", checked)
    repair(eng.root, apply=True)
    assert seen
    with closing(sqlite3.connect(eng.root / "search_index.db")) as db:
        assert db.execute("SELECT count(*) FROM fts").fetchone()[0] == 9


@pytest.mark.skipif(os.name != "nt", reason="Windows namespace aliases")
@pytest.mark.parametrize("prefix", ["\\\\?\\", "\\\\.\\"])
@pytest.mark.parametrize("relative,reason", [
    ("knowledge/decisions.json", "reserved_store_source"),
    ("quick_context.md", "canonical_artifact_managed_separately"),
])
def test_windows_namespace_alias_is_invalid(tmp_path, prefix, relative, reason):
    eng, path, rows = _ordinary(tmp_path / "store")
    target = eng.root / relative
    if relative == "quick_context.md":
        eng.refresh_quick_context()
    else:
        target.write_text("[]", encoding="utf-8")
    alias = prefix + str(target).upper()
    assert os.path.samefile(alias, target)
    (eng.root / MANIFEST).write_text(json.dumps({"version": 2, "targets": [{
        "path": alias, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "generated_at": "2026-01-01T00:00:00+00:00",
    }]}), encoding="utf-8")
    _mark(path, rows)
    before = target.read_bytes(), target.stat().st_mtime_ns
    for result in (repair(eng.root), repair(eng.root, apply=True)):
        entry = next(item for item in result["generated_files"] if item["target"] == alias)
        assert entry["status"] == "invalid" and entry["reason"] == reason
    if relative.startswith("knowledge/"):
        assert (target.read_bytes(), target.stat().st_mtime_ns) == before


def test_hardlink_to_store_source_is_invalid(tmp_path):
    eng, path, rows = _ordinary(tmp_path / "store")
    source = eng.root / "knowledge/decisions.json"
    source.write_text("[]", encoding="utf-8")
    alias = tmp_path / "outside-source-alias.json"
    os.link(source, alias)
    (eng.root / MANIFEST).write_text(json.dumps({"version": 2, "targets": [{
        "path": str(alias), "sha256": hashlib.sha256(alias.read_bytes()).hexdigest(),
    }]}), encoding="utf-8")
    _mark(path, rows)
    before = source.read_bytes(), source.stat().st_mtime_ns
    for result in (repair(eng.root), repair(eng.root, apply=True)):
        entry = next(item for item in result["generated_files"] if item["target"] == str(alias))
        assert entry["status"] == "invalid" and entry["reason"] == "reserved_store_source"
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
