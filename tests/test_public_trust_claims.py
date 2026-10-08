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
_DEFAULT_ENDPOINT = "https://telemetry.example.test/v1/events"
_DEFAULT_FEEDBACK_ENDPOINT = "https://telemetry.example.test/v1/feedback"
''',
    )
    _write(
        root,
        "README.md",
        "Engram sends one anonymous usage ping a day (off with DO_NOT_TRACK=1); identity and knowledge tools make no network calls; "
        "remote telemetry and feedback require separate explicit opt-in. "
        "All data lives in local plain JSON files by default. "
        "Local access audit log on by default at ~/.engram/audit.log; opt out with ENGRAM_AUDIT=0.\n",
    )
    _write(
        root,
        "README.zh-CN.md",
        "Engram 每天发送一次匿名使用信号（DO_NOT_TRACK=1 可关闭）；远程 telemetry 和每周反馈报告必须单独显式开启。"
        "默认以本地明文 JSON 文件存储。本地访问审计默认开启。\n",
    )
    _write(
        root,
        "SECURITY.md",
        "Engram sends one anonymous usage ping a day. Remote telemetry is a separate opt-in. "
        "https://telemetry.example.test/v1/events "
        "https://telemetry.example.test/v1/feedback "
        "Never collected: identity content, prompts, file paths. "
        "Optional web reads only fetch URLs you explicitly provide. "
        "Optional field-level encryption requires piia-engram[secure] and ENGRAM_SECRET. "
        "Audit logging (on by default): operations recorded to ~/.engram/audit.log, "
        "a plain JSON-lines file; opt out with ENGRAM_AUDIT=0.\n",
    )
    _write(
        root,
        "PRIVACY.md",
        "Your identity, preferences, lessons, and decisions are stored as plain JSON files. "
        "Engram sends one anonymous usage ping a day. Remote telemetry and weekly feedback reports are separate opt-ins. "
        "Without ENGRAM_SECRET, piia-engram works normally with plaintext. "
        "Local audit logging is on by default; opt out with ENGRAM_AUDIT=0.\n",
    )
    _write(
        root,
        "docs/telemetry-privacy.md",
        "Engram sends one anonymous usage ping a day with a random install ID. Remote sending is a separate opt-in. "
        "No lesson / decision / playbook content is collected.\n",
    )
    _write(
        root,
        "docs/trust.md",
        "The files are plain JSON or Markdown unless you explicitly enable optional field-level encryption. "
        "Remote telemetry and weekly feedback reports require separate explicit opt-in. "
        "Local audit logging is on by default; opt out with ENGRAM_AUDIT=0.\n",
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
        "Engram sends one anonymous usage ping a day (off with DO_NOT_TRACK=1); identity and knowledge tools make no network calls; "
        "remote telemetry and feedback require separate explicit opt-in. "
        "All data lives in local plain JSON files by default. "
        "Remote telemetry is never enabled by default. "
        "Not all data is encrypted at rest; only supported fields are encrypted when configured. "
        "Local access audit log on by default at ~/.engram/audit.log; opt out with ENGRAM_AUDIT=0.\n",
    )

    result = guard.scan(tmp_path)

    assert result["ok"] is True, result["problems"]


def test_missing_plaintext_default_disclosure_fails(guard, tmp_path: Path):
    _write_minimal_trust_surface(tmp_path)
    _write(tmp_path, "PRIVACY.md", "Engram sends one anonymous usage ping a day. Remote telemetry is opt-in.\n")

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
        "Engram sends one anonymous usage ping a day. Remote telemetry is a separate opt-in. "
        "https://example.invalid/v1/events "
        "https://telemetry.example.test/v1/feedback "
        "Never collected: identity content, prompts, file paths. "
        "Optional web reads only fetch URLs you explicitly provide. "
        "Optional field-level encryption requires piia-engram[secure] and ENGRAM_SECRET.\n",
    )

    result = guard.scan(tmp_path)

    assert result["ok"] is False
    assert any(p["kind"] == "endpoint_drift" and p["endpoint"] == "telemetry" for p in result["problems"])


def test_empty_default_endpoints_produce_no_drift(guard, tmp_path: Path):
    """No built-in telemetry endpoint (empty defaults) => nothing to drift-check.

    The open-source core ships with empty `_DEFAULT_ENDPOINT` /
    `_DEFAULT_FEEDBACK_ENDPOINT` (operators opt in via env vars). The guard must
    parse the empty constants without error and must NOT demand that SECURITY.md
    document any concrete URL.
    """
    _write_minimal_trust_surface(tmp_path)
    _write(
        tmp_path,
        "src/piia_engram/telemetry.py",
        '''
