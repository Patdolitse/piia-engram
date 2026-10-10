"""UTF-8 and filesystem boundaries for a caller-owned offline replay."""

from __future__ import annotations

import ast
import io
import json
import locale
import os
import sys
import tempfile
from pathlib import Path

import pytest

from piia_engram import usage_ping, update_check
from piia_engram.core import Engram
from piia_engram.isolated_store import Config, GuardRefused, IsolatedStore, init_root
from piia_engram.isolated_store_launch import build_child_env
from test_isolated_store import ADMIT, _card, _make_world
from test_replay_experience import ADMITTED, CLOCK, CUT, EARLY, MODE

_PING_STATE_DIR = usage_ping.state_dir  # capture before the suite's per-test patch
_REPO = Path(__file__).resolve().parents[1]
# All Python sources added/changed in 86547e88b..287d71838, plus this follow-up.
_MODULES = (
    "agents_md_export cli_commands compat context_preview core import_export "
    "isolated_store isolated_store_launch memory_import recall_service reconcile "
    "reconcile_apply reports_analytics reports_identity reports_portrait reports_review "
    "reports_weekly storage usage_ping update_check audit"
).split()
_TESTS = (
    "test_replay_attachment_exports test_replay_boundary_fixes "
    "test_replay_experience test_replay_io_containment"
).split()


def _implicit_text_io(source):
    problems = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        encoding = kwargs.get("encoding")
        if name in {"read_text", "write_text"}:
            offset = 0 if name == "read_text" else 1
            if encoding is None and len(node.args) > offset:
                encoding = node.args[offset]
        elif name in {"open", "fdopen"}:
            # Store attachment methods are not filesystem streams. os.open is binary.
            owner = getattr(getattr(func, "value", None), "id", "")
            if owner in {"Engram", "IsolatedStore", "os"} and name == "open":
                continue
            builtin = isinstance(func, ast.Name) or owner in {"io", "builtins", "os"}
            mode_offset = 1 if builtin else 0
            mode = kwargs.get("mode", node.args[mode_offset] if len(node.args) > mode_offset else ast.Constant("r"))
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value:
                continue
            if encoding is None and len(node.args) > mode_offset + 2:
                encoding = node.args[mode_offset + 2]
        else:
            continue
        if not isinstance(encoding, ast.Constant) or encoding.value not in {"utf-8", "utf-8-sig"}:
            problems.append((node.lineno, name))
    return problems


def test_branch_text_io_requires_explicit_utf8():
    files = [(_REPO / "src" / "piia_engram" / f"{name}.py") for name in _MODULES]
    files += [(_REPO / "tests" / f"{name}.py") for name in _TESTS]
    files += sorted((_REPO / "tests" / "fixtures" / "replay_production_base").glob("*.txt"))
    problems = {str(path.relative_to(_REPO)): _implicit_text_io(path.read_text(encoding="utf-8")) for path in files}
    assert {path: rows for path, rows in problems.items() if rows} == {}


@pytest.mark.parametrize("source", [
    "p.read_text()", "p.write_text('sample')", "open(p)", "open(p, 'w')",
    "p.open()", "io.open(p, 'r')", "os.fdopen(fd, 'w')", "p.read_text(encoding=None)",
])
def test_utf8_guard_detects_implicit_text_streams(source):
    assert _implicit_text_io(source)


def test_replay_roundtrip_under_forced_gbk_locale(tmp_path, monkeypatch):
    from test_replay_experience import _world
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "gbk")
    original = io.text_encoding
    monkeypatch.setattr(io, "text_encoding", lambda encoding, stacklevel=2: "gbk" if encoding is None else original(encoding, stacklevel))
    witness = tmp_path / "utf8.txt"
    witness.write_text("中文回放：✓", encoding="utf-8")
    # Prove the simulated default really is incompatible, without an implicit read.
    with pytest.raises(UnicodeDecodeError):
        witness.read_bytes().decode(io.text_encoding(None))
    w = _world(tmp_path / "replay", monkeypatch)
    card = _card("sample", "Q1", EARLY, summary="中文回放观察：缓存命中✓")
    card["detail"] = "历史材料按时间顺序重新输入。"
    receipt = w.pr.admit(card, "R1", ADMIT, admitted_before=ADMITTED, now=CLOCK)
    assert receipt["result"] == "admitted"
    result = w.pr.recall("dp-replay", "R2", evidence_before=CUT, admitted_before=CUT, now=CLOCK)
    assert result["items"][0]["summary"] == card["summary"]
    eng = w.pr._engram(read_only=False)
    exported = eng.export_all(str(w.cfg.cache_dir / "roundtrip.json"))
    payload = json.loads(Path(exported).read_text(encoding="utf-8"))
    assert payload["knowledge"]["lessons"][0]["summary"] == card["summary"]
    assert eng.import_all(exported).get("error") is None
    assert "store_mode: replay_experience" in eng.generate_context()


def test_replay_default_export_requires_dedicated_cache(tmp_path, monkeypatch):
    from test_replay_experience import _world
    w = _world(tmp_path, monkeypatch)
    eng = w.pr._engram(read_only=False)
    monkeypatch.delenv("ENGRAM_CACHE_DIR")
    with pytest.raises(GuardRefused, match="guard_replay_cache_required"):
        eng.export_all()


