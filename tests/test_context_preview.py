"""Tests for the owner-facing context preview (``engram preview``).

Uses a duck-typed fake Engram (same convention as test_recall_service) so the
preview core is tested without a real store. Asserts the three-panel contract:
identity exposed + withheld field names, knowledge exposed/withheld split by
simulated caller ceiling, and governance/redaction/budget metadata. No version
numbers or other mutable release facts are hardcoded.
"""

from __future__ import annotations

import json

import pytest

from piia_engram import i18n
from piia_engram.context_preview import (
    DEFAULT_LEVEL,
    DEFAULT_ROLE,
    LEVELS,
    ROLE_TRUST_ANCHORS,
    build_context_preview,
    render_context_preview_html,
    render_context_preview_text,
    write_context_preview_html,
)


class FakeEngram:
    def __init__(self, *, profile=None, recent=None, relevant=None, search=None):
        self._profile = profile or {}
        self._recent = recent or []
        self._relevant = relevant or []
        self._search = search or {}

    def get_profile(self):
        return dict(self._profile)

    def get_recent_context(self, limit=1):
        return self._recent[:limit]

    def get_relevant_lessons(self, project_folder=None, limit=8, _update_access=True):
        return list(self._relevant)[:limit]

    def search_knowledge(self, query, scope="all", limit=8):
        return self._search


def _lesson(summary, *, sensitivity=None, tier="verified", item_id=None):
    item = {"type": "lesson", "summary": summary, "tier": tier}
    if sensitivity is not None:
        item["sensitivity"] = sensitivity
    if item_id is not None:
        item["id"] = item_id
    return item


def _mixed_engram():
    return FakeEngram(
        profile={"role": "builder", "language": "zh"},
        relevant=[
            _lesson("public fact", sensitivity="public", item_id="l-pub"),
            _lesson("work note", sensitivity="work", item_id="l-work"),
            _lesson("secret recipe", sensitivity="secret", item_id="l-sec"),
        ],
    )


# ---------------------------------------------------------------------------
# Knowledge split by simulated ceiling
# ---------------------------------------------------------------------------


def test_assistant_withholds_above_work_ceiling():
    preview = build_context_preview(_mixed_engram(), role="assistant")
    knowledge = preview["knowledge"]
    exposed = {item["summary"] for item in knowledge["exposed"]}
    assert "public fact" in exposed
    assert "work note" in exposed
    assert knowledge["withheld_count"] == 1
    withheld = knowledge["withheld"][0]
    assert withheld["sensitivity"] == "secret"
    assert withheld["withheld_reason"] == "sensitivity_above_ceiling"


def test_owner_sees_everything():
    preview = build_context_preview(_mixed_engram(), role="owner")
    assert preview["knowledge"]["withheld_count"] == 0
    assert preview["knowledge"]["exposed_count"] == 3
    assert preview["caller"]["effective_ceiling"] == "secret"


def test_automation_gets_public_only():
    preview = build_context_preview(_mixed_engram(), role="automation")
    exposed = {item["summary"] for item in preview["knowledge"]["exposed"]}
    assert exposed == {"public fact"}
    reasons = {
        item["withheld_reason"] for item in preview["knowledge"]["withheld"]
    }
    assert reasons == {"sensitivity_above_ceiling"}


def test_default_sensitivity_counts_as_work():
    eng = FakeEngram(relevant=[_lesson("untagged note", item_id="l-un")])
    assistant = build_context_preview(eng, role="assistant")
    assert assistant["knowledge"]["exposed_count"] == 1
    automation = build_context_preview(eng, role="automation")
    assert automation["knowledge"]["withheld_count"] == 1


def test_staging_excluded_for_non_owner_but_not_owner():
    eng = FakeEngram(
        relevant=[
            _lesson("staged idea", sensitivity="work", tier="staging", item_id="l-st"),
        ]
    )
    assistant = build_context_preview(eng, role="assistant")
    assert assistant["knowledge"]["withheld_count"] == 1
    assert assistant["knowledge"]["withheld"][0]["withheld_reason"] == "staging_excluded"
    owner = build_context_preview(eng, role="owner")
    assert owner["knowledge"]["withheld_count"] == 0


