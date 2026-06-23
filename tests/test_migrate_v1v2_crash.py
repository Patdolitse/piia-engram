"""R1-2: _migrate_v1_to_v2 must not crash on corrupted schema_version.json.

Bug: _migrate_v1_to_v2 calls _read_json without allow_corrupt=True and
calls .get() without isinstance(dict) check. A corrupted file crashes
init, bricking the store. Other migration paths have both guards.
"""

from __future__ import annotations

import json

import pytest

from piia_engram.core import Engram


class TestMigrateV1V2CrashGuard:
    def test_corrupted_json_does_not_crash(self, tmp_path):
        """Corrupted schema_version.json must not prevent init."""
        ver_path = tmp_path / "schema_version.json"
        ver_path.write_text("{{{{not json", encoding="utf-8")
        eng = Engram(root=tmp_path)
        assert eng is not None

    def test_null_json_does_not_crash(self, tmp_path):
        """null JSON value must not crash migration."""
        ver_path = tmp_path / "schema_version.json"
        ver_path.write_text("null", encoding="utf-8")
        eng = Engram(root=tmp_path)
        assert eng is not None

    def test_list_json_does_not_crash(self, tmp_path):
        """List JSON value must not crash migration."""
        ver_path = tmp_path / "schema_version.json"
        ver_path.write_text('["not", "a", "dict"]', encoding="utf-8")
        eng = Engram(root=tmp_path)
        assert eng is not None

    def test_string_json_does_not_crash(self, tmp_path):
        """String JSON value must not crash migration."""
        ver_path = tmp_path / "schema_version.json"
        ver_path.write_text('"just a string"', encoding="utf-8")
        eng = Engram(root=tmp_path)
        assert eng is not None

    def test_empty_file_does_not_crash(self, tmp_path):
        """Empty file must not crash migration."""
        ver_path = tmp_path / "schema_version.json"
        ver_path.write_text("", encoding="utf-8")
        eng = Engram(root=tmp_path)
        assert eng is not None

    def test_v2_already_migrated_skips(self, tmp_path):
        """Already-migrated stores (v2.0+) skip cleanly."""
        ver_path = tmp_path / "schema_version.json"
        ver_path.write_text(json.dumps({"schema_version": "2.0"}), encoding="utf-8")
        eng = Engram(root=tmp_path)
        assert eng is not None
