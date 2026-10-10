"""Setup discloses the daily ping without asking or changing telemetry choices."""

import io
import json
import sys

import pytest

from piia_engram import i18n, setup_wizard, telemetry, usage_ping


@pytest.fixture
def active_ping(monkeypatch):
    for name in ("DO_NOT_TRACK", "NO_TELEMETRY", "ENGRAM_TELEMETRY",
                 "ENGRAM_TELEMETRY_REMOTE", "ENGRAM_FEEDBACK", "ENGRAM_EPHEMERAL",
                 "KUBERNETES_SERVICE_HOST", *usage_ping._CI_VARS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(usage_ping, "_in_test", lambda: False)
    monkeypatch.setattr(usage_ping, "_in_container", lambda: False)
    monkeypatch.setattr(i18n, "_runtime_lang", "en")

    def no_network(*args, **kwargs):
        pytest.fail("setup disclosure must not send a ping")

    monkeypatch.setattr(usage_ping, "urlopen", no_network)
    monkeypatch.setattr(telemetry, "urlopen", no_network)
    assert usage_ping.decision() == (True, "default")


def _setup(flow, root, monkeypatch, *, offer_import=False):
    prompts = []
    answers = iter(["n"] if offer_import else [])

    def respond(prompt):
        prompts.append(prompt)
        assert "statistics" not in prompt.lower() and "telemetry" not in prompt.lower()
        assert "统计" not in prompt and "使用信号" not in prompt
        return next(answers)  # No telemetry answer is available.

    monkeypatch.setattr("builtins.input", respond)
    getattr(setup_wizard, f"_run_privacy_{flow}")(str(root), offer_import=offer_import)
    assert len(prompts) == int(offer_import)


@pytest.mark.parametrize("flow", ["defaults", "preferences"])
@pytest.mark.parametrize("offer_import", [False, True])
@pytest.mark.parametrize("lang", ["en", "zh"])
def test_setup_never_asks_or_enables_statistics(
        active_ping, tmp_path, monkeypatch, flow, offer_import, lang):
    monkeypatch.setattr(i18n, "_runtime_lang", lang)
    _setup(flow, tmp_path, monkeypatch, offer_import=offer_import)
    assert telemetry.is_enabled() is False
    assert telemetry.is_remote_enabled() is False
    assert telemetry.is_feedback_enabled() is False
    assert telemetry._load_config() == {}
    assert usage_ping.decision() == (True, "default")
    assert not usage_ping.state_dir().exists()


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_both_paths_have_identical_notice_and_state(
        active_ping, tmp_path, monkeypatch, capsys, lang):
    monkeypatch.setattr(i18n, "_runtime_lang", lang)
    states, notices = [], []
    for flow in ("defaults", "preferences"):
        root = tmp_path / flow
        monkeypatch.setenv("ENGRAM_DIR", str(root))
        _setup(flow, root, monkeypatch)
        notices.append([line for line in capsys.readouterr().out.splitlines()
                        if line.startswith("[engram]")])
        states.append((telemetry._load_config(), usage_ping._load_settings(),
                       usage_ping.decision()))
    assert states[0] == states[1] == ({}, {}, (True, "default"))
    assert notices[0] == notices[1]
    assert len(notices[0]) == 1


@pytest.mark.parametrize("flow", ["defaults", "preferences"])
def test_explicit_cli_off_is_preserved_by_setup(active_ping, tmp_path, monkeypatch, flow):
    setup_wizard._run_telemetry_cli(["off"])
    before = telemetry._config_path().read_bytes()
    _setup(flow, tmp_path, monkeypatch)
    assert usage_ping.decision() == (False, "settings")
    assert telemetry._config_path().read_bytes() == before


@pytest.mark.parametrize("flow", ["defaults", "preferences"])
@pytest.mark.parametrize("legacy", [
    {"enabled": False, "opted_out_at": "2026-01-01T00:00:00+00:00"},
    {"enabled": True, "remote_enabled": False,
     "remote_opted_out_at": "2026-01-01T00:00:00+00:00"},
    {"enabled": True, "remote_enabled": True, "feedback_enabled": True},
    {"enabled": False, "legacy_ping_opted_out_at": "2026-01-01T00:00:00+00:00"},
])
def test_existing_explicit_choices_survive_setup(
        active_ping, tmp_path, monkeypatch, flow, legacy):
    root = tmp_path / "existing-store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    path = root / "telemetry_config.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before, decision = path.read_bytes(), usage_ping.decision()
    _setup(flow, root, monkeypatch)
    assert path.read_bytes() == before
    assert usage_ping.decision() == decision
    if not decision[0]:
        setup_wizard._run_telemetry_cli(["on"])
        assert usage_ping.decision() == (True, "default")


@pytest.mark.parametrize("flow", ["defaults", "preferences"])
@pytest.mark.parametrize("var,value", [("ENGRAM_TELEMETRY", "0"),
                                      ("DO_NOT_TRACK", "1"), ("NO_TELEMETRY", "1")])
def test_environment_opt_out_survives_setup(
        active_ping, tmp_path, monkeypatch, flow, var, value):
    monkeypatch.setenv(var, value)
    _setup(flow, tmp_path, monkeypatch)
    assert usage_ping.decision() == (False, var)


def test_detailed_statistics_need_explicit_commands(active_ping, tmp_path, monkeypatch):
    _setup("defaults", tmp_path, monkeypatch)
    setup_wizard._run_telemetry_cli(["on"])
    assert telemetry.is_enabled() is True
    assert telemetry.is_remote_enabled() is False
    assert telemetry.is_feedback_enabled() is False
    setup_wizard._run_telemetry_cli(["remote", "on"])
    assert telemetry.is_remote_enabled() is True
    assert telemetry.is_feedback_enabled() is False
    setup_wizard._run_telemetry_cli(["feedback", "on"])
    assert telemetry.is_feedback_enabled() is True


def test_cli_off_still_disables_ping_after_setup(active_ping, tmp_path, monkeypatch):
    _setup("defaults", tmp_path, monkeypatch)
    assert usage_ping.decision() == (True, "default")
    setup_wizard._run_telemetry_cli(["off"])
    assert usage_ping.decision() == (False, "settings")


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_setup_cli_and_mcp_share_notice_text(active_ping, tmp_path, monkeypatch, capsys, lang):
    from piia_engram import mcp_server

    monkeypatch.setattr(i18n, "_runtime_lang", lang)
    _setup("defaults", tmp_path, monkeypatch)
    setup_lines = [line for line in capsys.readouterr().out.splitlines()
                   if line.startswith("[engram]")]
    cli_stream = io.StringIO()
    setup_wizard._show_usage_notice(cli_stream)
    mcp_stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", mcp_stream)
    mcp_server._show_usage_notice()
    assert cli_stream.getvalue() == mcp_stream.getvalue()
    assert setup_lines[0] in cli_stream.getvalue().splitlines()
    text = cli_stream.getvalue()
    for phrase in ("random install ID", "Python version", "AI client name", "date",
                   "No content, prompts or file paths", "IP addresses are not stored",
                   "DO_NOT_TRACK=1", "engram telemetry preview", "engram telemetry off",
                   "PRIVACY.md", "随机安装 ID", "不发送内容、提示词或文件路径", "不保存 IP"):
        assert phrase in text


@pytest.mark.parametrize("sub", ["status", "preview"])
def test_status_preview_repeat_the_disclosure(active_ping, capsys, sub):
    setup_wizard._run_telemetry_cli([sub])
    text = capsys.readouterr().out
    for phrase in ("No content, prompts or file paths", "IP addresses are not stored",
                   "DO_NOT_TRACK=1", "PRIVACY.md", "Detailed statistics"):
        assert phrase in text


@pytest.mark.parametrize("advanced", [False, True])
@pytest.mark.parametrize("lang", ["en", "zh"])
def test_real_setup_entry_has_one_notice_and_no_telemetry_prompt(
        active_ping, tmp_path, monkeypatch, capsys, advanced, lang):
    # Main -> run_setup -> actual privacy step. Other offers are outside this
    # change and cannot install dependencies or edit clients in this test.
    monkeypatch.setattr(setup_wizard, "_find_python", lambda: sys.executable)
    monkeypatch.setattr(setup_wizard, "_detect_tools", lambda: [])
    monkeypatch.setattr(setup_wizard, "_candidate_engram_roots", lambda default: [default])
    monkeypatch.setattr(setup_wizard, "_run_hybrid_search_offer", lambda _: False)
    monkeypatch.setattr(setup_wizard, "_run_seed_knowledge_onboarding", lambda *a, **kw: None)
    monkeypatch.setattr(setup_wizard, "_start_usage_ping_cli", lambda: None)
    answers = iter(["2" if lang == "en" else "1", "1"])
    prompts = []

    def respond(prompt):
        prompts.append(prompt)
        assert "telemetry" not in prompt.lower() and "statistics" not in prompt.lower()
        assert "统计" not in prompt
        return next(answers)

    monkeypatch.setattr("builtins.input", respond)
    monkeypatch.setattr(sys, "argv", ["engram", "setup"] + (["--advanced"] if advanced else []))
    setup_wizard.main()
    out = capsys.readouterr()
    assert len(prompts) == 2
    assert len([line for line in out.out.splitlines() if line.startswith("[engram]")]) == 1
    assert not out.err
    assert telemetry.is_enabled() is False
    assert telemetry.is_remote_enabled() is False
    assert telemetry.is_feedback_enabled() is False
    assert usage_ping.decision() == (True, "default")