_DEFAULT_ENDPOINT = ""
_DEFAULT_FEEDBACK_ENDPOINT = ""
''',
    )
    # SECURITY.md here documents no URL at all — must still pass.
    _write(
        tmp_path,
        "SECURITY.md",
        "Engram sends one anonymous usage ping a day. Remote telemetry is a separate opt-in "
        "configured via ENGRAM_TELEMETRY_URL; the core ships with no built-in endpoint. "
        "Never collected: identity content, prompts, file paths. "
        "Optional web reads only fetch URLs you explicitly provide. "
        "Optional field-level encryption requires piia-engram[secure] and ENGRAM_SECRET. "
        "Audit logging (on by default): operations recorded to ~/.engram/audit.log, "
        "a plain JSON-lines file; opt out with ENGRAM_AUDIT=0.\n",
    )

    result = guard.scan(tmp_path)

    assert result["ok"] is True, result["problems"]
    assert not any(p["kind"] == "endpoint_drift" for p in result["problems"])


def test_stale_off_by_default_wording_fails(guard, tmp_path: Path):
    _write_minimal_trust_surface(tmp_path)
    _write(tmp_path, "PRIVACY.md",
           "stored as plain JSON files. Engram sends one anonymous usage ping a day. "
           "Telemetry is off by default. Remote telemetry and weekly feedback reports are separate opt-ins. "
           "Without ENGRAM_SECRET, piia-engram works normally with plaintext. "
           "Local audit logging is on by default.\n")
    result = guard.scan(tmp_path)
    assert any(p["claim"] == "stale_telemetry_off_default" for p in result["problems"])


@pytest.mark.parametrize(
    "text",
    [
        "Detailed telemetry is off by default.",
        "Remote telemetry is off by default and a separate opt-in.",
        "Funnel telemetry is off by default.",
        "Detailed usage statistics stay off unless you turn them on.",
        "The first-value funnel is also off by default.",
    ],
)
def test_qualified_current_wording_passes_on_forbidden_only_surface(guard, tmp_path: Path, text: str):
    _write_minimal_trust_surface(tmp_path)
    _write(tmp_path, "docs/comparison.md", text + "\n")

    result = guard.scan(tmp_path)

    assert "docs/comparison.md" in result["scanned"]
    assert not [p for p in result["problems"] if p["kind"] == "forbidden_claim"], result["problems"]


@pytest.mark.parametrize(
    "text",
    [
        "Telemetry is **off** by default.",
        "Telemetry is **off**.",
        "Usage statistics are off by default.",
        "telemetry is opt-in only",
        "| Network calls by default | **0** for identity |",
        "Nothing leaves the machine.",
        "遥测**默认关闭**",
        "telemetry **默认关闭**",
    ],
)
def test_stale_wording_fails_on_forbidden_only_surface(guard, tmp_path: Path, text: str):
    _write_minimal_trust_surface(tmp_path)
    _write(tmp_path, "docs/comparison.md", text + "\n")

    result = guard.scan(tmp_path)

    assert result["ok"] is False
    assert any(
        p["kind"] == "forbidden_claim" and p["file"] == "docs/comparison.md"
        for p in result["problems"]
    ), result["problems"]


def test_missing_forbidden_only_surface_is_skipped(guard, tmp_path: Path):
    _write_minimal_trust_surface(tmp_path)

    result = guard.scan(tmp_path)

    assert result["ok"] is True, result["problems"]
    assert "docs/comparison.md" not in result["scanned"]
