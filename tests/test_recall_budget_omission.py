"""Budget omissions on every recall entry: machine-readable info + one text line.

When a token/char budget drops content, the entry reports
``{omitted_count, ids, sections, reason}`` (ids and section names only, never
the dropped text); text-form cold start ends with one line
"已省略 N 项（预算）：sections", the resume brief and the session hooks (English
headings) with "Omitted N items (budget): sections". The line is paid for inside
the budget and left out when it cannot fit. Only the Memory Lens preview shows
the trimmed items' summaries. Nothing is reported when everything fits, and a
fixed item cap is not a budget omission.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_seed import raw_write_json
from piia_engram.core import Engram
from test_recall_eligibility_matrix import (  # noqa: F401  (env is a fixture)
    APPROVED,
    HOOKS,
    TOPIC,
    TRUSTED_IDS,
    TRUSTED_TOKENS,
    _playbook,
    _recall,
    _resume_brief,
    _user_context,
    _write_playbooks,
    env,
)

ZH_PREFIX = "已省略 "
EN_PREFIX = "Omitted "


def _long_patterns(root: Path) -> None:
    prefs = {"work_patterns": {f"pattern{i}": "x" * 1400 for i in range(6)}}
    (root / "identity" / "preferences.json").write_text(json.dumps(prefs), encoding="utf-8")


# --- cold start ---------------------------------------------------------------


def test_user_context_small_budget_ends_with_omission_line(env):
    m, eng, root, tmp_path = env
    text = eng.generate_context(max_tokens=40, level="standard")
    last = text.rstrip().splitlines()[-1]
    assert last.startswith(ZH_PREFIX) and "（预算）" in last and "lessons" in last
    assert eng._estimate_tokens(text) <= 40
    omitted = eng.last_context_omitted
    assert omitted["reason"] == "budget"
    assert "l-approved" in omitted["ids"]
    assert APPROVED not in json.dumps(omitted, ensure_ascii=False)
    assert eng.generate_context_report(max_tokens=40, level="standard") == (text, omitted)

    mcp_text = _user_context(m, eng, tmp_path, token_budget=40)
    assert mcp_text.rstrip().splitlines()[-1].startswith(ZH_PREFIX)
    assert mcp_text.count(ZH_PREFIX) == 1


def test_user_context_without_cut_has_no_omission(env):
    m, eng, root, tmp_path = env
    text = eng.generate_context(level="standard")
    assert ZH_PREFIX not in text
    assert eng.last_context_omitted is None
    assert ZH_PREFIX not in _user_context(m, eng, tmp_path, token_budget=100000)


@pytest.mark.parametrize("budget", [0, 1, 5, 40])
def test_generate_context_never_exceeds_tiny_budgets(env, budget, monkeypatch):
    m, eng, root, tmp_path = env
    calls = {"n": 0}
    real = Engram._estimate_tokens

    def counting(text):
        calls["n"] += 1
        return real(text)

    monkeypatch.setattr(eng, "_estimate_tokens", counting)
    text, omitted = eng.generate_context_report(max_tokens=budget, level="full")
    assert real(text) <= budget
    assert omitted is not None and omitted["omitted_count"] >= 1
    # bounded: each fit round costs one estimate per section plus two checks
    assert calls["n"] <= (Engram._OMISSION_FIT_MAX_ROUNDS + 1) * 20


def test_generate_context_round_cap_still_honours_the_budget(env, monkeypatch):
    m, eng, root, tmp_path = env
    monkeypatch.setattr(Engram, "_OMISSION_FIT_MAX_ROUNDS", 1)
    text, omitted = eng.generate_context_report(max_tokens=40, level="full")
    assert eng._estimate_tokens(text) <= 40
    assert omitted is not None


def test_mcp_user_context_drops_the_line_when_it_cannot_fit(env, monkeypatch):
    m, eng, root, tmp_path = env
    long_prompt = "x" * 600  # the appended question fills the budget
    text = _user_context(m, eng, tmp_path, token_budget=40, user_prompt=long_prompt)
    assert ZH_PREFIX not in text


# --- resume brief and hooks -------------------------------------------------------


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
    assert EN_PREFIX in md and "(budget)" in md
    assert md.index(EN_PREFIX) < md.index("</engram-resume>")
    # the line is paid for inside the brief's own char budget (the brand lead
    # line has always been outside it; the MCP layer adds the permissions note)
    core = eng.get_resume_brief(token_budget=100)["markdown"]
    brand = next(line for line in core.splitlines() if line.startswith("[Engram]"))
    assert EN_PREFIX in core
    assert len(core) - len(brand) - 2 <= max(400, 100 * 4)


def test_resume_brief_leaves_out_a_line_longer_than_the_budget(env, monkeypatch):
    from piia_engram import recall_policy

    m, eng, root, tmp_path = env
    monkeypatch.setattr(recall_policy, "omission_line", lambda omitted, lang="zh": "y" * 1000)
    brief = eng.get_resume_brief(token_budget=100)
    assert "y" * 1000 not in brief["markdown"]
    assert brief["omitted"]["reason"] == "budget"  # still reported in data
    brand = next(line for line in brief["markdown"].splitlines() if line.startswith("[Engram]"))
    assert len(brief["markdown"]) - len(brand) - 2 <= 400


def test_resume_brief_default_budget_has_no_omission(env):
    m, eng, root, tmp_path = env
    brief = json.loads(_resume_brief(m, eng, tmp_path))
    assert "omitted" not in brief
    assert EN_PREFIX not in brief["markdown"]


@pytest.mark.parametrize("entry", sorted(HOOKS))
def test_hooks_print_omission_line_when_budget_cuts(env, entry, monkeypatch, capsys):
    m, eng, root, tmp_path = env
    _long_patterns(root)
    text = HOOKS[entry](m, eng, tmp_path, monkeypatch=monkeypatch, capsys=capsys)
    line = next(ln for ln in text.splitlines() if ln.startswith(EN_PREFIX))
    assert "(budget)" in line


@pytest.mark.parametrize("entry", sorted(HOOKS))
def test_hooks_have_no_omission_line_when_everything_fits(env, entry, monkeypatch, capsys):
    m, eng, root, tmp_path = env
    text = HOOKS[entry](m, eng, tmp_path, monkeypatch=monkeypatch, capsys=capsys)
    assert EN_PREFIX not in text and ZH_PREFIX not in text


# --- get_recall ------------------------------------------------------------------


def test_recall_small_budget_reports_omitted_ids_only(env):
    m, eng, root, tmp_path = env
    payload = json.loads(_recall(m, eng, tmp_path, token_budget=1, include_playbooks=False))
    omitted = payload["meta"]["omitted"]
    assert omitted["reason"] == "budget" and omitted["omitted_count"] >= 1
    assert set(omitted["ids"]) <= TRUSTED_IDS
    assert omitted["sections"] == ["knowledge"]
    blob = json.dumps(omitted, ensure_ascii=False)
    for token in TRUSTED_TOKENS:
        assert token not in blob


def test_recall_without_cut_has_no_omitted(env):
    m, eng, root, tmp_path = env
    payload = json.loads(_recall(m, eng, tmp_path))
    assert "omitted" not in payload["meta"]


def test_recall_playbook_item_cap_is_not_a_budget_omission(tmp_path, monkeypatch):
    root = tmp_path / "engram"
    (root / "knowledge").mkdir(parents=True)
    raw_write_json(root / "knowledge" / "lessons.json", [])
    raw_write_json(root / "knowledge" / "decisions.json", [])
    _write_playbooks(root, [
        _playbook(f"pb-{i}", f"zqpCap{i}x", f"2026-10-0{i + 1}T00:00:00") for i in range(3)
    ])
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    import piia_engram.mcp_server as m

    monkeypatch.setattr(m, "_engram", Engram(root))
    payload = json.loads(_recall(m, None, tmp_path, token_budget=200000))
    usage = payload["meta"]["context_usage"]["playbooks"]
    assert usage["returned"] == 2 and usage["trimmed"] == 1  # the fixed cap of two
    assert "omitted" not in payload["meta"]


# --- Memory Lens --------------------------------------------------------------------


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
    assert all(any(t in item["summary"] for t in TRUSTED_TOKENS) for item in knowledge["trimmed"])
    omitted = knowledge["omitted"]
    assert omitted["omitted_count"] == knowledge["trimmed_by_budget"]
    assert set(omitted["ids"]) <= TRUSTED_IDS
    text = cp.render_context_preview_text(preview)
    assert knowledge["trimmed"][0]["summary"] in text


def test_memory_lens_without_cut_has_no_trimmed(env):
    from piia_engram.context_preview import build_context_preview

    m, eng, root, tmp_path = env
    preview = build_context_preview(eng, role="owner", level="full", query=TOPIC)
    assert preview["knowledge"]["trimmed"] == []
    assert "omitted" not in preview["knowledge"]
