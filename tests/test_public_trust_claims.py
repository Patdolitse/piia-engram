"""Tests for scripts/check_public_trust_claims.py.

The trust-claim guard polices prose claims that public-fact numeric checks do
not understand: telemetry/network boundaries, plaintext-at-rest disclosure, and
endpoint consistency.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = ROOT / "scripts" / "check_public_trust_claims.py"


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("check_public_trust_claims", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_minimal_trust_surface(root: Path) -> None:
    _write(
        root,
        "src/piia_engram/telemetry.py",
        '''
_DEFAULT_ENDPOINT = "https://engram-telemetry.pp3x325.workers.dev/v1/events"
_DEFAULT_FEEDBACK_ENDPOINT = "https://engram-telemetry.pp3x325.workers.dev/v1/feedback"
''',
    )
    _write(
        root,
        "README.md",
        "Network calls by default: 0 for identity and knowledge tools; "
        "remote telemetry and feedback require separate explicit opt-in. "
        "All data lives in local plain JSON files by default.\n",
    )
    _write(
        root,
        "README.zh-CN.md",
        "身份与知识工具默认 0 次网络请求；远程 telemetry 和每周反馈报告必须单独显式开启。"
        "默认以本地明文 JSON 文件存储。\n",
    )
    _write(
        root,
        "SECURITY.md",
        "Telemetry is off by default. Remote telemetry is a separate opt-in. "
        "https://engram-telemetry.pp3x325.workers.dev/v1/events "
        "https://engram-telemetry.pp3x325.workers.dev/v1/feedback "
        "Never collected: identity content, prompts, file paths. "
        "Optional web reads only fetch URLs you explicitly provide. "
        "Optional field-level encryption requires piia-engram[secure] and ENGRAM_SECRET.\n",
    )
    _write(
        root,
        "PRIVACY.md",
        "Your identity, preferences, lessons, and decisions are stored as plain JSON files. "
        "Telemetry is off by default. Remote telemetry and weekly feedback reports are separate opt-ins. "
        "Without ENGRAM_SECRET, piia-engram works normally with plaintext.\n",
    )
    _write(
        root,
        "docs/telemetry-privacy.md",
        "Telemetry is opt-in and off by default. Remote sending is a separate opt-in. "
        "No lesson / decision / playbook content is collected. No stable cross-day user ID.\n",
    )
    _write(
        root,
        "docs/trust.md",
        "The files are plain JSON or Markdown unless you explicitly enable optional field-level encryption. "
        "Remote telemetry and weekly feedback reports require separate explicit opt-in.\n",
    )


def test_current_repo_public_trust_claims_pass(guard):
    result = guard.scan(ROOT)
    assert result["ok"] is True, result["problems"]


def test_absolute_no_network_overclaim_fails(guard, tmp_path: Path):
    _write_minimal_trust_surface(tmp_path)
    _write(
        tmp_path,
        "README.md",
        "Engram makes no network requests. Remote telemetry and feedback require separate explicit opt-in. "
        "All data lives in local plain JSON files by default.\n",
    )

    result = guard.scan(tmp_path)

    assert result["ok"] is False
    assert any(
        p["kind"] == "forbidden_claim" and "no network" in p["match"].lower()
        for p in result["problems"]
    )


def test_negated_default_and_encryption_clarifications_do_not_false_positive(guard, tmp_path: Path):
    _write_minimal_trust_surface(tmp_path)
    _write(
        tmp_path,
        "README.md",
        "Network calls by default: 0 for identity and knowledge tools; "
        "remote telemetry and feedback require separate explicit opt-in. "
        "All data lives in local plain JSON files by default. "
        "Remote telemetry is never enabled by default. "
        "Not all data is encrypted at rest; only supported fields are encrypted when configured.\n",
    )

    result = guard.scan(tmp_path)

    assert result["ok"] is True, result["problems"]


def test_missing_plaintext_default_disclosure_fails(guard, tmp_path: Path):
    _write_minimal_trust_surface(tmp_path)
    _write(tmp_path, "PRIVACY.md", "Telemetry is off by default. Remote telemetry is opt-in.\n")

    result = guard.scan(tmp_path)

    assert result["ok"] is False
    assert any(
        p["kind"] == "missing_required_claim"
        and p["file"] == "PRIVACY.md"
        and p["claim"] == "plaintext_default"
        for p in result["problems"]
    )


def test_endpoint_drift_fails_against_telemetry_source(guard, tmp_path: Path):
    _write_minimal_trust_surface(tmp_path)
    _write(
        tmp_path,
        "SECURITY.md",
        "Telemetry is off by default. Remote telemetry is a separate opt-in. "
        "https://example.invalid/v1/events "
        "https://engram-telemetry.pp3x325.workers.dev/v1/feedback "
        "Never collected: identity content, prompts, file paths. "
        "Optional web reads only fetch URLs you explicitly provide. "
        "Optional field-level encryption requires piia-engram[secure] and ENGRAM_SECRET.\n",
    )

    result = guard.scan(tmp_path)

    assert result["ok"] is False
    assert any(p["kind"] == "endpoint_drift" and p["endpoint"] == "telemetry" for p in result["problems"])
