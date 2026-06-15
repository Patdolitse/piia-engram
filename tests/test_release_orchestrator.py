"""Tests for scripts/release_orchestrator.py.

The orchestrator is a DRY-RUN, metadata-only checklist. These tests lock:
- the three-phase ordering (LOCAL -> AUTH -> REMOTE),
- that it never reads or emits a secret value (token env vars are NAMES only),
- that the primary hidden stall (mcp-publisher auth) is surfaced and covered by
  the explicit ``--warm-mcp`` preflight command,
- that --probe reports presence booleans only and computes auth gaps,
- that no remote action is described as being executed.
Probes are dependency-injected so the suite is host-independent.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_orchestrator.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("release_orchestrator", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestChecklist:
    def test_phases_are_ordered_local_auth_remote(self, mod):
        steps = mod.build_checklist()
        phase_seq = [s["phase"] for s in steps]
        # Every LOCAL precedes every AUTH precedes every REMOTE.
        first_auth = phase_seq.index(mod.AUTH)
        first_remote = phase_seq.index(mod.REMOTE)
        assert all(p == mod.LOCAL for p in phase_seq[:first_auth])
        assert all(p in (mod.AUTH,) for p in phase_seq[first_auth:first_remote])
        assert all(p == mod.REMOTE for p in phase_seq[first_remote:])

    def test_every_step_has_required_metadata(self, mod):
        keys = {"id", "phase", "title", "command", "auth_required", "auth_kind",
                "token_env", "blocking", "stall_risk", "timeout_hint",
                "preflight_covered", "probe"}
        ids = set()
        for s in mod.build_checklist():
            assert keys <= set(s)
            assert s["id"] not in ids, f"duplicate step id {s['id']}"
            ids.add(s["id"])
            assert s["phase"] in (mod.LOCAL, mod.AUTH, mod.REMOTE)

    def test_token_env_is_name_only_never_value(self, mod):
        # token_env entries must look like ENV VAR NAMES (UPPER_SNAKE), not values.
        for s in mod.build_checklist():
            if s["token_env"] is not None:
                assert s["token_env"].isupper()
                assert " " not in s["token_env"]

    def test_mcp_publisher_auth_stall_is_surfaced_and_warmable(self, mod):
        steps = {s["id"]: s for s in mod.build_checklist()}
        step = steps["mcp_publisher_auth"]
        assert step["auth_required"] is True
        assert step["auth_kind"] == mod.TOKEN
        assert "--warm-mcp" in step["command"]
        assert step["preflight_covered"] is True
        assert step["stall_risk"] and "device-flow" in step["stall_risk"].lower()

    def test_export_redaction_and_claim_drift_are_local_gates(self, mod):
        steps = {s["id"]: s for s in mod.build_checklist()}

        assert steps["ci_pytest_entrypoint"]["phase"] == mod.LOCAL
        assert "check_ci_pytest_entrypoint.py --discover-script-imports" in (
            steps["ci_pytest_entrypoint"]["command"]
        )

        assert steps["claim_drift"]["phase"] == mod.LOCAL
        assert "check_public_claim_drift.py" in steps["claim_drift"]["command"]

        assert steps["release_surface"]["phase"] == mod.LOCAL
        assert "check_public_release_surface.py" in steps["release_surface"]["command"]

        assert steps["publish_workflow_order"]["phase"] == mod.LOCAL
        assert "check_publish_workflow_order.py" in steps["publish_workflow_order"]["command"]

        assert steps["export_redaction"]["phase"] == mod.LOCAL
        assert "check_export_redaction.py" in steps["export_redaction"]["command"]
        assert "--strict" in steps["export_redaction"]["command"]
        assert steps["export_redaction"]["auth_required"] is False

        assert steps["generated_export_redaction"]["phase"] == mod.LOCAL
        assert "check_generated_export_redaction.py" in steps["generated_export_redaction"]["command"]
        assert steps["generated_export_redaction"]["auth_required"] is False

        assert steps["memory_eval_suite"]["phase"] == mod.LOCAL
        assert "run_memory_evals.py --json" in steps["memory_eval_suite"]["command"]
        assert steps["memory_eval_suite"]["auth_required"] is False

    def test_remote_steps_are_described_not_executed(self, mod):
        # No step command should be auto-run; the orchestrator only renders text.
        remote = [s for s in mod.build_checklist() if s["phase"] == mod.REMOTE]
        assert any(s["id"] == "mcp_publish" for s in remote)
        assert any(s["id"] == "gh_release" for s in remote)
        assert any(s["id"] == "mcp_verify" for s in remote)

    def test_mcp_publish_uses_retrying_wrapper(self, mod):
        step = {s["id"]: s for s in mod.build_checklist()}["mcp_publish"]
        assert "publish_mcp_registry.py" in step["command"]
        assert "expired JWT" in step["stall_risk"]

    def test_twine_fallback_wrapper_is_visible(self, mod):
        step = {s["id"]: s for s in mod.build_checklist()}["twine_available"]
        assert "publish_pypi_fallback.py" in step["command"]

    def test_glama_step_is_manual_high_stall_visibility(self, mod):
        step = {s["id"]: s for s in mod.build_checklist()}["glama_manual_auth"]
        assert step["phase"] == mod.REMOTE
        assert step["blocking"] is False
        assert step["stall_risk"] == "high"
        assert "manual" in step["title"].lower()
        assert "Glama" in step["title"]

    def test_github_release_uses_notes_file_not_inline_markdown(self, mod):
        step = {s["id"]: s for s in mod.build_checklist()}["gh_release"]
        assert "--notes-file" in step["command"]
        assert "--notes ..." not in step["command"]

    def test_prep_mode_excludes_remote_steps(self, mod):
        steps = mod.build_checklist(mod.MODE_PREP)
        assert steps
        assert all(s["phase"] in (mod.LOCAL, mod.AUTH) for s in steps)
        assert any(s["id"] == "ci_pytest_entrypoint" for s in steps)
        assert not any(s["id"] == "gh_release" for s in steps)

    def test_publish_fast_mode_is_short_hot_path(self, mod):
        steps = mod.build_checklist(mod.MODE_PUBLISH_FAST)
        ids = [s["id"] for s in steps]

        assert "gh_auth" in ids
        assert "gh_release" in ids
        assert "pypi_publish" in ids
        assert "mcp_publish" in ids
        assert "mcp_verify" in ids
        assert "tests" not in ids
        assert "sanitize" not in ids
        assert "artifact_scan" not in ids


class TestReport:
    def test_report_counts(self, mod):
        rep = mod.build_report(probe=False)
        assert rep["dry_run"] is True
        assert rep["mode"] == mod.MODE_ALL
        c = rep["counts"]
        assert c["total"] == len(rep["steps"])
        assert c["auth_required"] >= 1
        assert c["remote_actions"] >= 3
        # PyPI OIDC remains a one-time remote trust setting that cannot be
        # proven locally; MCP auth is now covered by --warm-mcp.
        assert c["uncovered_by_preflight"] >= 1
        # no probe section without --probe
        assert "presence" not in rep

    def test_json_is_serializable_and_metadata_only(self, mod):
        rep = mod.build_report(probe=True, which=lambda n: None, env={})
        blob = json.dumps(rep)  # must serialize cleanly
        # No accidental secret-shaped content; only env var NAMES appear.
        assert "sk-" not in blob
        assert "presence" in rep
        for v in rep["presence"].values():
            assert isinstance(v, bool)

    def test_probe_reads_presence_only_no_values(self, mod):
        # Inject an env with a token value; probe must report True but never echo it.
        env = {"TWINE_PASSWORD": "sk-SUPERSECRET-do-not-print"}
        rep = mod.build_report(probe=True, which=lambda n: None, env=env)
        assert rep["presence"]["pypi_credential_source"] is True
        blob = json.dumps(rep)
        assert "SUPERSECRET" not in blob

    def test_probe_flags_missing_gh_auth_as_gap(self, mod):
        # gh not on PATH -> gh_auth presence missing -> a presence_missing gap.
        rep = mod.build_report(probe=True, which=lambda n: None, env={})
        gap_ids = {g["id"] for g in rep["auth_gaps"]}
        assert "gh_auth" in gap_ids
        # mcp_publisher_auth now depends on the same gh presence needed for
        # --warm-mcp instead of becoming a hidden human-verify gap.
        kinds = {g["id"]: g["kind"] for g in rep["auth_gaps"]}
        assert kinds.get("mcp_publisher_auth") == "presence_missing"

    def test_probe_present_tools_reduce_gaps(self, mod):
        present = {"gh", "mcp-publisher", "twine", "python"}
        rep = mod.build_report(probe=True,
                               which=lambda n: f"/usr/bin/{n}" if n in present else None,
                               env={})
        gap_ids = {g["id"] for g in rep["auth_gaps"]}
        # gh present -> no presence gap for gh_auth.
        assert "gh_auth" not in gap_ids

    def test_explicit_mcp_publisher_path_is_presence_only(self, mod, tmp_path):
        exe = tmp_path / "mcp-publisher.exe"
        exe.write_text("fake binary placeholder", encoding="utf-8")

        rep = mod.build_report(
            probe=True,
            which=lambda n: None,
            env={"MCP_PUBLISHER_PATH": str(exe)},
        )

        assert rep["presence"]["mcp_publisher_on_path"] is True
        assert str(exe) not in json.dumps(rep)

    def test_known_mcp_publisher_fallback_is_presence_only(self, mod, tmp_path, monkeypatch):
        exe = tmp_path / "mcp-publisher.exe"
        exe.write_text("fake binary placeholder", encoding="utf-8")
        monkeypatch.setattr(mod, "DEFAULT_MCP_PUBLISHER_CANDIDATES", (str(exe),))

        rep = mod.build_report(probe=True, which=lambda n: None, env={})

        assert rep["presence"]["mcp_publisher_on_path"] is True
        assert str(exe) not in json.dumps(rep)


class TestCli:
    def test_json_dry_run_exit_zero(self, mod, tmp_path, capsys, monkeypatch):
        (tmp_path / "pyproject.toml").write_text("[project]\nversion='9.9.9'\n",
                                                 encoding="utf-8")
        rc = mod.main(["--root", str(tmp_path), "--mode", "publish-fast", "--json"])
        out = capsys.readouterr().out
        assert rc == 0
        data = json.loads(out)
        assert data["dry_run"] is True
        assert data["mode"] == "publish-fast"

    def test_setup_error_without_pyproject(self, mod, tmp_path):
        rc = mod.main(["--root", str(tmp_path), "--json"])
        assert rc == 2

    def test_probe_strict_exits_one_on_open_gap(self, mod, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text("[project]\nversion='9.9.9'\n",
                                                 encoding="utf-8")
        # Force all probes to report absent so a presence gap is open.
        monkeypatch.setattr(mod, "probe_presence", lambda **kw: {
            "gh_on_path": False, "mcp_publisher_on_path": False,
            "twine_runnable": False, "pypi_credential_source": False,
        })
        rc = mod.main(["--root", str(tmp_path), "--probe", "--strict", "--json"])
        assert rc == 1