def test_query_knowledge_is_merged_in():
    eng = FakeEngram(
        relevant=[_lesson("project note", sensitivity="work", item_id="l-1")],
        search={
            "lessons": [_lesson("query hit", sensitivity="work", item_id="l-2")],
            "decisions": [],
        },
    )
    preview = build_context_preview(eng, role="assistant", query="hit")
    exposed = {item["summary"] for item in preview["knowledge"]["exposed"]}
    assert exposed == {"project note", "query hit"}


def test_decision_digest_uses_choice():
    eng = FakeEngram(
        relevant=[
            {
                "type": "decision",
                "question": "which path?",
                "choice": "the safe one",
                "tier": "verified",
                "sensitivity": "work",
                "id": "d-1",
            }
        ]
    )
    preview = build_context_preview(eng, role="assistant")
    item = preview["knowledge"]["exposed"][0]
    assert item["type"] == "decision"
    assert item["summary"] == "the safe one"


# ---------------------------------------------------------------------------
# Identity panel
# ---------------------------------------------------------------------------


def test_identity_withheld_fields_are_names_only():
    eng = FakeEngram(
        profile={
            "role": "builder",
            "language": "zh",
            "private_notes": "do-not-show-this-value",
        }
    )
    preview = build_context_preview(eng, role="assistant")
    assert preview["identity"]["exposed"].get("role") == "builder"
    assert "private_notes" in preview["identity"]["withheld_fields"]
    assert "do-not-show-this-value" not in json.dumps(preview, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Redaction + budget
# ---------------------------------------------------------------------------


def test_exposed_summary_is_redacted_and_counted():
    eng = FakeEngram(
        relevant=[
            _lesson("contact owner@example.com for access",
                    sensitivity="work", item_id="l-r"),
        ]
    )
    preview = build_context_preview(eng, role="assistant")
    blob = json.dumps(preview, ensure_ascii=False)
    assert "owner@example.com" not in blob
    assert preview["redaction"]["hits"] >= 1
    assert preview["redaction"]["placeholder"] in (
        preview["knowledge"]["exposed"][0]["summary"]
    )


def test_withheld_summary_is_redacted_too():
    eng = FakeEngram(
        relevant=[
            _lesson("secret contact admin@example.com",
                    sensitivity="secret", item_id="l-s"),
        ]
    )
    preview = build_context_preview(eng, role="assistant")
    blob = json.dumps(preview, ensure_ascii=False)
    assert "admin@example.com" not in blob
    assert preview["knowledge"]["withheld_count"] == 1


def test_quick_level_budget_trims_long_knowledge():
    long_items = [
        _lesson("x" * 1500 + f" tail-{i}", sensitivity="work", item_id=f"l-{i}")
        for i in range(4)
    ]
    eng = FakeEngram(relevant=long_items)
    preview = build_context_preview(eng, role="assistant", level="quick")
    assert preview["budget"]["max_chars"] == LEVELS["quick"]["max_chars"]
    assert preview["knowledge"]["trimmed_by_budget"] >= 1
    assert preview["budget"]["trimmed"] is True


# ---------------------------------------------------------------------------
# Input validation + invariants
# ---------------------------------------------------------------------------


def test_unknown_level_raises():
    with pytest.raises(ValueError, match="unknown level"):
        build_context_preview(FakeEngram(), level="mega")


def test_unknown_role_raises():
    with pytest.raises(ValueError, match="unknown role"):
        build_context_preview(FakeEngram(), role="superuser")


def test_defaults_and_invariant():
    preview = build_context_preview(FakeEngram())
    assert preview["level"] == DEFAULT_LEVEL
    assert preview["role"] == DEFAULT_ROLE
    assert preview["invariant"] == "context_preview_read_only"
    assert preview["caller"]["trust_level"] == ROLE_TRUST_ANCHORS[DEFAULT_ROLE]


def test_counts_are_consistent():
    preview = build_context_preview(_mixed_engram(), role="assistant")
    knowledge = preview["knowledge"]
    assert knowledge["exposed_count"] == len(knowledge["exposed"])
    assert knowledge["withheld_count"] == len(knowledge["withheld"])


# ---------------------------------------------------------------------------
# Renderers (bilingual: pin the runtime language so assertions are
# deterministic regardless of the machine's profile.json preference)
# ---------------------------------------------------------------------------


@pytest.fixture
def lang_en(monkeypatch):
    monkeypatch.setattr(i18n, "_runtime_lang", "en")


@pytest.fixture
def lang_zh(monkeypatch):
    monkeypatch.setattr(i18n, "_runtime_lang", "zh")


def test_text_render_has_governance_and_read_only_note(lang_en):
    preview = build_context_preview(_mixed_engram(), role="assistant")
    text = render_context_preview_text(preview)
    assert "Context preview" in text
    assert "role=assistant" in text
    assert "Governance:" in text
    assert "read-only preview" in text
    assert "secret recipe" in text  # withheld summary still shown to the owner


def test_text_render_localizes_to_chinese(lang_zh):
    preview = build_context_preview(_mixed_engram(), role="assistant")
    text = render_context_preview_text(preview)
    assert "记忆透视" in text
    assert "被拦截的知识" in text
    assert "只读预览" in text
    assert "secret recipe" in text  # values stay raw; only labels localize


def test_html_lang_attribute_follows_language(lang_zh):
    preview = build_context_preview(_mixed_engram(), role="assistant")
    assert '<html lang="zh">' in render_context_preview_html(preview)
    i18n._runtime_lang = "en"
    try:
        assert '<html lang="en">' in render_context_preview_html(preview)
    finally:
        i18n._runtime_lang = "zh"  # fixture monkeypatch still restores after


def test_identity_field_names_localize_to_chinese(lang_zh):
    preview = build_context_preview(_mixed_engram(), role="assistant")
    text = render_context_preview_text(preview)
    assert "角色: builder" in text
    assert "语言: zh" in text
    page = render_context_preview_html(preview)
    assert ">角色</span>" in page
    assert 'title="role"' in page  # raw key kept as tooltip for traceability


def test_identity_field_names_stay_raw_in_english(lang_en):
    preview = build_context_preview(_mixed_engram(), role="assistant")
    text = render_context_preview_text(preview)
    assert "role: builder" in text
    assert "角色" not in text


def test_identity_list_values_render_as_structured_items(lang_zh):
    eng = FakeEngram(profile={
        "role": "builder",
        "work_patterns": ["节奏: 快速迭代", "纯文本规则，没有前缀"],
    })
    preview = build_context_preview(eng, role="assistant")
    page = render_context_preview_html(preview)
    # one bullet row per item, not a "；"-joined blob
    assert page.count('class="val-item"') == 2
    assert 'class="vk">节奏</span>' in page  # "标签:" prefix highlighted
    assert "纯文本规则，没有前缀" in page
    assert "节奏: 快速迭代" not in page


def test_knowledge_summary_renders_multipart_as_bullets(lang_zh):
    eng = FakeEngram(relevant=[
        _lesson("要点一: 内容A；要点二: 内容B", sensitivity="work", item_id="l-m"),
        _lesson("单段纯文本经验", sensitivity="work", item_id="l-s"),
    ])
    preview = build_context_preview(eng, role="assistant")
    page = render_context_preview_html(preview)
    # multipart summary becomes bullet rows inside the table cell too
    assert 'class="vk">要点一</span>' in page
    assert 'class="vk">要点二</span>' in page
    assert "要点一: 内容A；要点二" not in page  # no more "；"-joined blob
    assert "单段纯文本经验" in page  # plain summaries stay plain


def test_long_summary_splits_title_sentences_and_highlights_arrows(lang_zh):
    summary = "标题示例: 第一句" + "很长" * 30 + "。第二句根治法 → 下一步收尾"
    eng = FakeEngram(relevant=[_lesson(summary, sensitivity="work", item_id="l-t")])
    preview = build_context_preview(eng, role="assistant")
    page = render_context_preview_html(preview)
    assert 'class="sum-title">标题示例</div>' in page  # lead becomes a title line
    assert "第二句根治法" in page  # sentences split into bullet rows
    assert page.count('class="val-item"') >= 2
    assert 'class="arr">→</span>' in page  # step arrows highlighted


def test_html_is_structured_not_a_terminal_dump(lang_en):
    preview = build_context_preview(_mixed_engram(), role="assistant")
    page = render_context_preview_html(preview)
    assert "<pre" not in page  # owner-facing layout, not a text dump
    assert 'class="hero"' in page
    assert 'class="stats"' in page
    assert "Knowledge withheld from this caller" in page


def test_html_escapes_injected_markup(lang_en):
    eng = FakeEngram(
        relevant=[
            _lesson("<script>alert(1)</script>", sensitivity="work", item_id="l-x"),
            _lesson("<img onerror=x>", sensitivity="secret", item_id="l-y"),
        ]
    )
    preview = build_context_preview(eng, role="assistant")
    page = render_context_preview_html(preview)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "<img onerror" not in page


def test_write_html_defaults_under_reports(tmp_path):
    preview = build_context_preview(_mixed_engram(), role="assistant")
    path = write_context_preview_html(preview, tmp_path)
    assert path.parent == tmp_path / "reports"
    content = path.read_text(encoding="utf-8")
    assert content.lstrip().lower().startswith("<!doctype html")


def test_write_html_honors_explicit_output(tmp_path):
    preview = build_context_preview(_mixed_engram(), role="owner")
    target = tmp_path / "out" / "preview.html"
    path = write_context_preview_html(preview, tmp_path, target)
    assert path == target
    assert target.exists()


# ---------------------------------------------------------------------------
# CLI wiring (arg validation + end-to-end against a temp store)
# ---------------------------------------------------------------------------


def _cli(monkeypatch, tmp_path, argv):
    # Import via setup_wizard (the canonical re-export hub); importing
    # cli_commands first would trip the known module-order circularity.
    from piia_engram.setup_wizard import run_preview

    monkeypatch.setattr(i18n, "_runtime_lang", "en")  # deterministic labels
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    return run_preview(argv)


def test_cli_help_exits_zero(monkeypatch, tmp_path, capsys):
    assert _cli(monkeypatch, tmp_path, ["--help"]) == 0
    assert "engram preview" in capsys.readouterr().out


def test_cli_rejects_unknown_option(monkeypatch, tmp_path, capsys):
    assert _cli(monkeypatch, tmp_path, ["--bogus"]) == 2
    assert "Unknown preview option" in capsys.readouterr().out


def test_cli_rejects_output_without_html(monkeypatch, tmp_path, capsys):
    assert _cli(monkeypatch, tmp_path, ["--output", "x.html"]) == 2
    assert "--output only applies with --html" in capsys.readouterr().out


def test_cli_rejects_json_plus_html(monkeypatch, tmp_path):
    assert _cli(monkeypatch, tmp_path, ["--json", "--html"]) == 2


def test_cli_rejects_unknown_level_as_usage_error(monkeypatch, tmp_path, capsys):
    assert _cli(monkeypatch, tmp_path, ["--level", "mega"]) == 2
    assert "unknown level" in capsys.readouterr().out


def test_cli_text_run_against_temp_store(monkeypatch, tmp_path, capsys):
    assert _cli(monkeypatch, tmp_path, ["--as", "assistant"]) == 0
    out = capsys.readouterr().out
    assert "Context preview" in out
    assert "read-only preview" in out


def test_cli_json_run_parses(monkeypatch, tmp_path, capsys):
    assert _cli(monkeypatch, tmp_path, ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["invariant"] == "context_preview_read_only"


def test_cli_html_run_writes_file(monkeypatch, tmp_path, capsys):
    out_file = tmp_path / "preview.html"
    assert _cli(
        monkeypatch, tmp_path, ["--html", "--output", str(out_file)]
    ) == 0
    assert out_file.exists()
    assert "written to" in capsys.readouterr().out
