"""Robustness regressions for offline recovery and local diagnostics."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from piia_engram import Engram
from knowledge_seed import raw_write_json


def test_retry_older_checkpoint_keeps_newer_digest(tmp_path):
    from piia_engram.hooks import spool
    from test_hook_spool_regressions import queued
    eng = Engram(root=tmp_path / "store")
    old = tmp_path / "old.jsonl"
    new = tmp_path / "new.jsonl"
    new.write_text(json.dumps({"content": "Next: deploy NEW release"}) + "\n", encoding="utf-8")
    queued(eng.root, "cursor_save", {"session_id": "recovery", "transcript_path": str(old), "roots": [str(tmp_path)]})
    queued(eng.root, "cursor_save", {"session_id": "recovery", "transcript_path": str(new), "roots": [str(tmp_path)]})
    assert spool.drain(eng.root, engram=eng)["processed"] == 1
    path = eng._session_digest_path("cursor", "recovery")
    before = path.read_bytes()
    assert "deploy NEW release" in before.decode()
    old.write_text(json.dumps({"content": "Next: investigate OLD issue"}) + "\n", encoding="utf-8")
    assert spool.drain(eng.root, engram=eng)["processed"] == 1
    assert path.read_bytes() == before


def _decision_pair(tmp_path):
    eng = Engram(root=tmp_path / "store")
    old = eng.add_decision({"question": "Choose the storage engine", "choice": "SQLite", "tier": "verified"})
    return eng, old


def test_explicit_supersede_retires_predecessor(tmp_path):
    eng, old = _decision_pair(tmp_path)
    new = eng.add_decision({"question": "Choose the storage engine", "choice": "PostgreSQL", "tier": "verified", "supersedes": old["id"]})
    rows = json.loads((eng._knowledge_dir / "decisions.json").read_text(encoding="utf-8"))
    predecessor = next(r for r in rows if r["id"] == old["id"])
    assert predecessor["status"] == "superseded"
    assert predecessor["superseded_by"] == new["id"]
    assert [r["id"] for r in eng.get_decisions(limit=None, _update_access=False)] == [new["id"]]


def test_historical_supersede_is_hidden_and_doctor_is_read_only(tmp_path):
    from piia_engram.doctor import decision_consistency_check
    from piia_engram.governance_store import RelationStore
    eng, old = _decision_pair(tmp_path)
    new = eng.add_decision({"question": "Choose cache policy", "choice": "bounded", "tier": "verified"})
    RelationStore(eng.root).add_relation(new["id"], "supersedes", old["id"])
    before = {str(p): p.read_bytes() for p in eng.root.rglob("*") if p.is_file()}
    read = Engram(root=eng.root, read_only=True)
    assert old["id"] not in {r["id"] for r in read.get_decisions(limit=None, _update_access=False)}
    check = decision_consistency_check(read)
    assert check["status"] == "WARN"
    assert check["predecessors"] == [old["id"]]
    assert "engram conflicts resolve" in check["hint"]
    assert {str(p): p.read_bytes() for p in eng.root.rglob("*") if p.is_file()} == before


@pytest.fixture
def registry():
    spec = importlib.util.spec_from_file_location("r1_registry", Path(__file__).parents[1] / "scripts/verify_mcp_registry_version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def registry_entry(version="4.23.0", latest=True):
    return {"server": {"name": "example/engram", "version": "4.23.0", "packages": [{"version": version}]}, "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": latest}}}


@pytest.mark.parametrize("entry", [registry_entry("4.22.0"), registry_entry(latest=False), registry_entry(latest="true")])
def test_registry_rejects_package_or_latest_mismatch(registry, entry):
    with pytest.raises(ValueError):
        registry.find_registry_version(name="example/engram", version="4.23.0", fetch=lambda _: {"servers": [entry], "metadata": {}})


@pytest.mark.parametrize("payload", [[], {}, {"servers": {}}, {"servers": [None]}, {"servers": [], "metadata": []}])
def test_registry_rejects_unexpected_shape(registry, payload):
    with pytest.raises(ValueError, match="shape"):
        registry.find_registry_version(name="example/engram", version="4.23.0", fetch=lambda _: payload)


def test_registry_timeout_is_clear(registry, monkeypatch, capsys):
    def timeout(*args, **kwargs):
        raise TimeoutError("timed out")
    monkeypatch.setattr(registry.urllib.request, "urlopen", timeout)
    assert registry.main(["--name", "example/engram", "--version", "4.23.0"]) == 1
    assert "timeout" in capsys.readouterr().out.lower()
    assert registry.DEFAULT_API.endswith("/v0.1/servers")


@pytest.mark.parametrize("order", [("doctor", "setup_wizard"), ("setup_wizard", "doctor")])
def test_import_order_in_fresh_process(order):
    code = ";".join(f"import piia_engram.{name}" for name in order)
    result = subprocess.run([sys.executable, "-c", code], env=os.environ.copy(), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("module, args", [("setup_wizard", ["--help"]), ("doctor", ["--json"])])
def test_module_entrypoints(module, args, monkeypatch):
    # Entry points emit UTF-8 even when the parent console defaults to GBK.
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "gbk")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-m", "piia_engram." + module, *args],
                            env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


@pytest.mark.parametrize("layout", ["nested", "mixed", "unknown", "corrupt"])
def test_snapshot_write_requires_migration_and_reads_do_not_write(tmp_path, layout):
    from piia_engram.storage import _project_id
    eng = Engram(root=tmp_path / "store")
    project = str(tmp_path / "project")
    path = eng._projects_dir / (_project_id(project) + ".json")
    values = {"nested": {"snapshot": {"title": "old"}}, "mixed": {"snapshot": {"title": "old"}, "title": "new"},
              "unknown": {"schema": "project_snapshot.v99", "title": "unknown"}}
    path.write_text("{broken" if layout == "corrupt" else json.dumps(values[layout]), encoding="utf-8")
    before = {str(p): p.read_bytes() for p in eng.root.rglob("*") if p.is_file()}
    result = Engram(root=eng.root, read_only=True).get_project_snapshot(project)
    assert result["migration"]["error"] == "migration_required"
    assert {str(p): p.read_bytes() for p in eng.root.rglob("*") if p.is_file()} == before
    with pytest.raises(ValueError, match="migration_required"):
        eng.save_project_snapshot(project, {"notes": "write"})
    assert path.read_bytes() == before[str(path)]


def test_snapshot_top_level_stays_writable(tmp_path):
    eng = Engram(root=tmp_path / "store")
    eng.save_project_snapshot("example", {"title": "compatible"})
    eng.save_project_snapshot("example", {"notes": "updated"})
    assert eng.get_project_snapshot("example")["title"] == "compatible"


@pytest.mark.parametrize("prefer, title", [("nested", "old"), ("top-level", "new")])
def test_snapshot_migration_requires_explicit_conflict_choice_and_backup(tmp_path, prefer, title):
    from piia_engram.storage import _project_id
    eng = Engram(root=tmp_path / "store")
    project = str(tmp_path / "project")
    path = eng._projects_dir / (_project_id(project) + ".json")
    path.write_text(json.dumps({"snapshot": {"title": "old", "notes": "retained"}, "title": "new"}), encoding="utf-8")
    before = path.read_bytes()
    assert eng.migrate_project_snapshot(project)["status"] == "preview"
    assert path.read_bytes() == before
    assert eng.migrate_project_snapshot(project, apply=True)["error"] == "migration_required"
    result = eng.migrate_project_snapshot(project, apply=True, prefer=prefer)
    assert result["status"] == "migrated"
    assert Path(result["backup"]).read_bytes() == before
    migrated = eng.get_project_snapshot(project)
    assert migrated["title"] == title and migrated["notes"] == "retained"
    assert "snapshot" not in migrated
    eng.save_project_snapshot(project, {"current_state": {"next_actions": ["verify"]}})


@pytest.mark.parametrize("exc", [BrokenPipeError("gone"), ConnectionResetError("gone"), RuntimeError("MCP session terminated"), RuntimeError("server is shutting down")])
def test_transport_loss_has_stable_code_and_retry_guidance(exc):
    from piia_engram.transport_errors import transport_failure
    result = transport_failure(exc, idempotency_key="retry-key")
    assert result["error"] == "transport_unavailable"
    assert "restart" in result["hint"].lower() and "rerun" in result["hint"].lower()
    assert result["retry_same_key"] is True
    assert "gone" not in result["hint"]


def test_transport_classifier_does_not_hide_storage_errors():
    from piia_engram.transport_errors import transport_failure
    assert transport_failure(OSError("disk full")) is None


def test_mcp_write_and_shutdown_transport_failures(tmp_path, monkeypatch):
    import asyncio
    from piia_engram import mcp_server as server
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setattr(server, "_engram", eng)
    monkeypatch.setattr(server, "_shutting_down", True)
    out = json.loads(asyncio.run(server.save_project_snapshot("example", '{"title":"sample"}')))
    assert out["error"] == "transport_unavailable"
    assert list(eng._projects_dir.glob("*.json")) == []


def test_hook_failure_log_gives_transport_retry_hint(tmp_path):
    from piia_engram.hooks._log import log_failure
    log_failure("example", "save failed", BrokenPipeError("gone"), root=tmp_path)
    line = (tmp_path / "logs/hooks.log").read_text(encoding="utf-8")
    assert "transport_unavailable" in line and "restart" in line.lower()


def test_documented_tool_counts_match_tool_registry():
    from piia_engram.tool_surface import mcp_surface_counts
    counts = mcp_surface_counts()
    for name in ("README.md", "README.zh-CN.md", "docs/architecture.md"):
        text = (Path(__file__).parents[1] / name).read_text(encoding="utf-8")
        assert f"MCP tool counts: total={counts['total']}; Core={counts['core']}; Advanced={counts['advanced']}" in text
        assert "18 tools" not in text and "18 工具" not in text and "18 个" not in text


def test_approved_revision_retires_old_decision(tmp_path):
    eng, old = _decision_pair(tmp_path)
    new = eng.add_decision({"question": "Choose the storage engine", "choice": "PostgreSQL", "tier": "staging", "supersedes": old["id"]})
    assert eng._find_item_by_id(old["id"])[1]["status"] == "active"
    eng.apply_review({"promote": [{"id": new["id"]}]})
    assert eng._find_item_by_id(old["id"])[1]["status"] == "superseded"
    assert old["id"] not in {r["id"] for r in eng.get_decisions(limit=None, _update_access=False)}


def test_doctor_suggested_historical_local_repair(tmp_path):
    from piia_engram.cli_commands import _run_conflicts_resolve
    from piia_engram.governance_store import RelationStore
    from piia_engram.doctor import decision_consistency_check
    eng, old = _decision_pair(tmp_path)
    new = eng.add_decision({"question": "Choose cache policy", "choice": "bounded", "tier": "verified"})
    RelationStore(eng.root).add_relation(new["id"], "supersedes", old["id"])
    rc, result = _run_conflicts_resolve(eng, [new["id"], old["id"], "--action", "supersede", "--keep", new["id"], "--commit", "--yes"])
    assert rc == 0 and result["changed"] is True
    assert eng._find_item_by_id(old["id"])[1]["status"] != "active"
    assert decision_consistency_check(eng)["status"] == "PASS"


def test_pending_successor_does_not_retire_predecessor(tmp_path):
    from piia_engram.governance_store import RelationStore
    from piia_engram.doctor import decision_consistency_check
    eng, old = _decision_pair(tmp_path)
    new = eng.add_decision({"question": "Choose cache policy", "choice": "bounded", "tier": "staging"})
    RelationStore(eng.root).add_relation(new["id"], "supersedes", old["id"])
    assert old["id"] in {r["id"] for r in eng.get_decisions(limit=None, _update_access=False)}
    assert decision_consistency_check(eng)["status"] == "PASS"


def test_mcp_transport_exception_inside_write_is_normalized(tmp_path, monkeypatch):
    import asyncio
    from piia_engram import mcp_server as server
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setattr(server, "_engram", eng)
    def fail(*args, **kwargs):
        raise BrokenPipeError("gone")
    monkeypatch.setattr(server, "_locked_engram_call", fail)
    result = json.loads(asyncio.run(server.memory_store(kind="lesson", content_json='{"summary":"Use atomic writes for recovery"}', user_confirmed=True)))
    assert result["error"] == "transport_unavailable"


def test_cli_wrapper_reports_transport_loss(monkeypatch, capsys):
    from piia_engram import setup_wizard
    def fail():
        raise ConnectionResetError("gone")
    monkeypatch.setattr(setup_wizard, "_command_main", fail)
    with pytest.raises(SystemExit) as raised:
        setup_wizard.main()
    assert raised.value.code == 1
    assert json.loads(capsys.readouterr().err)["error"] == "transport_unavailable"


def test_registered_counts_match_canonical_surface_in_fresh_process():
    code = "import os; os.environ['ENGRAM_TOOLS']='all'; from piia_engram.mcp_server import mcp; from piia_engram.tool_surface import ALL_CAPABILITY_TOOLS; assert set(mcp._tool_manager._tools)==set(ALL_CAPABILITY_TOOLS)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_nested_only_snapshot_migrates_without_losing_metadata(tmp_path):
    from piia_engram.storage import _project_id
    eng = Engram(root=tmp_path / "store")
    path = eng._projects_dir / (_project_id("example") + ".json")
    path.write_text(json.dumps({"snapshot": {"title": "legacy", "notes": "retained"}, "project_folder": "example"}), encoding="utf-8")
    before = path.read_bytes()
    result = eng.migrate_project_snapshot("example", apply=True)
    assert result["status"] == "migrated"
    assert Path(result["backup"]).read_bytes() == before
    assert eng.get_project_snapshot("example")["notes"] == "retained"


def test_corrupt_snapshot_migration_backs_up_and_refuses_to_guess(tmp_path):
    from piia_engram.storage import _project_id
    eng = Engram(root=tmp_path / "store")
    path = eng._projects_dir / (_project_id("example") + ".json")
    path.write_bytes(b"{broken")
    result = eng.migrate_project_snapshot("example", apply=True)
    assert result["error"] == "migration_required"
    assert Path(result["backup"]).read_bytes() == path.read_bytes() == b"{broken"


def test_snapshot_error_reaches_mcp_write(tmp_path, monkeypatch):
    import asyncio
    from piia_engram import mcp_server as server
    from piia_engram.storage import _project_id
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setattr(server, "_engram", eng)
    path = eng._projects_dir / (_project_id("example") + ".json")
    path.write_text('{"snapshot":{"title":"old"}}', encoding="utf-8")
    result = json.loads(asyncio.run(server.save_project_snapshot("example", '{"notes":"write"}')))
    assert result["error"] == "migration_required"


def test_local_migration_cli_preview_leaves_store_unchanged(tmp_path, monkeypatch, capsys):
    from piia_engram import setup_wizard
    from piia_engram.storage import _project_id
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    path = eng._projects_dir / (_project_id("example") + ".json")
    path.write_text('{"data":{"title":"old"}}', encoding="utf-8")
    before = {str(p): p.read_bytes() for p in eng.root.rglob("*") if p.is_file()}
    monkeypatch.setattr(sys, "argv", ["engram", "migrate-project", "example"])
    with pytest.raises(SystemExit) as raised:
        setup_wizard.main()
    assert raised.value.code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "preview"
    assert {str(p): p.read_bytes() for p in eng.root.rglob("*") if p.is_file()} == before


def test_doctor_current_predecessor_fix_command_works(tmp_path):
    from piia_engram.doctor import decision_consistency_check
    from piia_engram.cli_commands import _run_conflicts_resolve
    from piia_engram.governance_store import RelationStore
    eng, old = _decision_pair(tmp_path)
    new = eng.add_decision({"question": "Choose cache policy", "choice": "bounded", "tier": "verified"})
    path = eng._knowledge_dir / "decisions.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    next(r for r in rows if r["id"] == old["id"])["status"] = "current"
    raw_write_json(path, rows)
    RelationStore(eng.root).add_relation(new["id"], "supersedes", old["id"])
    assert decision_consistency_check(eng)["status"] == "WARN"
    rc, result = _run_conflicts_resolve(eng, [new["id"], old["id"], "--action", "supersede", "--keep", new["id"], "--commit", "--yes"])
    assert rc == 0 and result["changed"]
    assert decision_consistency_check(eng)["status"] == "PASS"


def test_snapshot_corruption_between_preflight_and_lock_is_coded(tmp_path, monkeypatch):
    from piia_engram import core
    from piia_engram.storage import _project_id
    eng = Engram(root=tmp_path / "store")
    eng.save_project_snapshot("example", {"title": "valid"})
    path = eng._projects_dir / (_project_id("example") + ".json")
    original = core._update_json
    def corrupt_then_update(target, *args, **kwargs):
        if target == path:
            path.write_bytes(b"{broken")
        return original(target, *args, **kwargs)
    monkeypatch.setattr(core, "_update_json", corrupt_then_update)
    with pytest.raises(ValueError, match="migration_required"):
        eng.save_project_snapshot("example", {"notes": "write"})
    assert path.read_bytes() == b"{broken"


def test_closeout_transport_error_preserves_queryable_operation(tmp_path, monkeypatch):
    import asyncio
    from piia_engram import mcp_server as server
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setattr(server, "_engram", eng)
    original = server._locked_engram_call
    def fail_daily(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") == "append_daily_log":
            raise BrokenPipeError("gone")
        return original(fn, *args, **kwargs)
    monkeypatch.setattr(server, "_locked_engram_call", fail_daily)
    result = json.loads(asyncio.run(server.wrap_up_session(summary="Next: verify recovery", user_confirmed=True, idempotency_key="retry-closeout")))
    assert result["error"] == "transport_unavailable"
    assert result["retry_same_key"] is True
    status = json.loads(asyncio.run(server.get_wrap_up_session_status(idempotency_key="retry-closeout")))
    assert status["status"] != "not_found"


def test_mcp_closed_context_refuses_before_writing(tmp_path, monkeypatch):
    import asyncio
    from piia_engram import mcp_server as server
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setattr(server, "_engram", eng)
    def closed():
        raise RuntimeError("Session terminated")
    monkeypatch.setattr(server.mcp, "get_context", closed)
    result = json.loads(asyncio.run(server.save_project_snapshot("example", '{"title":"new"}')))
    assert result["error"] == "transport_unavailable"
    assert list(eng._projects_dir.glob("*.json")) == []


def test_anyio_and_grouped_transport_failures():
    import anyio
    from piia_engram.transport_errors import transport_failure
    assert transport_failure(anyio.ClosedResourceError())["error"] == "transport_unavailable"
    if sys.version_info >= (3, 11):
        assert transport_failure(ExceptionGroup("group", [BrokenPipeError()]))["error"] == "transport_unavailable"


def test_cleanup_helper_does_not_close_an_active_mcp_server(tmp_path, monkeypatch):
    import asyncio
    from piia_engram import mcp_server as server
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setattr(server, "_engram", eng)
    monkeypatch.setattr(server, "_shutting_down", False)
    monkeypatch.setattr(server._session, "auto_save", lambda: None)
    server._engram_clean_shutdown()
    result = asyncio.run(server.save_project_snapshot("example", '{"title":"still active"}'))
    assert "transport_unavailable" not in result
    assert eng.get_project_snapshot("example")["title"] == "still active"


def test_doctor_compatibility_facade_preserves_monkeypatch_assignment(monkeypatch):
    from piia_engram import doctor, setup_wizard
    original = setup_wizard._safe_print
    fake = lambda text: None
    monkeypatch.setattr(doctor.W, "_safe_print", fake)
    assert setup_wizard._safe_print is fake
    monkeypatch.undo()
    assert setup_wizard._safe_print is original
    fake2 = lambda text: None
    monkeypatch.setattr(setup_wizard, "_safe_print", fake2)
    assert doctor.W._safe_print is fake2


def test_readme_core_tables_list_the_actual_core_registry():
    import re
    from piia_engram.tool_surface import TIER1_TOOLS
    for name in ("README.md", "README.zh-CN.md"):
        text = (Path(__file__).parents[1] / name).read_text(encoding="utf-8")
        part = text[text.index("### Tier-1"):text.index("### Tier-2")]
        assert set(re.findall(r"^\| `(\w+)`", part, re.M)) == set(TIER1_TOOLS)


def test_readme_evidence_levels_agree_with_runbook():
    root = Path(__file__).parents[1]
    for name in ("README.md", "README.zh-CN.md"):
        text = (root / name).read_text(encoding="utf-8")
        assert "L5" in text and "L0 = untested" not in text
        assert "L2 end-to-end verified (hermes" not in text
        assert "L2 端到端验证（hermes" not in text


@pytest.mark.parametrize("filename", ["decisions.json", "relations.json"])
def test_decision_consistency_check_never_quarantines_corrupt_input(tmp_path, filename):
    from piia_engram.doctor import decision_consistency_check
    eng, _ = _decision_pair(tmp_path)
    (eng._knowledge_dir / filename).write_bytes(b"{broken")
    before = {str(p): p.read_bytes() for p in eng.root.rglob("*") if p.is_file()}
    result = decision_consistency_check(Engram(root=eng.root, read_only=True))
    assert result["status"] == "WARN" and result["read_only"] is True
    assert {str(p): p.read_bytes() for p in eng.root.rglob("*") if p.is_file()} == before


def test_nested_unknown_schema_migration_refuses_to_relabel(tmp_path):
    from piia_engram.storage import _project_id
    eng = Engram(root=tmp_path / "store")
    path = eng._projects_dir / (_project_id("example") + ".json")
    path.write_text('{"snapshot":{"schema":"project_snapshot.v99","title":"unknown"}}', encoding="utf-8")
    before = path.read_bytes()
    assert eng.migrate_project_snapshot("example", apply=True)["error"] == "migration_required"
    assert path.read_bytes() == before
