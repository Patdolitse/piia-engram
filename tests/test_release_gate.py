"""Tests for scripts/check_release_gate.py.

The release gate is a deterministic enforcement: publishing must fail unless
a complete evidence file records that the mandatory gates passed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_release_gate.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_release_gate", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rg():
    return _load()


def _write_evidence(root: Path, version: str, body: str) -> None:
    d = root / "release-evidence"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"v{version}.md").write_text(body, encoding="utf-8")


def test_missing_evidence_blocks(rg, tmp_path):
    ok, problems = rg.check_release_gate("9.9.9", tmp_path)
    assert ok is False
    assert any("missing evidence file" in p for p in problems)


def test_complete_evidence_passes(rg, tmp_path):
    _write_evidence(tmp_path, "9.9.9",
                    "# Release evidence — v9.9.9\n\n"
                    "- self-review: passed\n"
                    "- codex-review: passed\n"
                    "- tests: pass\n"
                    "- eval-gate: pass\n")
    ok, problems = rg.check_release_gate("9.9.9", tmp_path)
    assert ok is True, problems
    assert problems == []


def test_eval_gate_na_is_accepted(rg, tmp_path):
    _write_evidence(tmp_path, "9.9.9",
                    "- self-review: passed\n"
                    "- codex-review: passed\n"
                    "- tests: pass\n"
                    "- eval-gate: n/a\n")
    ok, _ = rg.check_release_gate("9.9.9", tmp_path)
    assert ok is True


def test_missing_codex_review_blocks(rg, tmp_path):
    """The whole point: self-review alone is NOT enough — codex-review required."""
    _write_evidence(tmp_path, "9.9.9",
                    "- self-review: passed\n"
                    "- tests: pass\n"
                    "- eval-gate: n/a\n")
    ok, problems = rg.check_release_gate("9.9.9", tmp_path)
    assert ok is False
    assert any("codex-review" in p for p in problems)


def test_non_passing_marker_blocks(rg, tmp_path):
    _write_evidence(tmp_path, "9.9.9",
                    "- self-review: passed\n"
                    "- codex-review: pending\n"
                    "- tests: pass\n"
                    "- eval-gate: n/a\n")
    ok, problems = rg.check_release_gate("9.9.9", tmp_path)
    assert ok is False
    assert any("codex-review" in p and "pending" in p for p in problems)


def test_missing_eval_gate_marker_blocks(rg, tmp_path):
    _write_evidence(tmp_path, "9.9.9",
                    "- self-review: passed\n"
                    "- codex-review: passed\n"
                    "- tests: pass\n")
    ok, problems = rg.check_release_gate("9.9.9", tmp_path)
    assert ok is False
    assert any("eval-gate" in p for p in problems)


def test_inline_comments_after_values_are_ignored(rg, tmp_path):
    """The README template uses 'passed  # note' style — comments must not
    break the passing-value check."""
    _write_evidence(tmp_path, "9.9.9",
                    "# Release evidence — v9.9.9\n\n"
                    "- self-review: passed     # diff reviewed\n"
                    "- codex-review: passed    # independent external review\n"
                    "- tests: pass             # 1006 green\n"
                    "- eval-gate: n/a          # no retrieval change\n")
    ok, problems = rg.check_release_gate("9.9.9", tmp_path)
    assert ok is True, problems


def test_version_specific_evidence(rg, tmp_path):
    """Evidence for a different version must not satisfy the current one."""
    _write_evidence(tmp_path, "1.0.0",
                    "- self-review: passed\n- codex-review: passed\n"
                    "- tests: pass\n- eval-gate: n/a\n")
    ok, _ = rg.check_release_gate("2.0.0", tmp_path)
    assert ok is False


def test_real_pyproject_version_resolves(rg):
    """_pyproject_version reads the actual repo version without error."""
    root = Path(__file__).resolve().parent.parent
    v = rg._pyproject_version(root)
    assert v and v[0].isdigit()
