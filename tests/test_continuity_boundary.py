from __future__ import annotations

import pytest

from piia_engram.continuity_contract import (
    default_mcic_scenarios,
    validate_no_real_paths,
)
from piia_engram.continuity_harness import simulate_continuity_cycle


def test_default_mcic_scenarios_pass_public_safety_scan():
    assert validate_no_real_paths(default_mcic_scenarios()) == []


def test_public_safety_scan_flags_real_windows_path():
    scenarios = default_mcic_scenarios()
    scenarios[0] = dict(scenarios[0])
    scenarios[0]["client_b_prompt"] = "Read C:\\Users\\realuser\\secrets.txt"

    violations = validate_no_real_paths(scenarios)

    assert violations
    assert "ER-01.client_b_prompt" in violations[0]


def test_public_safety_scan_flags_email():
    scenarios = default_mcic_scenarios()
    scenarios[0] = dict(scenarios[0])
    scenarios[0]["expected_signal"] = "Contact owner@example.com"

    violations = validate_no_real_paths(scenarios)

    assert violations
    assert "ER-01.expected_signal" in violations[0]


def test_harness_rejects_unsafe_scenarios_before_cycle_runs():
    scenarios = default_mcic_scenarios()
    scenarios[0] = dict(scenarios[0])
    scenarios[0]["failure_mode"] = "Leaks /home/alice/.config"

    with pytest.raises(ValueError, match="unsafe continuity scenario"):
        simulate_continuity_cycle(scenarios=scenarios)
