"""Tests for the offline resume-pack evaluation harness."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval_resume_pack.py"

spec = importlib.util.spec_from_file_location("eval_resume_pack", SCRIPT)
eval_resume_pack = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(eval_resume_pack)


def test_eval_resume_pack_script_json_passes():
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("ENGRAM_TEST", None)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "DATA FRAGMENTATION" not in result.stderr
    assert "knowledge may be incomplete" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema"] == "resume_pack_eval.v1"
    assert payload["overall_passed"] is True
    assert {case["name"] for case in payload["cases"]} >= {
        "next_action_from_digest",
        "session_candidate_requires_review",
        "verified_context_is_trusted",
    }


def test_resume_pack_eval_schema_fields_are_stable():
    payload = eval_resume_pack.run_eval()

    assert sorted(payload) == ["cases", "overall_passed", "schema"]
    assert payload["schema"] == "resume_pack_eval.v1"
    for case in payload["cases"]:
        assert sorted(case) == ["checks", "name", "passed"]
        assert sorted(case["checks"]) == [
            "focus",
            "no_forbidden_substrings",
            "review",
            "trusted",
        ]


def test_forbidden_substring_helper_can_fail_case():
    fake_secret = "sk-" + "x" * 32
    pack = {
        "schema": "project_resume_pack.v1",
        "handoff": {"current_focus": "continue"},
        "trusted_context": [{"summary": fake_secret}],
        "review_needed": [],
    }
    expected = {
        "current_focus_contains": "continue",
        "trusted_context_contains": [],
        "review_needed_contains": [],
        "forbidden_substrings": [fake_secret],
    }

    result = eval_resume_pack.evaluate_pack("synthetic_leak", pack, expected)

    assert result["checks"]["no_forbidden_substrings"] is False
    assert result["passed"] is False


def test_forbidden_substring_helper_checks_raw_fields_without_schema_key_false_positive():
    pack = {
        "schema": "project_resume_pack.v1",
        "handoff": {"current_focus": "continue"},
        "trusted_context": [{"summary": "safe remembered context"}],
        "review_needed": [],
    }
    expected = {
        "current_focus_contains": "continue",
        "trusted_context_contains": [],
        "review_needed_contains": [],
        "forbidden_substrings": ["content"],
    }

    result = eval_resume_pack.evaluate_pack("schema_key_ok", pack, expected)

    assert result["checks"]["no_forbidden_substrings"] is True
    assert result["passed"] is True

    leaked = {**pack, "content": "raw body must not be exposed"}
    leaked_result = eval_resume_pack.evaluate_pack("raw_field_leak", leaked, expected)

    assert leaked_result["checks"]["no_forbidden_substrings"] is False
    assert leaked_result["passed"] is False


def test_eval_uses_isolated_temp_store_and_not_live_engram_dir(tmp_path, monkeypatch):
    live_store = tmp_path / "live-engram-store"
    live_store.mkdir()
    sentinel = live_store / "sentinel.txt"
    sentinel.write_text("LIVE_STORE_SENTINEL_SHOULD_NOT_APPEAR", encoding="utf-8")
    before_files = sorted(path.relative_to(live_store).as_posix() for path in live_store.rglob("*"))
    monkeypatch.setenv("ENGRAM_DIR", str(live_store))
    fixture = tmp_path / "cases.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "name": "isolated",
                    "project_snapshot": {"title": "Isolated Project", "stage": "M3"},
                    "sessions": [
                        {
                            "tool": "codex",
                            "content": "Goal: isolate.\nNext: keep temp store isolated.\n",
                        }
                    ],
                    "expected": {
                        "current_focus_contains": "keep temp store isolated",
                        "trusted_context_contains": ["Isolated Project"],
                        "review_needed_contains": [],
                        "forbidden_substrings": ["raw_session", "content"],
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = eval_resume_pack.run_eval(fixture_path=fixture)
    after_files = sorted(path.relative_to(live_store).as_posix() for path in live_store.rglob("*"))
    payload_blob = json.dumps(payload, ensure_ascii=False)

    assert payload["overall_passed"] is True
    assert before_files == after_files == ["sentinel.txt"]
    assert sentinel.read_text(encoding="utf-8") == "LIVE_STORE_SENTINEL_SHOULD_NOT_APPEAR"
    assert "LIVE_STORE_SENTINEL_SHOULD_NOT_APPEAR" not in payload_blob