def test_isolated_open_refuses_cache_environment_mismatch(tmp_path, monkeypatch):
    from test_replay_experience import _world
    w = _world(tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_CACHE_DIR", str(tmp_path / "wrong-cache"))
    with pytest.raises(GuardRefused, match="guard_cache_dir_mismatch"):
        IsolatedStore.open(w.cfg)


def test_arbitrary_replay_root_contains_all_store_io(tmp_path, monkeypatch):
    # Import runtime/native dependencies before auditing store I/O; code loading
    # is outside this data boundary. Do not whitelist source paths in the hook.
    warm = _make_world(tmp_path / "imports", monkeypatch)
    warm_eng = warm.pr._engram(read_only=False)
    warm_eng.generate_context()
    warm_eng.export_review_page()
    warm_eng.export_all(str(warm.cfg.cache_dir / "warm.json"))
    warm_eng._backup_store("4.23.0")
    # The root has no parent relationship to the home, cache or ledger area.
    w = _make_world(tmp_path, monkeypatch, init=False)
    w.data.update(root=str(tmp_path / "separate-volume-layout" / "replay-root"), mode=MODE)
    w.cfg_path.write_text(json.dumps(w.data), encoding="utf-8")
    cfg = Config.load(w.cfg_path)
    cfg.root.parent.mkdir(parents=True, exist_ok=True)
    child = build_child_env(dict(os.environ), cfg)
    for name in list(os.environ):
        if name.startswith("ENGRAM_") and name not in child:
            monkeypatch.delenv(name)
    for name, value in child.items():
        monkeypatch.setenv(name, value)
    for name in ("APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR"):
        Path(child[name]).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tempfile, "tempdir", None)
    monkeypatch.setattr(usage_ping, "state_dir", _PING_STATE_DIR)
    # Run genuine startup paths: the suite's ENGRAM_TEST shortcut is absent.
    assert "ENGRAM_TEST" not in os.environ
    for point in w.dps.glob("*.json"):
        data = json.loads(point.read_text(encoding="utf-8"))
        data["as_of_utc"] = CUT
        point.write_text(json.dumps(data), encoding="utf-8")
    dedicated = (cfg.root, cfg.receipts_dir, cfg.fake_home, cfg.cache_dir, cfg.decision_points_dir)
    exact_files = (cfg.path, cfg.deny_list_file)
    normalise = lambda path: os.path.normcase(os.path.abspath(os.fsdecode(path)))
    allowed_dirs = tuple(normalise(path) for path in dedicated)
    allowed_files = {normalise(path) for path in exact_files}
    parents = {normalise(parent) for path in dedicated + exact_files for parent in path.parents}
    forbidden_homes = tuple(normalise(cfg.fake_home / name) for name in (".engram", ".piia"))
    observations, escaped = [], []
    active = True

    def observe(event, path, metadata=False):
        if not active or isinstance(path, int) or path is None:
            return
        value = normalise(path)
        observations.append((event, value))
        forbidden = any(value == home or value.startswith(home + os.sep) for home in forbidden_homes)
        contained = value in allowed_files or any(value == root or value.startswith(root + os.sep) for root in allowed_dirs)
        if forbidden or not (contained or (metadata and value in parents)):
            escaped.append((event, value))
            raise AssertionError(f"replay filesystem escape: {event}: {value}")

    def audit(event, args):
        if event in {"open", "os.listdir", "os.scandir", "os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.utime", "os.truncate",
                     "sqlite3.connect", "tempfile.mkstemp", "tempfile.mkdtemp"}:
            observe(event, args[0])
        elif event in {"os.rename", "os.link", "os.symlink", "shutil.copyfile"}:
            observe(event, args[0])
            observe(event, args[1])

    sys.addaudithook(audit)
    for name in ("stat", "lstat", "access"):
        original = getattr(os, name)
        def checked(path, *args, _original=original, _name=name, **kwargs):
            observe("os." + _name, path, metadata=True)
            return _original(path, *args, **kwargs)
        monkeypatch.setattr(os, name, checked)
    try:
        pr = init_root(cfg)
        pr = IsolatedStore.open(cfg)
        receipt = pr.admit(_card("sample", "Q1", EARLY), "R1", ADMIT, admitted_before=ADMITTED, now=CLOCK)
        assert receipt["result"] == "admitted"
        assert len(pr.recall("dp-replay", "R2", evidence_before=CUT, admitted_before=CUT, now=CLOCK)["items"]) == 1
        assert pr.reconcile()["problems"] == []
        eng = pr._engram(read_only=False)
        out = eng.export_all()
        assert eng.import_all(out).get("error") is None
        eng.generate_context()
        eng.export_review_page()
        eng._backup_store("4.23.0")
        with tempfile.TemporaryFile() as handle:
            handle.write(b"sample")
        # Exercise the local state/cache implementations without any network.
        assert usage_ping.decision() == (False, "DO_NOT_TRACK")
        assert not usage_ping.status()["will_send"]
        assert usage_ping.maybe_send("cli") is None
        usage_ping.set_enabled(False)
        update_check._write_cache("4.23.0")
        assert update_check._read_cache()["latest"] == "4.23.0"
        with pytest.raises(GuardRefused, match="guard_replay_experience_root"):
            Engram(root=cfg.root)
        # Sensitivity: a fallback read/lock/create is refused even if swallowed.
        with pytest.raises(AssertionError, match="filesystem escape"):
            (cfg.fake_home / ".engram" / "missing.json").read_text(encoding="utf-8")
        escaped.pop()
        assert escaped == []
        assert any(value.endswith(".engram-write.lock") for _, value in observations)
        for ending in ("receipts.jsonl", "refusals.jsonl", "audit.log", ".update_check.json", "usage_ping.json"):
            assert any(value.endswith(ending) for _, value in observations), ending
        assert any(os.sep + "backups" + os.sep in value for _, value in observations)
        assert any(value.startswith(normalise(cfg.fake_home / "Temp") + os.sep) for _, value in observations)
    finally:
        active = False
