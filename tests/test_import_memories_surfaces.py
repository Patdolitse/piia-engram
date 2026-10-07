"""Where the explicit import shows up: setup, doctor, status, and auto_migrate.

- ``engram setup`` asks whether to import once now (default no) and runs the
  same flow as ``engram import-memories``; a "no" stores no switch.
- ``engram doctor`` / ``engram status`` show a read-only count and explain the
  old switches; neither writes to the store.
- ``auto_migrate`` (stdio server start) stays config-only and leaves one audit
  line per installed version.

HOME / USERPROFILE point at a temporary directory with sample files (conftest
``other_ai_tools_home``).
"""

from __future__ import annotations

import hashlib
import io
import json
from contextlib import redirect_stdout

import pytest

from piia_engram.core import Engram

IMPORT_TOOLS = {"auto_reconcile", "config_scan"}


@pytest.fixture
def store(tmp_path, monkeypatch, other_ai_tools_home):
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    for var in ("ENGRAM_TELEMETRY", "DO_NOT_TRACK", "NO_TELEMETRY"):
        monkeypatch.delenv(var, raising=False)
    Engram(root=root).add_lesson(
        "Pin test fixtures to a temporary store so suites never share state",
        domain="testing",
        source_tool="cli",
    )
    return root


def _snapshot(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _imported_rows(root):
    rows = Engram(root=root, read_only=True).get_lessons(limit=None, _update_access=False)
    return [row for row in rows if row.get("source_tool") in IMPORT_TOOLS]


def _answers(monkeypatch, *values):
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(it, ""))


def _telemetry_config(root):
    path = root / "telemetry_config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


# -- setup -------------------------------------------------------------------


def test_setup_default_answer_imports_nothing_and_stores_no_switch(store, monkeypatch, capsys):
    from piia_engram.setup_wizard import _run_privacy_preferences

    _answers(monkeypatch, "", "")  # import now: default (no); statistics: default
    _run_privacy_preferences(str(store))

    out = capsys.readouterr().out
    assert "engram import-memories" in out
    assert _imported_rows(store) == []
    assert not (store / "import_receipts").exists()
    assert "reconcile_authorized" not in _telemetry_config(store)


@pytest.mark.parametrize("flow", ["preferences", "defaults"])
def test_setup_yes_lists_then_imports_into_the_review_queue(store, monkeypatch, capsys, flow):
    from piia_engram import setup_wizard

    _answers(monkeypatch, "y", "y", "n", "n")  # import now, confirm, statistics no
    if flow == "preferences":
        setup_wizard._run_privacy_preferences(str(store))
    else:
        setup_wizard._run_privacy_defaults(str(store))

    out = capsys.readouterr().out
    assert "lint_rule.md" in out  # the list came first
    rows = _imported_rows(store)
    assert len(rows) >= 5
    assert all((row.get("tier") or row.get("memory_state")) == "staging" for row in rows)
    assert len(list((store / "import_receipts").glob("*.json"))) == 1


def test_setup_yes_then_no_at_the_list_writes_nothing(store, monkeypatch):
    from piia_engram.setup_wizard import _run_privacy_preferences

    _answers(monkeypatch, "y", "n", "")
    knowledge_before = {
        rel: digest for rel, digest in _snapshot(store).items() if rel.startswith("knowledge/")
    }
    _run_privacy_preferences(str(store))

    knowledge_after = {
        rel: digest for rel, digest in _snapshot(store).items() if rel.startswith("knowledge/")
    }
    assert knowledge_after == knowledge_before
    assert not (store / "import_receipts").exists()


def test_setup_yes_lifts_an_earlier_stored_no(store, monkeypatch):
    from piia_engram.setup_wizard import _run_privacy_preferences

    (store / "telemetry_config.json").write_text(
        json.dumps({"reconcile_authorized": False}), encoding="utf-8"
    )
    _answers(monkeypatch, "y", "y", "")
    _run_privacy_preferences(str(store))

    assert _telemetry_config(store)["reconcile_authorized"] is True
    assert _imported_rows(store)


# -- doctor / status -----------------------------------------------------------


def _doctor_output(fix=False):
    from piia_engram import doctor, setup_wizard  # noqa: F401  (doctor is imported through it)

    buf = io.StringIO()
    with redirect_stdout(buf):
        doctor._run_functional_checks(fix=fix)
    return buf.getvalue()


