"""Issue #91: the resume brief must survive real stores.

A store-mode check is made once per top-level guarded read (nested reads reuse
it), and the digest walk skips sessions without a sidecar and is bounded, so a
resume read no longer costs (session files x knowledge store size). The brief's
recent-context read is bounded the same way and no longer resolves a path per
scanned session file, so it barely grows with the number of saved sessions.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from piia_engram import contexts as contexts_mod
from piia_engram import core
from piia_engram import storage
from piia_engram.core import Engram
from piia_engram.isolated_store import REPLAY_EXPORT_MARKER, carries_replay_marker
from piia_engram.storage import _project_id, _project_id_aliases
from test_replay_experience import MODE, _world

# Added to both sides before the 3x comparison: a timing ratio alone would
# flake on a loaded CI runner, where a few thousand file-system calls vary a lot.
SCALING_FLOOR_SECONDS = 5.0
STUB_SESSIONS = (12, 1500)
LESSONS_BYTES = 300_000
LEGACY_BODY = "# Session: claude_code @ 2026-01-01 10:00\n\n### 10:00\nshort session\n"
# Longer than the 128 characters a session id may have: no read accepts it.
LONG_STEM = "x" * 130


def _count_mode_checks(monkeypatch) -> list:
    calls = []
    original = Engram._read_store_mode

    def counting(self):
        calls.append(self)
        return original(self)

    monkeypatch.setattr(Engram, "_read_store_mode", counting)
    return calls


def _guarded(name, body):
    """A read method ``body(self, ...)`` behind the regular guard, as ``name``."""
    body.__name__ = name
    return core._non_quarantining_read_guard(body)


def _store(root: Path, project: Path) -> Engram:
    eng = Engram(root=root)
    eng.update_profile({"role": "developer", "language": "en"})
    eng.add_lesson({"summary": "Pin test fixtures to a temporary store"})
    eng.save_project_snapshot(str(project), {"title": "Sample", "tech_stack": ["python"]})
    for tool in ("claude_code", "codex", "cursor"):
        eng.save_agent_context(tool, "Goal: ship the sample.\nNext: assemble the resume pack.\n",
                               project_folder=str(project))
    (eng._contexts_dir / "claude_code" / "hook-legacy.md").write_text(LEGACY_BODY, encoding="utf-8")
    return eng


# ---------------------------------------------------------------------------
# A. one store-mode check per top-level read
# ---------------------------------------------------------------------------


def test_nested_reads_of_one_brief_check_the_store_mode_once(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    reader = Engram(root=_store(tmp_path / "store", project).root, read_only=True)
    calls = _count_mode_checks(monkeypatch)

    brief = reader.get_resume_brief(project_folder=str(project), token_budget=1500)

    assert "handoff" in brief["sections_included"] and "recent_context" in brief["sections_included"]
    assert calls == [reader]
    assert core._ACTIVE_READ_MODES.get() is None


@pytest.mark.parametrize("read_only", [False, True])
def test_each_top_level_read_checks_the_store_mode_once(tmp_path, monkeypatch, read_only):
    project = tmp_path / "project"
    project.mkdir()
    reader = Engram(root=_store(tmp_path / "store", project).root, read_only=read_only)
    calls = _count_mode_checks(monkeypatch)

    full = reader.get_resume_brief(str(project), include_resume_pack=True,
                                   include_agent_context_pack=True, task_summary="sample")
    pack = reader.build_project_resume_pack(str(project))
    reader.get_session_digest("codex", "hook-legacy")
    reader.get_session_digest("claude_code", "missing")

    assert full["resume_pack"] and full["agent_context_pack"]
    assert pack["schema"] == "project_resume_pack.v1"
    # One check per top-level call; no call reused an earlier call's result.
    assert calls == [reader] * 4
    assert core._ACTIVE_READ_MODES.get() is None


def test_a_nested_read_of_another_instance_checks_its_own_store(tmp_path, monkeypatch):
    first = Engram(root=tmp_path / "one")
    second = Engram(root=tmp_path / "two")
    calls = _count_mode_checks(monkeypatch)
    monkeypatch.setattr(Engram, "get_profile", _guarded(
        "get_profile", lambda self: second.get_session_digest("codex", "session")))

    assert first.get_profile() is None

    assert calls == [first, second]
    assert core._ACTIVE_READ_MODES.get() is None


def test_a_worker_thread_does_not_inherit_the_active_store_mode(tmp_path, monkeypatch):
    eng = _store(tmp_path / "store", tmp_path)
    calls = _count_mode_checks(monkeypatch)

    def get_profile(self):
        result = []

        def work():
            result.append(self.get_preferences())

        thread = threading.Thread(target=work)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive()
        return result[0]

    monkeypatch.setattr(Engram, "get_profile", _guarded("get_profile", get_profile))

    eng.get_profile()

    assert calls == [eng, eng]  # the outer read, then the thread's own check
    assert core._ACTIVE_READ_MODES.get() is None


def test_a_failing_read_still_ends_its_scope(tmp_path, monkeypatch):
    eng = _store(tmp_path / "store", tmp_path)
    calls = _count_mode_checks(monkeypatch)

    def failing(self, tool, session_id):
        raise RuntimeError("digest unavailable")

    monkeypatch.setattr(Engram, "get_session_digest", _guarded("get_session_digest", failing))

    with pytest.raises(RuntimeError, match="digest unavailable"):
        eng.get_recent_context(limit=1)  # the outer read nests the failing one
    assert core._ACTIVE_READ_MODES.get() is None

    with pytest.raises(RuntimeError, match="digest unavailable"):
        eng.get_session_digest("codex", "session")
    assert core._ACTIVE_READ_MODES.get() is None
    assert calls == [eng] * 2  # the outer read of the first call, then the top-level read


@pytest.mark.parametrize("read_only", [False, True])
def test_non_quarantining_context_follows_the_handle_at_every_level(tmp_path, monkeypatch, read_only):
    project = tmp_path / "project"
    project.mkdir()
    store = _store(tmp_path / "store", project).root
    seen = []

    def get_preferences(self):
        seen.append(storage._NON_QUARANTINING_READS.get())
        return {}

    monkeypatch.setattr(Engram, "get_preferences", _guarded("get_preferences", get_preferences))
    eng = Engram(root=store, read_only=read_only)

    eng.get_preferences()  # top level
    eng.get_resume_brief(str(project))  # nested inside another guarded read

    assert seen == [read_only, read_only]


def test_read_only_still_reads_damaged_input_without_quarantining(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    store = _store(tmp_path / "store", project).root
    (store / "knowledge" / "lessons.json").write_text("{damaged", encoding="utf-8")
    copies = []
    monkeypatch.setattr(storage.shutil, "copy2", lambda *a, **k: copies.append(a))
    reader = Engram(root=store, read_only=True)

    brief = reader.get_resume_brief(str(project))

    assert "identity" in brief["sections_included"]
    assert any(section.startswith("lessons (") for section in brief["sections_skipped"])
    assert copies == []
    assert core._ACTIVE_READ_MODES.get() is None


@pytest.mark.parametrize("mode", ["production", MODE])
def test_replay_marking_still_applies_at_every_level(tmp_path, monkeypatch, mode):
    world = _world(tmp_path / "source", monkeypatch, mode=mode)
    eng = world.pr._engram(read_only=False)
    project = tmp_path / "sample"
    project.mkdir()
    eng.save_project_snapshot(str(project), {
        "current_state": {"current_focus": "Validate the sample.",
                          "next_actions": ["Check the output shape."]}})
    saved = eng.save_agent_context("test", "Next: Validate the sample.\n",
                                   project_folder=str(project))
    calls = _count_mode_checks(monkeypatch)

    digest = eng.get_session_digest("test", saved["session_id"])

    assert digest["source"]["session_ref"] == saved["session_id"]
    assert ("store_mode" in digest) == (mode == MODE)
    assert carries_replay_marker(digest["next_actions"][0]) == (mode == MODE)
    assert calls == [eng]

    brief = eng.get_resume_brief(str(project), include_resume_pack=True)

    assert calls == [eng, eng]
    assert brief["markdown"].startswith(REPLAY_EXPORT_MARKER + "\n") == (mode == MODE)
    assert brief["byte_size"] == len(brief["markdown"].encode("utf-8"))
    assert brief["estimated_tokens"] == max(1, len(brief["markdown"]) // 4)
    assert brief["budget"]["over_budget"] == (
        brief["estimated_tokens"] > max(100, int(brief["budget"]["requested_tokens"])))
    # Reads nested inside the brief are marked too, and marking twice adds nothing.
    for text in (brief["handoff_meta"]["current_focus"], brief["handoff_meta"]["next_actions"][0],
                 brief["resume_pack"]["handoff"]["next_actions"][0]):
        assert carries_replay_marker(text) == (mode == MODE)
        assert text.count(REPLAY_EXPORT_MARKER) <= 1


# ---------------------------------------------------------------------------
# B. bounded digest walk
# ---------------------------------------------------------------------------


def _checkpoint(eng, tool, session_id, folder, mtime):
    saved = eng.save_agent_context(
        tool, f"Goal: finish {session_id}.\nNext: assemble {session_id}.\n",
        session_id=session_id, project_folder=folder)
    os.utime(saved["file"], (mtime, mtime))
    return Path(saved["file"])


def _ordered_store(root: Path, project: Path) -> Engram:
    """Sessions with and without sidecars, newest first: l2, l1 (legacy, other
    project), s4 (codex), s2, s3 (no project), s5 (its body lies in a directory
    named ``Cursor Dir``), s1. All but l1, l2 and s3 belong to ``project``."""
    eng = Engram(root=root)
    other = root.parent / "other"
    other.mkdir(exist_ok=True)
    for index, (tool, session_id, folder) in enumerate([
        ("claude_code", "s1", str(project)),
        ("cursor_dir", "s5", str(project)),
        ("claude_code", "s3", ""),
        ("claude_code", "s2", str(project)),
        ("codex", "s4", str(project)),
        ("claude_code", "l1", str(other)),
        ("codex", "l2", str(other)),
    ]):
        path = _checkpoint(eng, tool, session_id, folder, 1_700_000_000 + index)
        if session_id == "s5":
            # The tool directory's name needs sanitizing to find this sidecar.
            raw = eng._contexts_dir / "Cursor Dir"
            raw.mkdir()
            moved = raw / path.name
            path.rename(moved)
            os.utime(moved, (1_700_000_001, 1_700_000_001))
        elif session_id.startswith("l"):
            # A legacy session: its checkpoint never got a digest sidecar.
            path.with_name(f"{session_id}.digest.json").unlink()
    (eng._contexts_dir / "claude_code" / f"{LONG_STEM}.md").write_text(LEGACY_BODY, encoding="utf-8")
    return eng


def _digests_before(eng, *, project_folder: str = "", limit: int = 6) -> list[dict]:
    """The walk ``_recent_session_digests`` used before D2: every file, newest first."""
    project_ids = set(_project_id_aliases(project_folder)) if project_folder else set()
    files = []
    for tool_dir in eng._contexts_dir.iterdir():
        for path in tool_dir.glob("*.md"):
            files.append((path.stat().st_mtime, tool_dir.name, path.stem))
    found = []
    for _mtime, tool, session_id in sorted(files, reverse=True):
        digest = eng.get_session_digest(tool, session_id)
        if not digest:
            continue
        source_project = (digest.get("source") or {}).get("project_id", "")
        if source_project == _project_id(""):
            source_project = ""
        if project_ids and source_project not in project_ids:
            continue
        if not project_ids and source_project:
            continue
        found.append(digest)
        if len(found) >= limit:
            break
    return found


@pytest.mark.parametrize("limit", [1, 2, 6])
@pytest.mark.parametrize("folder_kind", ["project", "global"])
@pytest.mark.parametrize("read_only", [False, True])
def test_store_with_sidecars_returns_the_digests_it_always_did(tmp_path, limit, folder_kind, read_only):
    project = tmp_path / "project"
    project.mkdir()
    folder = "" if folder_kind == "global" else str(project)
    reader = Engram(root=_ordered_store(tmp_path / "store", project).root, read_only=read_only)

    digests = reader._recent_session_digests(project_folder=folder, limit=limit)

    before = _digests_before(reader, project_folder=folder, limit=limit)
    expected = ["s3"] if folder_kind == "global" else ["s4", "s2", "s5", "s1"]
    assert [d["source"]["session_ref"] for d in before] == expected[:limit]
    assert [d["source"]["session_ref"] for d in digests] == expected[:limit]
    for digest, stored in zip(digests, before):
        # The stored sidecar as before; only the derived selection metadata is added.
        derived = ("generated_at", "source_scope")
        assert {k: v for k, v in digest.items() if k not in derived} == {
            k: v for k, v in stored.items() if k not in derived}
        assert digest["generated_at"]
        assert digest["source_scope"]["mode"] in ("project_exact", "global_only")


def test_sessions_without_a_sidecar_cost_no_guarded_read(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    eng = _ordered_store(tmp_path / "store", project)
    calls = _count_mode_checks(monkeypatch)
    opened = []
    real_read_text, real_open = Path.read_text, Path.open

    def read_text(self, *args, **kwargs):
        opened.append(self)
        return real_read_text(self, *args, **kwargs)

    def record_open(self, *args, **kwargs):
        opened.append(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "open", record_open)

    digests = eng._recent_session_digests(project_folder=str(project), limit=6)

    assert [d["source"]["session_ref"] for d in digests] == ["s4", "s2", "s5", "s1"]
    # Five sessions have a sidecar (s3 belongs to no project): five guarded
    # reads, and none for the legacy sessions or the too-long stem.
    assert len(calls) == 5
    # Under contexts/ only sidecars were opened; no session body was read.
    # (read_text opens the file too, hence the set.)
    opened_sessions = {path for path in opened if eng._contexts_dir in path.parents}
    assert sorted(path.name for path in opened_sessions) == [
        "s1.digest.json", "s2.digest.json", "s3.digest.json", "s4.digest.json", "s5.digest.json"]


def test_a_session_without_a_sidecar_is_never_read(tmp_path, monkeypatch):
    eng = Engram(root=tmp_path / "store")
    claude = eng._contexts_dir / "claude_code"
    claude.mkdir(parents=True)
    for name in ("l1.md", " spaced .md", "..md", f"{LONG_STEM}.md"):
        (claude / name).write_text(LEGACY_BODY, encoding="utf-8")
    (claude / "folder.md").mkdir()  # a directory that matches the session glob
    raw = eng._contexts_dir / "Cursor Dir"
    raw.mkdir()
    (raw / "x.md").write_text(LEGACY_BODY, encoding="utf-8")
    checked = []
    real_is_file = Path.is_file

    def is_file(self):
        checked.append(self)
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    read_digest = Engram.get_session_digest.__wrapped__

    def no_read(self, tool, session_id):
        pytest.fail("guarded read " + session_id)

    monkeypatch.setattr(Engram, "get_session_digest", _guarded("get_session_digest", no_read))

    assert eng._recent_session_digests() == []

    # Each session whose id can name a sidecar had one existence check, where
    # get_session_digest reads: the id is stripped, the tool directory sanitized.
    sidecars = [path for path in checked if path.name.endswith(".digest.json")]
    assert sorted((path.parent.name, path.name) for path in sidecars) == [
        ("claude_code", "folder.digest.json"), ("claude_code", "l1.digest.json"),
        ("claude_code", "spaced.digest.json"), ("cursor_dir", "x.digest.json")]
    # Skipping them changes nothing: the read itself finds no sidecar either.
    unreadable = (("claude_code", "."), ("claude_code", LONG_STEM), ("claude_code", "folder"),
                  ("claude_code", " spaced "), ("Cursor Dir", "x"))
    for tool, session_id in unreadable:
        assert read_digest(eng, tool, session_id) is None


def test_digest_walk_inspects_a_bounded_number_of_session_files(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    eng = Engram(root=tmp_path / "store")
    limit = contexts_mod.RESUME_DIGEST_SCAN_LIMIT
    assert limit == 200  # documented in contexts.RESUME_DIGEST_SCAN_LIMIT
    _checkpoint(eng, "claude_code", "s9", str(project), 1_700_000_000)
    _checkpoint(eng, "codex", "s10", str(project), 1_700_003_000)
    codex = eng._contexts_dir / "codex"

    def legacy(index, mtime):
        path = codex / f"hook-legacy-{index:03d}.md"
        path.write_text(LEGACY_BODY, encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    newest = [legacy(index, 1_700_001_000 + index) for index in range(limit - 2)]

    def found():
        return [d["source"]["session_ref"]
                for d in eng._recent_session_digests(project_folder=str(project), limit=6)]

    # s10 and 198 legacy files: s9 is the 200th, the last file the walk inspects.
    assert found() == ["s10", "s9"]
    # One more legacy file pushes it out; the digest itself is still readable.
    extra = legacy(limit, 1_700_000_500)
    assert found() == ["s10"]
    assert eng.get_session_digest("claude_code", "s9")["source"]["session_ref"] == "s9"
    extra.unlink()
    assert found() == ["s10", "s9"]
    for path in newest:
        path.unlink()
    assert found() == ["s10", "s9"]


def _big_store(root: Path, sessions: int) -> Path:
    """A ~300 KB lessons.json and ``sessions`` legacy checkpoints without sidecars."""
    eng = Engram(root=root)
    eng.update_profile({"role": "developer", "language": "en"})
    for index in range(20):
        eng.add_lesson({"summary": f"Synthetic lesson number {index}",
                        "domain": "testing", "source_tool": "cli"})
    path = root / "knowledge" / "lessons.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    base = list(rows)
    while path.stat().st_size < LESSONS_BYTES:
        for copy in range(20):
            rows.extend(dict(row, id=f"{row['id']}-{copy}-{len(rows)}") for row in base)
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    tool_dir = root / "contexts" / "claude_code"
    tool_dir.mkdir(parents=True, exist_ok=True)
    for index in range(sessions):
        (tool_dir / f"hook-legacy-{index:05d}.md").write_text(LEGACY_BODY, encoding="utf-8")
    assert len({row["id"] for row in rows}) == len(rows)
    return root


def _brief_seconds(eng: Engram) -> float:
    started = time.perf_counter()
    brief = eng.get_resume_brief(project_folder="", token_budget=1500)
    assert "lessons" in brief["sections_included"]
    return time.perf_counter() - started


def test_brief_time_barely_grows_with_the_number_of_session_files(tmp_path, monkeypatch):
    calls = _count_mode_checks(monkeypatch)
    readers = {}
    for sessions in STUB_SESSIONS:
        root = _big_store(tmp_path / f"store-{sessions}", sessions)
        readers[sessions] = Engram(root=root, read_only=True)
        assert (root / "knowledge" / "lessons.json").stat().st_size >= LESSONS_BYTES
    _brief_seconds(readers[STUB_SESSIONS[0]])
    _brief_seconds(readers[STUB_SESSIONS[1]])  # warm the file cache
    calls.clear()
    seconds = {}
    checks = {}
    for sessions, reader in readers.items():
        before = len(calls)
        seconds[sessions] = min(_brief_seconds(reader) for _ in range(3))
        checks[sessions] = len(calls) - before
    print("\nget_resume_brief, best of 3: "
          + "; ".join(f"{sessions} session files: {seconds[sessions]:.3f} s, "
                      f"{checks[sessions]} store-mode checks" for sessions in readers))

    small, big = (seconds[sessions] for sessions in STUB_SESSIONS)
    assert checks[STUB_SESSIONS[0]] == checks[STUB_SESSIONS[1]] == 3
    assert big <= 3 * (small + SCALING_FLOOR_SECONDS)


# Fix-pass targets for the 1,500-file store: the brief's session reads are bounded.
WARM_LIMIT_SECONDS = 1.0     # best of three warm calls
COLD_LIMIT_SECONDS = 2.0     # the first call on a freshly written store
SCALING_SLOPE, SCALING_SLACK = 2.0, 0.5   # 1,500-file time <= slope * 12-file time + slack


def test_brief_time_meets_the_fix_pass_targets(tmp_path, monkeypatch):
    calls = _count_mode_checks(monkeypatch)
    measured = {}
    for sessions in STUB_SESSIONS:
        reader = Engram(root=_big_store(tmp_path / f"target-{sessions}", sessions), read_only=True)
        before = len(calls)
        cold = _brief_seconds(reader)
        warm = min(_brief_seconds(reader) for _ in range(3))
        assert len(calls) - before == 4  # one store-mode check per call
        measured[sessions] = (cold, warm)
    print("\nget_resume_brief: " + "; ".join(
        f"{sessions} session files: cold first call {cold:.3f} s, warm best of 3 {warm:.3f} s"
        for sessions, (cold, warm) in measured.items()))

    (small_cold, small_warm), (big_cold, big_warm) = (measured[s] for s in STUB_SESSIONS)
    print(f"1,500-file time over 2 x (12-file time) + 0.5 s: "
          f"warm {big_warm / (SCALING_SLOPE * small_warm + SCALING_SLACK):.2f}, "
          f"cold {big_cold / (SCALING_SLOPE * small_cold + SCALING_SLACK):.2f}")
    assert big_warm < WARM_LIMIT_SECONDS
    assert big_cold < COLD_LIMIT_SECONDS
    assert big_warm <= SCALING_SLOPE * small_warm + SCALING_SLACK


# The read without a cap (the MCP tool, recall) still looks at every session
# file, but no longer resolves two paths for each.
UNCAPPED_READ_LIMIT_SECONDS = 2.0


def test_uncapped_recent_context_read_on_the_large_store(tmp_path):
    reader = Engram(root=_big_store(tmp_path / "store", STUB_SESSIONS[1]), read_only=True)

    def seconds():
        started = time.perf_counter()
        sessions = reader.get_recent_context(limit=2)
        assert len(sessions) == 2
        return time.perf_counter() - started

    seconds()  # warm
    best = min(seconds() for _ in range(3))
    print(f"\nget_recent_context(limit=2) without a cap, 1500 session files, "
          f"warm best of 3: {best:.3f} s")

    assert best < UNCAPPED_READ_LIMIT_SECONDS


# ---------------------------------------------------------------------------
# C. the brief's recent-context read is bounded (fix pass)
# ---------------------------------------------------------------------------

T0 = 1_700_000_000.0


def _legacy(eng, tool: str, session_id: str, folder: str, mtime: float) -> Path:
    """A legacy checkpoint without a digest sidecar, at a fixed time."""
    tool_dir = eng._context_tool_dir(tool)
    tool_dir.mkdir(parents=True, exist_ok=True)
    header = f"# Session: {tool} @ 2026-01-01 10:00\n"
    if folder:
        header += f"## Project: {folder}\n"
    path = tool_dir / f"{session_id}.md"
    path.write_text(f"{header}\n### 10:00\nshort session {session_id}\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _mixed_store(root: Path, project: Path) -> Engram:
    """Eight session files in two tools. Newest first: cc-tie and cx-tie (one
    second apart from the rest, and in the same second as each other),
    cx-legacy-global, cc-global, cx-new, cc-legacy, cx-other, cc-old. All but
    cx-other and the two global sessions belong to ``project``."""
    eng = Engram(root=root)
    other = root.parent / "other"
    other.mkdir(exist_ok=True)
    _checkpoint(eng, "claude_code", "cc-old", str(project), T0)
    _checkpoint(eng, "codex", "cx-other", str(other), T0 + 1)
    _legacy(eng, "claude_code", "cc-legacy", str(project), T0 + 2)
    _checkpoint(eng, "codex", "cx-new", str(project), T0 + 3)
    _checkpoint(eng, "claude_code", "cc-global", "", T0 + 4)
    _legacy(eng, "codex", "cx-legacy-global", "", T0 + 5)
    _checkpoint(eng, "claude_code", "cc-tie", str(project), T0 + 10.2)
    _checkpoint(eng, "codex", "cx-tie", str(project), T0 + 10.7)
    return eng


def _ids(sessions: list[dict]) -> list[str]:
    return [session["session_id"] for session in sessions]


QUERIES = [
    {"project_folder": "{project}"},
    {"project_folder": ""},
    {"tool": "claude_code", "project_folder": "{project}"},
    {"tool": "codex", "project_folder": ""},
]


@pytest.mark.parametrize("limit", [1, 3, 10])
@pytest.mark.parametrize("scan_limit", [8, 200])  # exactly the file count, and more
def test_capped_read_equals_the_uncapped_read_when_matches_are_within_the_cap(
        tmp_path, limit, scan_limit):
    project = tmp_path / "project"
    project.mkdir()
    eng = _mixed_store(tmp_path / "store", project)

    for query in QUERIES:
        query = {key: value.format(project=project) for key, value in query.items()}
        capped = eng.get_recent_context(limit=limit, scan_limit=scan_limit, **query)
        assert capped == eng.get_recent_context(limit=limit, **query), query

    project_sessions = eng.get_recent_context(limit=10, project_folder=str(project),
                                              scan_limit=scan_limit)
    assert sorted(_ids(project_sessions)[:2]) == ["cc-tie", "cx-tie"]
    assert _ids(project_sessions)[2:] == ["cx-new", "cc-legacy", "cc-old"]
    assert _ids(eng.get_recent_context(limit=10, scan_limit=scan_limit)) == [
        "cx-legacy-global", "cc-global"]
    # Same-second sessions keep the order the uncapped read gives them.
    assert _ids(project_sessions[:2]) == _ids(
        eng.get_recent_context(limit=2, project_folder=str(project)))
    assert {session["modified_at"] for session in project_sessions[:2]} == {
        datetime.fromtimestamp(T0 + 10).replace(microsecond=0).isoformat()}
    by_id = {session["session_id"]: session for session in project_sessions}
    assert "cc-tie" in by_id["cc-tie"]["content"] and "cc-legacy" in by_id["cc-legacy"]["content"]


def test_capped_read_cuts_the_newest_files_across_all_tools_and_only_those(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    eng = _mixed_store(tmp_path / "store", project)

    # The four newest files hold both global sessions: cutting the other four
    # changes nothing for a global query.
    assert eng.get_recent_context(limit=5, scan_limit=4) == eng.get_recent_context(limit=5)
    # The oldest file is a project session: cutting it drops it from that query.
    assert _ids(eng.get_recent_context(limit=10, project_folder=str(project), scan_limit=8))[-1] == "cc-old"
    assert "cc-old" not in _ids(
        eng.get_recent_context(limit=10, project_folder=str(project), scan_limit=7))
    # A single tool is capped on its own files.
    claude = dict(tool="claude_code", project_folder=str(project), limit=10)
    assert _ids(eng.get_recent_context(**claude)) == ["cc-tie", "cc-legacy", "cc-old"]
    assert _ids(eng.get_recent_context(scan_limit=2, **claude)) == ["cc-tie"]
    assert _ids(eng.get_recent_context(scan_limit=3, **claude)) == ["cc-tie", "cc-legacy"]


def _store_with_old_session(tmp_path: Path) -> tuple[Engram, Path]:
    """One project session, then 200 newer legacy sessions of another tool and project."""
    project = tmp_path / "project"
    project.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    eng = Engram(root=tmp_path / "store")
    _checkpoint(eng, "claude_code", "m-old", str(project), T0)
    for index in range(contexts_mod.RESUME_DIGEST_SCAN_LIMIT):
        _legacy(eng, "codex", f"other-{index:03d}", str(other), T0 + 1000 + index)
    return eng, project


def test_a_matching_session_behind_the_scan_limit_is_only_found_without_the_cap(tmp_path):
    eng, project = _store_with_old_session(tmp_path)
    limit = contexts_mod.RESUME_DIGEST_SCAN_LIMIT
    uncapped = eng.get_recent_context(project_folder=str(project), limit=2)

    assert _ids(uncapped) == ["m-old"]  # the MCP tool and other existing callers
    assert eng.get_recent_context(project_folder=str(project), limit=2, scan_limit=limit) == []
    assert eng.get_recent_context(
        project_folder=str(project), limit=2, scan_limit=limit + 1) == uncapped
    assert eng.get_recent_context(project_folder=str(project), limit=2, scan_limit=0) == []

    # The resume brief uses the capped read: the session behind it is not
    # resumed, until it is among the newest files again.
    assert "recent_context" not in eng.get_resume_brief(str(project))["sections_included"]
    for path in sorted((eng._contexts_dir / "codex").glob("other-*.md"))[1:]:
        path.unlink()
    assert "recent_context" in eng.get_resume_brief(str(project))["sections_included"]


def test_mcp_recent_context_tool_keeps_the_uncapped_read(tmp_path, monkeypatch):
    import asyncio

    from piia_engram import mcp_server

    eng, project = _store_with_old_session(tmp_path)
    uncapped = eng.get_recent_context(project_folder=str(project), limit=2)
    monkeypatch.setattr(mcp_server, "_engram", eng)
    monkeypatch.setattr(mcp_server, "_track", lambda *a, **k: None)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())

    payload = json.loads(asyncio.run(mcp_server.get_recent_context(
        project_folder=str(project), limit=2)))

    assert _ids(payload["sessions"]) == ["m-old"]
    assert payload["sessions"][0]["content"] == uncapped[0]["content"]


def _links(link: Path, file_target: Path, dir_target: Path) -> list[str]:
    """The session-file links this OS allows: a file symlink and, on Windows, a junction."""
    made = []
    try:
        os.symlink(file_target, link)
        made.append("symlink")
    except (OSError, NotImplementedError, AttributeError):
        pass
    try:
        import _winapi

        _winapi.CreateJunction(str(dir_target), str(link.with_name("junction.md")))
        made.append("junction")
    except (ImportError, AttributeError, OSError):
        pass
    return made


def test_a_linked_session_file_is_skipped(tmp_path):
    eng = Engram(root=tmp_path / "store")
    _checkpoint(eng, "claude_code", "s-old", "", T0)
    _checkpoint(eng, "claude_code", "s-new", "", T0 + 100)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text(
        "# Session: claude_code @ 2026-01-01 10:00\n\n### 10:00\nOUTSIDE-SECRET\n", encoding="utf-8")
    made = _links(eng._context_tool_dir("claude_code") / "linked.md", outside / "secret.md", outside)
    if not made:
        pytest.skip("this system allows neither a symlink nor a junction")
    print(f"\nsession-file links made and skipped: {', '.join(made)}")

    for kwargs in ({}, {"scan_limit": 200}):
        sessions = eng.get_recent_context(limit=5, **kwargs)  # a global query: the links would match

        assert _ids(sessions) == ["s-new", "s-old"], (made, kwargs)
        assert "OUTSIDE-SECRET" not in json.dumps(sessions)


def test_only_returned_session_files_get_the_confinement_check(tmp_path, monkeypatch):
    eng = Engram(root=tmp_path / "store")
    for index in range(5):
        _checkpoint(eng, "claude_code", f"s{index}", "", T0 + index)
    real = Engram._context_session_path
    checked = []

    def spy(self, tool, session_id):
        checked.append(session_id)
        return real(self, tool, session_id)

    monkeypatch.setattr(Engram, "_context_session_path", spy)
    for kwargs in ({}, {"scan_limit": 200}):
        checked.clear()
        assert _ids(eng.get_recent_context(limit=2, **kwargs)) == ["s4", "s3"]
        assert checked == ["s4", "s3"]  # one check per returned file, none per scanned file
        checked.clear()
        eng.get_recent_context(limit=2, project_folder=str(tmp_path), **kwargs)
        assert checked == []  # nothing matched, nothing checked

    def unconfined(self, tool, session_id):
        if session_id == "s4":
            raise ValueError("path is outside its store directory")
        return real(self, tool, session_id)

    monkeypatch.setattr(Engram, "_context_session_path", unconfined)
    for kwargs in ({}, {"scan_limit": 200}):
        # A file that turns out unconfined is left out; the next match moves up.
        assert _ids(eng.get_recent_context(limit=2, **kwargs)) == ["s3", "s2"]
