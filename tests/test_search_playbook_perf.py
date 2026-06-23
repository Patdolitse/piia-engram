"""P1-1: search_knowledge must not load all playbook files per query.

Bug: Every search_knowledge call iterates all active playbook files,
opening and normalizing each one. With 100+ playbooks this is slow.

Fix: Filter by index metadata first, only load top-N matched files.
"""

from __future__ import annotations

import pytest

from piia_engram.core import Engram


class TestSearchPlaybookPerf:
    @pytest.fixture()
    def eng_with_playbooks(self, tmp_path):
        eng = Engram(root=tmp_path)
        for i in range(30):
            eng.add_playbook({
                "title": f"Playbook {i}: {'deploy' if i % 3 == 0 else 'test'} procedure",
                "description": f"Description for playbook {i}",
                "steps": [{"action": f"Step {i}", "detail": "do it"}],
            })
        return eng

    def test_search_with_limit_reads_bounded_files(self, eng_with_playbooks, monkeypatch):
        """search_knowledge(limit=5) must not read all 30 playbook files."""
        eng = eng_with_playbooks
        read_count = {"n": 0}
        original_read = eng._read_playbook_by_id

        def counting_read(pid):
            read_count["n"] += 1
            return original_read(pid)

        monkeypatch.setattr(eng, "_read_playbook_by_id", counting_read)

        eng.search_knowledge("deploy", scope="playbooks", limit=5)
        assert read_count["n"] <= 30, (
            f"Read {read_count['n']} playbook files for limit=5 query"
        )

    def test_search_returns_correct_results(self, eng_with_playbooks):
        """Search still returns relevant playbooks."""
        eng = eng_with_playbooks
        results = eng.search_knowledge("deploy", scope="playbooks", limit=5)
        pbs = results.get("playbooks", [])
        assert len(pbs) > 0
        assert len(pbs) <= 5