def test_doctor_counts_importable_memories_without_writing(store):
    before = _snapshot(store)

    out = _doctor_output()

    assert "Other AI tools' memories:" in out
    assert "engram import-memories" in out
    line = next(line for line in out.splitlines() if "Other AI tools' memories:" in line)
    assert "6" in line
    assert _snapshot(store) == before
    assert _imported_rows(store) == []


def test_doctor_explains_the_old_switches(store, monkeypatch):
    monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", "eager")
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")

    out = _doctor_output()

    assert "ENGRAM_MCP_STARTUP_SYNC=eager no longer has any effect" in out
    assert "ENGRAM_RECONCILE=1 no longer turns on any automatic import" in out

    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    out = _doctor_output()
    assert "ENGRAM_RECONCILE=0: other AI tools' files are never read" in out
    assert "switched off" in out or "已关闭" in out


def test_status_shows_the_count_and_stays_read_only(store):
    from piia_engram.status_report import build_status, render_status_text

    before = _snapshot(store)

    plain = build_status(probe=False, root=store)
    assert "external_memories" not in plain  # other callers (desktop client) unchanged

    status = build_status(probe=False, root=store, external_memories=True)
    assert status["external_memories"]["count"] == 6
    text = render_status_text(status)
    assert "Other AI tools' memories:" in text
    assert any("engram import-memories" in warning for warning in status["warnings"])
    assert _snapshot(store) == before


def test_status_cli_includes_the_count(store, capsys):
    from piia_engram.cli_commands import run_status

    assert run_status(["--no-probe"]) == 0
    assert "Other AI tools' memories:" in capsys.readouterr().out


# -- auto_migrate ----------------------------------------------------------------


def test_auto_migrate_is_config_only_idempotent_and_audited(store):
    from piia_engram.setup_wizard import auto_migrate

    knowledge = {rel: d for rel, d in _snapshot(store).items()
                 if rel.split("/")[0] in ("knowledge", "identity")}

    auto_migrate()
    auto_migrate()

    lines = [json.loads(line) for line in (store / "audit.log").read_text(encoding="utf-8").splitlines()
             if line.strip()]
    migrate_lines = [line for line in lines if line.get("resource") == "config/auto_migrate"]
    assert len(migrate_lines) == 1  # once per installed version
    assert "memory_content=untouched" in migrate_lines[0]["detail"]
    assert {rel: d for rel, d in _snapshot(store).items()
            if rel.split("/")[0] in ("knowledge", "identity")} == knowledge
    assert _imported_rows(store) == []


# -- every import goes through review with a receipt -----------------------------


def _verified_ids(root):
    rows = Engram(root=root, read_only=True).get_lessons(limit=None, _update_access=False)
    return {row["id"] for row in rows if (row.get("tier") or row.get("memory_state")) != "staging"}


def test_setup_seed_import_uses_the_review_queue_and_writes_a_receipt(store, tmp_path, monkeypatch, capsys):
    from piia_engram.setup_wizard import _run_seed_knowledge_onboarding

    monkeypatch.setattr("piia_engram.setup_wizard._probe_environment", lambda cwd=None: {})
    project = tmp_path / "work"
    project.mkdir()
    (project / "AGENTS.md").write_text(
        "## Builds\nRun the release build only from a clean checkout of main.\n", encoding="utf-8"
    )
    verified_before = _verified_ids(store)
    # role, tech stack, language, first lesson, import now?, import these?
    _answers(monkeypatch, "", "", "", "", "y", "y")

    summary = _run_seed_knowledge_onboarding(str(store), cwd=project)

    out = capsys.readouterr().out
    assert "engram review" in out
    rows = _imported_rows(store)
    assert len(rows) == summary["imported_to_review"] >= 6  # home samples + the project file
    assert all((row.get("tier") or row.get("memory_state")) == "staging" for row in rows)
    assert any(row["summary"].startswith("[AGENTS.md] Builds:") for row in rows)
    assert _verified_ids(store) == verified_before  # no verified rows from setup
    receipt = json.loads((store / summary["import_receipt"]).read_text(encoding="utf-8"))
    assert receipt["imported"] == len(rows)
    assert any(f["file"].endswith("work/AGENTS.md") for f in receipt["files"])
    assert "language" not in Engram(root=store, read_only=True).get_profile()


