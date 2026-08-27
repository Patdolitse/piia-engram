"""Attack-corpus regression for the session-end content digest (PR-1).

Parametrized from the symbolic corpus via scripts/eval_hook_digest.py.
These tests are the acceptance target for PR-2 (digest hardening): until the
three known bypass gaps are fixed, the crossline/homoglyph/window cases are
EXPECTED to fail — they are landed first per the corpus-first plan, with the
current expected-failure markers REMOVED by PR-2 when it turns them green.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "eval_hook_digest", _ROOT / "scripts" / "eval_hook_digest.py"
)
_eval = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("eval_hook_digest", _eval)
_spec.loader.exec_module(_eval)

# cases known-red until PR-2 lands the hardening (corpus-first sequencing)
# cases measured RED on the current (pre-PR2) code, 2026-08-27:
# 12 of 20 fail exactly on the known hardening gaps (verified per-case before
# landing); CORP-011 and CORP-014 already pass via the shape-scan drop path.
_PENDING_PR2: set[str] = set()  # PR-2 landed; all cases green

_cases = _eval.expanded_cases(_eval.load_corpus())


@pytest.mark.parametrize(
    "case", _cases, ids=[c["id"] for c in _cases]
)
def test_attack_corpus_case(case):
    result = _eval.evaluate_case(case)
    if result["id"] in _PENDING_PR2:
        pytest.xfail(
            f"corpus-first sequencing: case {result['id']} targets a PR-2 "
            "hardening gap and is expected red until PR-2 lands"
        )
    assert result["passed"], result["problems"]


def test_corpus_schema_and_coverage():
    corpus = _eval.load_corpus()
    assert corpus["schema"] == "engram.hook_digest_attack_corpus.v1"
    ids = [c["id"] for c in corpus["cases"]]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    layers = {c.get("layer", "") for c in corpus["cases"]}
    assert {"digest", "guard_window", "e2e"} <= layers
    oracles = {c.get("oracle", "") for c in corpus["cases"]}
    assert oracles == {"drop", "preserve"}


def test_corpus_file_has_no_literal_credentials():
    """The tracked corpus stores symbolic placeholders only; the release
    sanitizer must keep scanning it (no exemptions)."""
    raw = (_ROOT / "tests" / "fixtures" / "hook_digest_attack_corpus_v1.json").read_text(
        encoding="utf-8"
    )
    assert "sk-FAKE" not in raw, "literal credential leaked into tracked corpus"
    assert "${FAKE_KEY_A}" in raw, "symbolic placeholder expected"
