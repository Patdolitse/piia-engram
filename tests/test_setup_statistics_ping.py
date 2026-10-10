"""Setup statistics consent is independent of the daily ping preference."""

import json

import pytest

from piia_engram import i18n, setup_wizard, telemetry, usage_ping


@pytest.fixture
def active_ping(monkeypatch):
    # Keep ENGRAM_TEST for store isolation; bypass only the ping's pytest gate.
    for name in ("DO_NOT_TRACK", "NO_TELEMETRY", "ENGRAM_TELEMETRY",
                 "ENGRAM_TELEMETRY_REMOTE", "ENGRAM_FEEDBACK", "ENGRAM_EPHEMERAL",
                 "KUBERNETES_SERVICE_HOST", *usage_ping._CI_VARS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(usage_ping, "_in_test", lambda: False)
    monkeypatch.setattr(usage_ping, "_in_container", lambda: False)
    monkeypatch.setattr(i18n, "_runtime_lang", "en")

    def no_network(*args, **kwargs):
        raise AssertionError("setup consent must not send a ping")

    monkeypatch.setattr(usage_ping, "urlopen", no_network)
    assert usage_ping.decision() == (True, "default")


def _answer_setup(flow, answer, root, monkeypatch):
    prompts = []

    def respond(prompt):
        prompts.append(prompt)
        assert len(prompts) == 1, "setup must ask only one statistics question"
        return answer

    monkeypatch.setattr("builtins.input", respond)
    getattr(setup_wizard, f"_run_privacy_{flow}")(str(root), offer_import=False)
    return prompts


@pytest.mark.parametrize("flow", ["defaults", "preferences"])
@pytest.mark.parametrize("answer,enabled", [("y", True), ("n", False), ("", True)])
def test_setup_answer_controls_only_detailed_statistics(
        active_ping, tmp_path, monkeypatch, flow, answer, enabled):
    _answer_setup(flow, answer, tmp_path, monkeypatch)
    assert telemetry.is_enabled() is enabled
    assert telemetry.is_remote_enabled() is enabled
    assert telemetry.is_feedback_enabled() is enabled
    assert usage_ping.decision() == (True, "default")
    cfg = telemetry._load_config()
    assert "opted_out_at" not in cfg
    assert "remote_opted_out_at" not in cfg
    assert not (usage_ping.state_dir() / "usage_ping.json").exists()


@pytest.mark.parametrize("answer", ["y", "n", ""])
@pytest.mark.parametrize("lang", ["en", "zh"])
def test_both_paths_have_identical_question_and_state(
        active_ping, tmp_path, monkeypatch, answer, lang):
    monkeypatch.setattr(i18n, "_runtime_lang", lang)
    states = []
    questions = []
    for flow in ("defaults", "preferences"):
        root = tmp_path / flow
        monkeypatch.setenv("ENGRAM_DIR", str(root))
        questions.append(_answer_setup(flow, answer, root, monkeypatch))
        states.append((telemetry.is_enabled(), telemetry.is_remote_enabled(),
                       telemetry.is_feedback_enabled(), usage_ping.decision()))
    assert states[0] == states[1]
    assert questions[0] == questions[1]
    assert "[Y/n]" in questions[0][0]


@pytest.mark.parametrize("flow", ["defaults", "preferences"])
@pytest.mark.parametrize("answer", ["y", "n", ""])
def test_explicit_cli_off_is_preserved_by_setup(
        active_ping, tmp_path, monkeypatch, flow, answer):
    setup_wizard._run_telemetry_cli(["off"])
    _answer_setup(flow, answer, tmp_path, monkeypatch)
    assert usage_ping.decision() == (False, "settings")


@pytest.mark.parametrize("flow", ["defaults", "preferences"])
@pytest.mark.parametrize("answer", ["y", "n", ""])
@pytest.mark.parametrize("legacy", [
    {"enabled": False, "opted_out_at": "2026-01-01T00:00:00+00:00"},
    {"enabled": True, "remote_enabled": False,
     "remote_opted_out_at": "2026-01-01T00:00:00+00:00"},
])
def test_old_format_explicit_opt_out_survives_setup(
        active_ping, tmp_path, monkeypatch, flow, answer, legacy):
    root = tmp_path / "legacy-store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    path = root / "telemetry_config.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    assert usage_ping.decision() == (False, "earlier opt-out")
    _answer_setup(flow, answer, root, monkeypatch)
    assert usage_ping.decision() == (False, "earlier opt-out")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for key in ("opted_out_at", "remote_opted_out_at"):
        if key in legacy:
            assert cfg[key] == legacy[key]
    # The existing explicit opt-in command still overrides a historical refusal.
    setup_wizard._run_telemetry_cli(["on"])
    assert usage_ping.decision() == (True, "default")


@pytest.mark.parametrize("flow", ["defaults", "preferences"])
def test_statistics_no_preserves_an_explicit_ping_on(
        active_ping, tmp_path, monkeypatch, flow):
    setup_wizard._run_telemetry_cli(["on"])
    _answer_setup(flow, "n", tmp_path, monkeypatch)
    assert usage_ping.decision() == (True, "default")


def test_cli_off_still_disables_ping_after_declining_statistics(
        active_ping, tmp_path, monkeypatch):
    _answer_setup("defaults", "n", tmp_path, monkeypatch)
    assert usage_ping.decision() == (True, "default")
    setup_wizard._run_telemetry_cli(["off"])
    assert usage_ping.decision() == (False, "settings")