def test_setup_seed_step_default_no_imports_nothing(store, tmp_path, monkeypatch):
    from piia_engram.setup_wizard import _run_seed_knowledge_onboarding

    monkeypatch.setattr("piia_engram.setup_wizard._probe_environment", lambda cwd=None: {})
    _answers(monkeypatch, "", "", "", "", "")
    summary = _run_seed_knowledge_onboarding(str(store), cwd=tmp_path)

    assert summary["imported_to_review"] == 0 and summary["import_receipt"] == ""
    assert _imported_rows(store) == []
    assert not (store / "import_receipts").exists()


def test_privacy_step_does_not_ask_again_inside_setup(store, monkeypatch, capsys):
    from piia_engram.setup_wizard import _run_privacy_defaults, _run_privacy_preferences

    _answers(monkeypatch, "n", "n")
    _run_privacy_preferences(str(store), offer_import=False)
    _run_privacy_defaults(str(store), offer_import=False)
    out = capsys.readouterr().out
    assert "Import once now?" not in out and "现在导入一次吗" not in out


def test_reconcile_apply_preview_writes_nothing_and_commit_writes_receipt(store, monkeypatch, capsys):
    from piia_engram.setup_wizard import _run_reconcile

    before = _snapshot(store)
    assert _run_reconcile(["apply", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True and preview["counts"]["import"] == 2
    assert _snapshot(store) == before

    verified_before = _verified_ids(store)
    assert _run_reconcile(["apply", "--commit", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "receipt: import_receipts/" in out
    rows = _imported_rows(store)
    assert len(rows) == 2 and {row["source_tool"] for row in rows} == {"auto_reconcile"}
    assert all((row.get("tier") or row.get("memory_state")) == "staging" for row in rows)
    assert _verified_ids(store) == verified_before
    assert len(list((store / "import_receipts").glob("*.json"))) == 1


# -- every library write path records through the same receipt helper -------------


def _receipts(root):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root / "import_receipts").glob("*.json"))]


def test_apply_reconcile_commit_writes_a_receipt(store):
    from piia_engram.reconcile_apply import apply_reconcile

    payload = apply_reconcile(
        Engram(root=store),
        [{"summary": "prefer small reviewable commits over large mixed ones", "detail": "x",
          "source": "mem.md"}],
        source="memory_files", confirm=True, dry_run=False,
    )
    assert payload["counts"]["imported"] == 1
    receipt = json.loads((store / payload["receipt"]).read_text(encoding="utf-8"))
    assert receipt["command"] == "reconcile_apply.apply_reconcile"
    assert receipt["items"][0]["id"] == payload["items"][0]["imported_id"]
    assert "reviewable" not in json.dumps(receipt)


def test_bootstrap_writes_to_the_review_queue_with_a_receipt(store, tmp_path, monkeypatch):
    import piia_engram.bootstrap as bs

    rules = tmp_path / "rules.md"
    rules.write_text("# Rules\nI prefer concise answers.\nAlways add a test with a fix.\n", encoding="utf-8")
    monkeypatch.setattr(bs, "_scan_rule_files", lambda: [
        {"path": rules, "scope": "global", "lines": rules.read_text(encoding="utf-8").splitlines()},
    ])
    eng = Engram(root=store)
    (store / ".bootstrap_done").unlink(missing_ok=True)
    bs.run_bootstrap(eng)

    rows = [r for r in eng.get_lessons(limit=None, _update_access=False) if r.get("source_tool") == "engram_bootstrap"]
    assert rows and {r["tier"] for r in rows} == {"staging"}
    receipt = _receipts(store)[-1]
    assert receipt["command"] == "bootstrap.run_bootstrap"
    assert {i["id"] for i in receipt["items"]} == {r["id"] for r in rows}


def _write_legacy_dir(tmp_path):
    legacy = tmp_path / "legacy-memory"
    legacy.mkdir()
    (legacy / "near_misses.json").write_text(json.dumps([
        {"what_happened": "deployed without running the migration check",
         "what_could_have_happened": "the schema would have drifted"},
        {"what_happened": "merged with a failing lint job",
         "what_could_have_happened": "style drift across the codebase"},
    ]), encoding="utf-8")
    return legacy


def test_legacy_memory_migration_writes_to_the_review_queue_with_a_receipt(store, tmp_path):
    from piia_engram.compat import migrate_from_oca_memory

    eng = Engram(root=store)
    migrate_from_oca_memory(str(_write_legacy_dir(tmp_path)), eng)

    # project-tagged rows are not listed without a project, so read the file
    stored = json.loads((store / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    rows = [r for r in stored if r.get("domain") == "safety"]
    assert len(rows) == 2 and {r["tier"] for r in rows} == {"staging"}
    receipt = _receipts(store)[-1]
    assert receipt["command"] == "legacy_memory_migration"
    assert receipt["sources"] == ["legacy_memory_migration"]
    assert {i["id"] for i in receipt["items"]} == {r["id"] for r in rows}


def test_recording_writes_a_partial_receipt_and_reraises(store):
    from piia_engram import memory_import

    eng = Engram(root=store)
    with pytest.raises(ValueError):
        with memory_import.recording(eng, sources=["x"], command="test", resource="knowledge/test",
                                     source_tool="test") as record:
            record.add_written("L-1", source="x", file="a.md", summary="s")
            raise ValueError("stop")
    receipt = _receipts(store)[-1]
    assert receipt["status"] == "partial" and receipt["error"] == "ValueError"
    assert [i["id"] for i in receipt["items"]] == ["L-1"]
    audit = (store / "audit.log").read_text(encoding="utf-8")
    assert "partial error=ValueError" in audit


def test_doctor_states_the_import_limits(store):
    out = _doctor_output()
    assert "rule-file sections at most 25 per run" in out
    assert "ENGRAM_REVIEW_QUEUE_MAX" in out


# -- audit log stays metadata-only ---------------------------------------------------


_SAMPLE_TEXTS = ("linter", "staging bucket", "second human review", "small pure functions",
                 "concise answers", "commit messages")


def _audit_text(root):
    path = root / "audit.log"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def test_import_memories_keeps_imported_text_out_of_the_audit_log(store):
    from piia_engram import memory_import

    assert memory_import.run_cli(["--yes"]) == 0
    assert _imported_rows(store)
    audit = _audit_text(store)
    assert "knowledge/import_memories" in audit
    for text in _SAMPLE_TEXTS:
        assert text not in audit


def test_reconcile_apply_keeps_imported_text_out_of_the_audit_log(store, capsys):
    from piia_engram.setup_wizard import _run_reconcile

    assert _run_reconcile(["apply", "--commit", "--yes"]) == 0
    assert _imported_rows(store)
    audit = _audit_text(store)
    assert "knowledge/import_memories" in audit
    for text in _SAMPLE_TEXTS:
        assert text not in audit


# -- interruptions are recorded, then re-raised -----------------------------------------


def _interrupt_on_call(monkeypatch, cls, name, call_no):
    real = getattr(cls, name)
    calls = {"n": 0}

    def wrapped(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == call_no:
            raise KeyboardInterrupt
        return real(self, *args, **kwargs)

    monkeypatch.setattr(cls, name, wrapped)


def test_an_interrupted_import_records_a_partial_receipt_not_a_full_queue(store, monkeypatch):
    from piia_engram import memory_import

    preview = memory_import.plan(Engram(root=store, read_only=True))
    _interrupt_on_call(monkeypatch, Engram, "add_lesson", 3)

    with pytest.raises(KeyboardInterrupt):
        memory_import.write_plan(Engram(root=store), preview)

    receipt = _receipts(store)[-1]
    assert receipt["status"] == "partial" and receipt["error"] == "KeyboardInterrupt"
    assert receipt["stopped_by"] == "error"
    assert receipt["skipped"]["queue_full"] == 0
    assert receipt["imported"] == 2 and receipt["not_written"] == preview["count"] - 2
    assert "partial error=KeyboardInterrupt" in _audit_text(store)


def test_every_recording_path_writes_a_receipt_when_interrupted(store, tmp_path, monkeypatch):
    import piia_engram.bootstrap as bs
    from piia_engram import reconcile_apply
    from piia_engram.compat import import_from_openclaw, migrate_from_oca_memory

    # 1) reconcile_apply.apply_reconcile
    real_one = reconcile_apply._import_one
    calls = {"n": 0}

    def flaky_one(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real_one(*args, **kwargs)

    monkeypatch.setattr(reconcile_apply, "_import_one", flaky_one)
    with pytest.raises(KeyboardInterrupt):
        reconcile_apply.apply_reconcile(
            Engram(root=store),
            [{"summary": "first candidate about release notes wording"},
             {"summary": "second candidate about changelog grouping order"}],
            source="memory_files", confirm=True, dry_run=False,
        )
    monkeypatch.setattr(reconcile_apply, "_import_one", real_one)

    # 2) bootstrap, 3) OpenClaw, 4) legacy migration: interrupt the second add_lesson
    rules = tmp_path / "rules.md"
    rules.write_text("# Rules\nI prefer concise answers.\nThis repo uses pytest for tests.\n",
                     encoding="utf-8")
    monkeypatch.setattr(bs, "_scan_rule_files", lambda: [
        {"path": rules, "scope": "global", "lines": rules.read_text(encoding="utf-8").splitlines()},
    ])
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("## Lessons Learned\n- first openclaw lesson about retries\n"
                         "- second openclaw lesson about timeouts\n", encoding="utf-8")
    legacy = _write_legacy_dir(tmp_path)
    runs = [
        lambda eng: bs.run_bootstrap(eng),
        lambda eng: import_from_openclaw(eng, memory_path=str(memory_md)),
        lambda eng: migrate_from_oca_memory(str(legacy), eng),
    ]
    for run in runs:
        _interrupt_on_call(monkeypatch, Engram, "add_lesson", 2)
        with pytest.raises(KeyboardInterrupt):
            run(Engram(root=store))
        monkeypatch.undo()
        monkeypatch.setenv("ENGRAM_DIR", str(store))
        monkeypatch.setenv("ENGRAM_AUDIT", "1")

    receipts = _receipts(store)
    commands = [r["command"] for r in receipts]
    for command in ("reconcile_apply.apply_reconcile", "bootstrap.run_bootstrap",
                    "engram import --format openclaw", "legacy_memory_migration"):
        receipt = receipts[commands.index(command)]
        assert receipt["status"] == "partial", command
        assert receipt["error"] == "KeyboardInterrupt", command
        assert receipt["imported"] == 1, command


# -- setup lifts a stored "no" only at the second yes ----------------------------------------


def test_setup_keeps_a_stored_no_when_the_list_is_declined(store, monkeypatch, capsys):
    from piia_engram.setup_wizard import _run_privacy_preferences

    (store / "telemetry_config.json").write_text(
        json.dumps({"reconcile_authorized": False}), encoding="utf-8"
    )
    knowledge_before = {
        rel: digest for rel, digest in _snapshot(store).items() if rel.startswith("knowledge/")
    }
    _answers(monkeypatch, "y", "n", "")  # import now: yes; at the list: no

    _run_privacy_preferences(str(store))

    assert "lint_rule.md" in capsys.readouterr().out  # the list was shown
    assert _telemetry_config(store)["reconcile_authorized"] is False
    assert {
        rel: digest for rel, digest in _snapshot(store).items() if rel.startswith("knowledge/")
    } == knowledge_before
    assert not (store / "import_receipts").exists()


def test_setup_never_lifts_the_environment_switch(store, monkeypatch, capsys):
    from piia_engram.setup_wizard import _run_privacy_preferences

    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    _answers(monkeypatch, "y", "y", "")
    _run_privacy_preferences(str(store))
    assert "ENGRAM_RECONCILE=0" in capsys.readouterr().out
    assert _imported_rows(store) == []
    assert "reconcile_authorized" not in _telemetry_config(store)


# -- the switch follows the store being imported into ------------------------------------------


def test_the_switch_and_the_lift_follow_the_chosen_store(tmp_path, monkeypatch, other_ai_tools_home):
    from piia_engram import memory_import
    from piia_engram.setup_wizard import _offer_setup_import

    ambient = tmp_path / "ambient-store"
    chosen = tmp_path / "chosen-store"
    monkeypatch.setenv("ENGRAM_DIR", str(ambient))
    Engram(root=ambient)
    Engram(root=chosen)
    (chosen / "telemetry_config.json").write_text(
        json.dumps({"reconcile_authorized": False, "enabled": False}), encoding="utf-8"
    )

    assert memory_import.switch_state(chosen) == {
        "enabled": False, "disabled_by": "reconcile_authorized=false",
    }
    assert memory_import.switch_state(ambient)["enabled"] is True
    assert memory_import.importable_summary(chosen)["enabled"] is False

    _answers(monkeypatch, "y", "y")
    payload = _offer_setup_import(str(chosen))

    assert payload["imported"] >= 5
    cfg = _telemetry_config(chosen)
    assert cfg["reconcile_authorized"] is True and cfg["enabled"] is False  # other keys kept
    assert not (ambient / "telemetry_config.json").exists()
    assert _imported_rows(chosen) and not _imported_rows(ambient)


# -- reconcile apply writes what the reconcile previews show ------------------------------------


def test_reconcile_apply_skips_what_the_proposal_calls_a_conflict(store, monkeypatch, capsys):
    from piia_engram import reconcile_proposal
    from piia_engram.setup_wizard import _run_reconcile

    real_classify = reconcile_proposal.classify_candidate

    def classify(candidate, existing, **kwargs):
        verdict = real_classify(candidate, existing, **kwargs)
        if "staging bucket" in str(candidate.get("summary", "")):
            return dict(verdict, action="conflict", reason="same_question_different_choice")
        return verdict

    monkeypatch.setattr(reconcile_proposal, "classify_candidate", classify)

    assert _run_reconcile(["conflicts", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["counts"]["conflict"] == 1

    assert _run_reconcile(["apply", "--commit", "--yes", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["conflict"] == 1
    assert payload["counts"]["imported"] == 1
    summaries = [row["summary"] for row in _imported_rows(store)]
    assert not any("staging bucket" in s for s in summaries)
    assert any("linter" in s for s in summaries)


# -- OpenClaw and bootstrap obey the hard off switch --------------------------------------------


def test_openclaw_and_bootstrap_refuse_when_reading_is_off(store, tmp_path, monkeypatch, capsys):
    import piia_engram.bootstrap as bs
    from piia_engram.cli_commands import _run_privacy_report
    from piia_engram.compat import import_from_openclaw

    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("## Lessons Learned\n- an openclaw lesson about retries\n", encoding="utf-8")
    user_md = tmp_path / "USER.md"
    user_md.write_text("- Role: tester\n- Language: English\n", encoding="utf-8")
    monkeypatch.setattr(bs, "_scan_rule_files", lambda: (_ for _ in ()).throw(AssertionError("read")))
    before = _snapshot(store)

    result = import_from_openclaw(Engram(root=store), memory_path=str(memory_md), user_path=str(user_md))
    assert result["status"] == "disabled" and result["disabled_by"] == "ENGRAM_RECONCILE=0"
    boot = bs.run_bootstrap(Engram(root=store))
    assert boot["status"] == "disabled"
    reader = Engram(root=store, read_only=True)
    assert "role" not in reader.get_profile() or reader.get_profile().get("role") != "tester"
    assert {r: d for r, d in _snapshot(store).items() if r.startswith(("knowledge/", "identity/"))} == {
        r: d for r, d in before.items() if r.startswith(("knowledge/", "identity/"))
    }

    _run_privacy_report()
    out = capsys.readouterr().out
    assert "engram import --format openclaw" in out and "refuse" in out


# -- engine results carry no item text --------------------------------------------------------


def test_engine_results_carry_no_item_text(store):
    eng = Engram(root=store)
    preview = eng.reconcile_memories(dry_run=True)
    written = eng.reconcile_ai_configs()
    for result in (preview, written):
        assert result["items"]
        for item in result["items"]:
            assert set(item) == {"id", "source", "file", "content_sha256", "status"}
    assert json.dumps(written).find("second human review") == -1


# -- concurrency and receipt names ---------------------------------------------------------------


def test_two_concurrent_imports_respect_the_queue_limit(store, monkeypatch):
    import threading

    from piia_engram import memory_import

    monkeypatch.setenv("ENGRAM_REVIEW_QUEUE_MAX", "4")
    preview = memory_import.plan(Engram(root=store, read_only=True))
    assert preview["count"] >= 6
    halves = [dict(preview, items=preview["items"][:3]), dict(preview, items=preview["items"][3:6])]
    results = []
    barrier = threading.Barrier(2)

    def worker(part):
        barrier.wait()
        results.append(memory_import.write_plan(Engram(root=store), part))

    threads = [threading.Thread(target=worker, args=(part,)) for part in halves]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert len(results) == 2
    assert sum(r["imported"] for r in results) == 4
    assert len(_imported_rows(store)) == 4
    assert sum(r["queue_full"] for r in results) == 2


def test_receipt_names_are_long_random_and_never_overwritten(store, monkeypatch):
    import secrets as _secrets

    from piia_engram import memory_import

    tokens = iter(["a" * 16, "a" * 16, "b" * 16])
    monkeypatch.setattr(_secrets, "token_hex", lambda n=None: next(tokens))
    payload = {"items": [], "imported": 0, "sources": ["memories"], "partial": True, "error": "X"}
    first_id, first = memory_import.write_receipt(store, payload)
    second_id, second = memory_import.write_receipt(store, payload)

    assert first_id.endswith("a" * 16) and second_id.endswith("b" * 16)
    assert first != second and first.is_file() and second.is_file()
    assert json.loads(first.read_text(encoding="utf-8"))["receipt_id"] == first_id
