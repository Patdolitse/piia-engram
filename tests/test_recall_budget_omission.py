"""Budget omissions on every recall entry: machine-readable info + one text line.

When a token/char budget drops content, the entry reports
``{omitted_count, ids, sections, reason}`` (ids and section names only, never
the dropped text); text-form cold start and the session hooks end with one
line "已省略 N 项（预算）：sections". Only the Memory Lens preview shows the
trimmed items' summaries. Nothing is reported when everything fits.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_recall_eligibility_matrix import (  # noqa: F401  (env is a fixture)
    APPROVED,
    HOOKS,
    TOPIC,
    TRUSTED_TOKENS,
    _recall,
    _resume_brief,
    _user_context,
    env,
)

# ---------------------------------------------------------------------------
OMIT_PREFIX = "已省略 "


def _long_patterns(root: Path) -> None:
    prefs = {"work_patterns": {f"pattern{i}": "x" * 1400 for i in range(6)}}
    (root / "identity" / "preferences.json").write_text(json.dumps(prefs), encoding="utf-8")


def test_user_context_small_budget_ends_with_omission_line(env):
    m, eng, root, tmp_path = env
    text = eng.generate_context(max_tokens=40, level="standard")
    last = text.rstrip().splitlines()[-1]
    assert last.startswith(OMIT_PREFIX) and "（预算）" in last and "lessons" in last
    assert eng._estimate_tokens(text) <= 40
    omitted = eng.last_context_omitted
    assert omitted["reason"] == "budget"
    assert "l-approved" in omitted["ids"]
    assert APPROVED not in json.dumps(omitted, ensure_ascii=False)

    mcp_text = _user_context(m, eng, tmp_path, token_budget=40)
    assert mcp_text.rstrip().splitlines()[-1].startswith(OMIT_PREFIX)
    assert mcp_text.count(OMIT_PREFIX) == 1


def test_user_context_without_cut_has_no_omission(env):
    m, eng, root, tmp_path = env
    text = eng.generate_context(level="standard")
    assert OMIT_PREFIX not in text
    assert eng.last_context_omitted is None
    assert OMIT_PREFIX not in _user_context(m, eng, tmp_path, token_budget=100000)


def test_resume_brief_small_budget_reports_omitted(env):
    m, eng, root, tmp_path = env
    brief = json.loads(_resume_brief(m, eng, tmp_path, token_budget=100))
    omitted = brief["omitted"]
    assert omitted["reason"] == "budget" and omitted["omitted_count"] >= 1
    assert "lessons" in omitted["sections"]
    assert "l-approved" in omitted["ids"]
    blob = json.dumps(omitted, ensure_ascii=False)
    for token in TRUSTED_TOKENS:
        assert token not in blob
    md = brief["markdown"]
    assert OMIT_PREFIX in md
    assert md.index(OMIT_PREFIX) < md.index("</engram-resume>")
    # the line is paid for inside the brief's own char budget (the brand lead
    # line has always been outside it; the MCP layer adds the permissions note)
    core = eng.get_resume_brief(token_budget=100)["markdown"]
    brand = next(line for line in core.splitlines() if line.startswith("[Engram]"))
    assert OMIT_PREFIX in core
    assert len(core) - len(brand) - 2 <= max(400, 100 * 4)


def test_resume_brief_default_budget_has_no_omission(env):
    m, eng, root, tmp_path = env
    brief = json.loads(_resume_brief(m, eng, tmp_path))
    assert "omitted" not in brief
    assert OMIT_PREFIX not in brief["markdown"]


@pytest.mark.parametrize("entry", sorted(HOOKS))
def test_hooks_print_omission_line_when_budget_cuts(env, entry, monkeypatch, capsys):
    m, eng, root, tmp_path = env
    _long_patterns(root)
    text = HOOKS[entry](m, eng, tmp_path, monkeypatch=monkeypatch, capsys=capsys)
    assert OMIT_PREFIX in text
    line = next(ln for ln in text.splitlines() if ln.startswith(OMIT_PREFIX))
    assert "（预算）" in line


@pytest.mark.parametrize("entry", sorted(HOOKS))
def test_hooks_have_no_omission_line_when_everything_fits(env, entry, monkeypatch, capsys):
    m, eng, root, tmp_path = env
    text = HOOKS[entry](m, eng, tmp_path, monkeypatch=monkeypatch, capsys=capsys)
    assert OMIT_PREFIX not in text


def test_recall_small_budget_reports_omitted_ids_only(env):
    m, eng, root, tmp_path = env
    payload = json.loads(_recall(m, eng, tmp_path, token_budget=1))
    omitted = payload["meta"]["omitted"]
    assert omitted["reason"] == "budget" and omitted["omitted_count"] >= 1
    assert set(omitted["ids"]) <= {"l-approved", "l-new", "l-guarded", "l-secret"}
    assert omitted["sections"] == ["knowledge"]
    blob = json.dumps(omitted, ensure_ascii=False)
    for token in TRUSTED_TOKENS:
        assert token not in blob


def test_recall_without_cut_has_no_omitted(env):
    m, eng, root, tmp_path = env
    payload = json.loads(_recall(m, eng, tmp_path))
    assert "omitted" not in payload["meta"]


def test_memory_lens_shows_trimmed_summaries(env, monkeypatch):
    from piia_engram import context_preview as cp

    m, eng, root, tmp_path = env
    levels = {k: dict(v) for k, v in cp.LEVELS.items()}
    levels["quick"]["max_chars"] = 330
    monkeypatch.setattr(cp, "LEVELS", levels)
    preview = cp.build_context_preview(eng, level="quick", role="owner", query=TOPIC)
    knowledge = preview["knowledge"]
    assert knowledge["trimmed_by_budget"] >= 1
    assert len(knowledge["trimmed"]) == knowledge["trimmed_by_budget"]
    assert all(TOPIC in item["summary"] for item in knowledge["trimmed"])
    omitted = knowledge["omitted"]
    assert omitted["omitted_count"] == knowledge["trimmed_by_budget"]
    assert set(omitted["ids"]) <= {"l-approved", "l-new", "l-guarded", "l-secret"}
    text = cp.render_context_preview_text(preview)
    assert knowledge["trimmed"][0]["summary"] in text


def test_memory_lens_without_cut_has_no_trimmed(env):
    from piia_engram.context_preview import build_context_preview

    m, eng, root, tmp_path = env
    preview = build_context_preview(eng, role="owner", query=TOPIC)
    assert preview["knowledge"]["trimmed"] == []
    assert "omitted" not in preview["knowledge"]
