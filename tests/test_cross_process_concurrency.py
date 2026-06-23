"""Cross-process concurrency tests (X2-1).

Existing concurrent tests use threading.Thread — they prove in-process
lock correctness but miss cross-process failures (the real multi-tool
scenario). These tests spawn independent Python subprocesses that
race on the same Engram directory, validating that portalocker file
locking actually serializes writes across OS processes.

Uses a file-based barrier: each subprocess creates a .ready file and
polls until all peers are ready before starting the hot loop.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

PYTHON = sys.executable
TIMEOUT = 30
SRC_PATH = str(Path(__file__).resolve().parent.parent / "src")


def _run_workers(tmp_path: Path, script: str, n_workers: int, per_worker: int):
    """Spawn n_workers processes that all run the same script concurrently.

    The script receives via env:
      WORKER_ID    — "0", "1", ...
      WORKER_COUNT — total workers
      OPS_PER_WORKER — iterations per worker
      ENGRAM_DIR   — shared tmp directory
      BARRIER_DIR  — directory for .ready files
      PYTHONPATH   — points to engram/src
    """
    barrier_dir = tmp_path / "_barrier"
    barrier_dir.mkdir()

    script_file = tmp_path / "_worker_script.py"
    script_file.write_text(
        textwrap.dedent(script).strip() + "\n", encoding="utf-8"
    )

    import os
    base_env = {
        k: v for k, v in os.environ.items()
        if k.upper() not in ("ENGRAM_DIR", "PYTHONPATH")
    }
    base_env["PYTHONPATH"] = SRC_PATH
    base_env["PYTHONIOENCODING"] = "utf-8"
    base_env["ENGRAM_DIR"] = str(tmp_path)
    base_env["ENGRAM_TEST"] = "1"
    base_env["WORKER_COUNT"] = str(n_workers)
    base_env["OPS_PER_WORKER"] = str(per_worker)
    base_env["BARRIER_DIR"] = str(barrier_dir)

    procs = []
    for i in range(n_workers):
        env = {**base_env, "WORKER_ID": str(i)}
        p = subprocess.Popen(
            [PYTHON, str(script_file)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(tmp_path),
        )
        procs.append(p)

    results = []
    for p in procs:
        stdout, stderr = p.communicate(timeout=TIMEOUT)
        results.append({
            "returncode": p.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        })

    return results


BARRIER_SNIPPET = '''
import os, time
from pathlib import Path

_wid = os.environ["WORKER_ID"]
_wcount = int(os.environ["WORKER_COUNT"])
_barrier = Path(os.environ["BARRIER_DIR"])

# Signal ready
(_barrier / f"{_wid}.ready").write_text("1")

# Wait for all peers (poll with short sleep)
_deadline = time.monotonic() + 10
while True:
    if len(list(_barrier.glob("*.ready"))) >= _wcount:
        break
    if time.monotonic() > _deadline:
        raise TimeoutError("barrier timeout — not all workers started")
    time.sleep(0.01)
'''


class TestCrossProcessAddLesson:
    """Multiple processes adding lessons must not lose writes."""

    def test_no_lost_lessons(self, tmp_path):
        n_workers = 3
        per_worker = 8
        expected_total = n_workers * per_worker

        script = f'''
{BARRIER_SNIPPET}
import os, json
from pathlib import Path
from piia_engram.core import Engram

eng = Engram(root=Path(os.environ["ENGRAM_DIR"]))
wid = os.environ["WORKER_ID"]
ops = int(os.environ["OPS_PER_WORKER"])

for i in range(ops):
    eng.add_lesson({{"summary": f"w{{wid}}-{{i}}", "detail": "cross-proc test"}})

print(json.dumps({{"ok": True, "worker": wid, "ops": ops}}))
'''

        results = _run_workers(tmp_path, script, n_workers, per_worker)

        for r in results:
            assert r["returncode"] == 0, f"Worker failed: {r['stderr']}"

        from piia_engram.core import Engram
        eng = Engram(root=tmp_path)
        lessons = eng.get_lessons()
        summaries = {le["summary"] for le in lessons}

        for w in range(n_workers):
            for i in range(per_worker):
                assert f"w{w}-{i}" in summaries, f"Lost lesson w{w}-{i}"

        assert len(lessons) == expected_total


class TestCrossProcessSaveAgentContext:
    """Multiple processes appending agent context must not lose entries.

    All workers use the SAME tool name so they contend on the same
    per-tool-dir lock, exercising real cross-process serialization.
    Each worker creates its own session_id to avoid append collisions.
    Context files are .md (not .json).
    """

    def test_no_lost_contexts(self, tmp_path):
        n_workers = 3
        per_worker = 6

        script = f'''
{BARRIER_SNIPPET}
import os, json
from pathlib import Path
from piia_engram.core import Engram

eng = Engram(root=Path(os.environ["ENGRAM_DIR"]))
wid = os.environ["WORKER_ID"]
ops = int(os.environ["OPS_PER_WORKER"])

for i in range(ops):
    eng.save_agent_context(
        tool="shared_tool",
        content=f"ctx-{{wid}}-{{i}}",
        session_id=f"sess-{{wid}}-{{i}}",
        project_folder=os.environ["ENGRAM_DIR"],
    )

print(json.dumps({{"ok": True, "worker": wid}}))
'''

        results = _run_workers(tmp_path, script, n_workers, per_worker)

        for r in results:
            assert r["returncode"] == 0, f"Worker failed: {r['stderr']}"

        contexts_dir = tmp_path / "contexts"
        all_text = ""
        if contexts_dir.exists():
            for f in contexts_dir.rglob("*.md"):
                all_text += f.read_text(encoding="utf-8", errors="replace")

        found_markers = set()
        for w in range(n_workers):
            for i in range(per_worker):
                marker = f"ctx-{w}-{i}"
                if marker in all_text:
                    found_markers.add(marker)

        expected = {f"ctx-{w}-{i}" for w in range(n_workers) for i in range(per_worker)}
        lost = expected - found_markers
        assert not lost, f"Lost {len(lost)} context entries: {sorted(lost)[:5]}..."


class TestCrossProcessGrantStore:
    """Multiple processes granting different agents must not lose grants."""

    def test_no_lost_grants(self, tmp_path):
        n_workers = 3
        per_worker = 5

        script = f'''
{BARRIER_SNIPPET}
import os, json
from piia_engram.governance_store import GrantStore

store = GrantStore(os.environ["ENGRAM_DIR"])
wid = os.environ["WORKER_ID"]
ops = int(os.environ["OPS_PER_WORKER"])

for i in range(ops):
    store.set_grant(f"agent-{{wid}}-{{i}}", "trusted-local")

print(json.dumps({{"ok": True, "worker": wid}}))
'''

        results = _run_workers(tmp_path, script, n_workers, per_worker)

        for r in results:
            assert r["returncode"] == 0, f"Worker failed: {r['stderr']}"

        from piia_engram.governance_store import GrantStore
        store = GrantStore(tmp_path)
        grants = store.list_grants()["grants"]

        expected_total = n_workers * per_worker
        for w in range(n_workers):
            for i in range(per_worker):
                agent_id = f"agent-{w}-{i}"
                assert agent_id in grants, f"Lost grant for {agent_id}"

        assert len(grants) == expected_total


class TestCrossProcessAddPlaybook:
    """Multiple processes adding playbooks must not lose body files or index entries.

    add_playbook writes body + index in two steps (no overarching lock).
    This test verifies whether index entries survive concurrent inserts.
    """

    def test_no_lost_playbooks(self, tmp_path):
        n_workers = 3
        per_worker = 4

        script = f'''
{BARRIER_SNIPPET}
import os, json
from pathlib import Path
from piia_engram.core import Engram

eng = Engram(root=Path(os.environ["ENGRAM_DIR"]))
wid = os.environ["WORKER_ID"]
ops = int(os.environ["OPS_PER_WORKER"])

# Each title must share zero tokens with every other to avoid duplicate detection
TITLES = [
    ["Alpha canary sunrise", "Beta dolphin twilight", "Gamma elephant aurora", "Delta falcon midnight"],
    ["Epsilon gorilla harvest", "Zeta hamster equinox", "Eta ibex solstice", "Theta jaguar zenith"],
    ["Iota koala pinnacle", "Kappa lemur vanguard", "Lambda meerkat frontier", "Mu narwhal summit"],
]

for i in range(ops):
    title = TITLES[int(wid)][i]
    result = eng.add_playbook({{
        "title": title,
        "trigger": f"trigger for {{title}}",
        "steps": [{{"action": "execute", "detail": f"run {{title}}"}}],
    }})
    if result.get("status") == "duplicate":
        raise RuntimeError(f"Unexpected duplicate: {{title}} vs {{result.get('existing_title')}}")

print(json.dumps({{"ok": True, "worker": wid}}))
'''

        results = _run_workers(tmp_path, script, n_workers, per_worker)

        for r in results:
            assert r["returncode"] == 0, f"Worker failed: {r['stderr']}"

        from piia_engram.core import Engram
        eng = Engram(root=tmp_path)
        playbooks = eng.get_playbooks()
        titles = {pb["title"] for pb in playbooks}

        all_titles = [
            ["Alpha canary sunrise", "Beta dolphin twilight",
             "Gamma elephant aurora", "Delta falcon midnight"],
            ["Epsilon gorilla harvest", "Zeta hamster equinox",
             "Eta ibex solstice", "Theta jaguar zenith"],
            ["Iota koala pinnacle", "Kappa lemur vanguard",
             "Lambda meerkat frontier", "Mu narwhal summit"],
        ]

        expected_total = n_workers * per_worker
        for w in range(n_workers):
            for i in range(per_worker):
                title = all_titles[w][i]
                assert title in titles, f"Lost playbook '{title}'"

        assert len(playbooks) == expected_total


class TestCrossProcessRelationStore:
    """Multiple processes adding relations must not lose edges."""

    def test_no_lost_relations(self, tmp_path):
        n_workers = 3
        per_worker = 5

        script = f'''
{BARRIER_SNIPPET}
import os, json
from piia_engram.governance_store import RelationStore

store = RelationStore(os.environ["ENGRAM_DIR"])
wid = os.environ["WORKER_ID"]
ops = int(os.environ["OPS_PER_WORKER"])

for i in range(ops):
    store.add_relation(f"src-{{wid}}-{{i}}", "supersedes", f"dst-{{wid}}-{{i}}")

print(json.dumps({{"ok": True, "worker": wid}}))
'''

        results = _run_workers(tmp_path, script, n_workers, per_worker)

        for r in results:
            assert r["returncode"] == 0, f"Worker failed: {r['stderr']}"

        from piia_engram.governance_store import RelationStore
        store = RelationStore(tmp_path)
        edges = store.all_edges()

        expected_total = n_workers * per_worker
        edge_keys = {(e["src"], e["dst"]) for e in edges}

        for w in range(n_workers):
            for i in range(per_worker):
                key = (f"src-{w}-{i}", f"dst-{w}-{i}")
                assert key in edge_keys, f"Lost relation {key}"

        assert len(edges) == expected_total
