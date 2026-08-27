"""hook_content_digest (B-F1): privacy-guarded assistant-text digest.

Design v2 (adversarially reviewed): assistant-only collection, stateful block
filtering, normalization-before-detection-before-truncation, composed
redaction, hard budget, output guard before persistence, metadata-only audit,
behavioral default-off gate. Canary assertions run against the digest AND the
final persisted artifacts (see test_hooks_e2e for the end-to-end variant).
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from piia_engram.hook_digest import (
    CAPTURE_ORIGIN,
    MAX_LINE_CHARS,
    MAX_MESSAGES,
    MAX_TOTAL_BYTES,
    MAX_TOTAL_CHARS,
    PREFERENCE_KEY,
    build_digest,
    clean_block,
    digest_enabled,
    extract_assistant_text_blocks,
    normalize_text,
    output_guard_item,
    read_transcript_lines,
    sanitize_line,
    shape_scan,
)

# runtime-assembled fake credential shapes (never verbatim in source)
FAKE_KEY = "s" + "k-FAKE" + "-" + "A1b2C3d4E5f6G7h8I9j0"
FAKE_HEX = "d41d8cd98f00b204e9800998ecf8427e" + "abcdef123456"
FAKE_B64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5eg=="


def _assistant_line(text: str, ts: str = "2026-08-16T10:00:00.000Z") -> str:
    return json.dumps({
        "type": "assistant", "timestamp": ts,
        "content": [{"type": "text", "text": text}],
    })


def _user_line(text: str) -> str:
    return json.dumps({
        "type": "user", "timestamp": "2026-08-16T10:00:01.000Z",
        "content": [{"type": "text", "text": text}],
    })


# ── schema allowlist ────────────────────────────────────────────────────────


def test_schema_top_level_and_nested_shapes():
    nested = json.dumps({
        "timestamp": "2026-08-16T10:00:00.000Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "nested ok"}]},
    })
    blocks = extract_assistant_text_blocks([_assistant_line("top ok"), nested, _user_line("user")])
    assert blocks == ["top ok", "nested ok"]


def test_schema_forged_role_and_unknown_blocks_are_skipped():
    forged = json.dumps({
        "type": "user",  # outer type forged; nested role assistant must NOT rescue it
        "message": {"role": "assistant", "content": [{"type": "text", "text": "forged"}]},
    })
    unknown_block = json.dumps({
        "type": "assistant", "content": [{"type": "tool_use", "name": "Bash"}, {"type": "text", "text": "kept"}],
    })
    blocks = extract_assistant_text_blocks([forged, unknown_block, "not json at all"])
    assert blocks == ["kept"]


# ── block cleaning ──────────────────────────────────────────────────────────


def test_clean_block_stateful_fences():
    text = (
        "before\n"
        "```python\nSECRET_FENCE = 'x'\n```\n"
        "~~~\ntilde fence secret\n~~~\n"
        "    indented fence\n    still fenced\n"
        "inline `SECRET_INLINE` span\n"
        "> quoted secret\n"
        "kept line\n"
    )
    out = clean_block(text)
    for gone in ("SECRET_FENCE", "tilde fence secret", "fenced", "SECRET_INLINE", "quoted secret"):
        assert gone not in out
    assert "before" in out and "kept line" in out


def test_clean_block_unclosed_fence_drops_everything_after():
    out = clean_block("kept\n```\neverything after the unclosed fence is dropped")
    assert "unclosed" not in out and "kept" in out


# ── normalization & redaction composition ───────────────────────────────────


def test_normalize_folds_zero_width_and_fullwidth():
    assert normalize_text("sec\u200bret") == "secret"
    assert normalize_text("ｓｅｃｒｅｔ") == unicodedata.normalize("NFKC", "ｓｅｃｒｅｔ")


def test_sanitize_composed_shapes():
    cases = [
        f"token: {FAKE_KEY}",
        "Authorization: Bearer abc123def456",
        "postgres://admin:hunter2@db.internal:5432/prod",
        "https://api.example.com/v1?api_key=abc123&x=1",
        "Cookie: session=deadbeefcafebabe",
        "-----BEGIN RSA PRIVATE KEY-----",
        "path is C:\\Users\\someone\\secret.txt",
        "home path /home/alice/keys/id_rsa",
    ]
    for line in cases:
        sanitized, hit = sanitize_line(line)
        assert hit, line
        for raw_fragment in ("hunter2", "abc123def456", "deadbeefcafebabe", "secret.txt", "id_rsa", "PRIVATE KEY", FAKE_KEY):
            assert raw_fragment not in sanitized, (line, sanitized)


def test_sanitize_keeps_evidence_words():
    sanitized, hit = sanitize_line("验证发现失败因为路径解析错误")
    assert hit is False
    assert "验证" in sanitized and "失败" in sanitized


def test_shape_scan_detects_long_runs():
    assert shape_scan(f"hash {FAKE_HEX}")
    assert shape_scan(f"blob {FAKE_B64}")
    assert shape_scan("run AbCdEf1234567890abcdEF")
    assert not shape_scan("normal sentence with short words 123")


# ── budget ──────────────────────────────────────────────────────────────────


def test_budget_enforced_on_final_digest():
    lines = [
        _assistant_line(
            "教训" + str(i) + "：因为并发写导致失败，验证通过后修复 " + "字" * (MAX_LINE_CHARS + 50),
            ts=f"2026-08-16T10:{i % 60:02d}:00.000Z",
        )
        for i in range(MAX_MESSAGES + 10)
    ]
    digest = build_digest(lines)
    assert digest is not None
    body_lines = digest.splitlines()[1:]
    assert len(body_lines) <= MAX_MESSAGES
    assert all(len(line) <= MAX_LINE_CHARS for line in body_lines)
    assert len(digest) <= MAX_TOTAL_CHARS
    assert len(digest.encode("utf-8")) <= MAX_TOTAL_BYTES


# ── behavioral default-off gate ─────────────────────────────────────────────


@pytest.mark.parametrize("value", [None, False, "true", "yes", 1, [], {}, "", "TrUe"])
def test_digest_disabled_for_anything_but_literal_true(value):
    assert digest_enabled(value) is False


def test_digest_activation_formula():
    """4.18 gate: literal True activates; everything else disables."""
    assert digest_enabled(True) is True
    assert digest_enabled("true") is False
    assert digest_enabled(1) is False
    assert digest_enabled(None) is False
    assert digest_enabled(False) is False


def test_preference_key_is_stable():
    assert PREFERENCE_KEY == "hook_content_digest"
    assert CAPTURE_ORIGIN == "hook_content_digest"


# ── canary digest ───────────────────────────────────────────────────────────


def test_canary_digest_never_carries_secrets():
    boundary_prefix = "x" * (MAX_LINE_CHARS - 20)
    lines = [
        # user-pasted secret: user text is never collected
        _user_line(f"my password is hunter2 and key {FAKE_KEY}"),
        # tool echo: tool blocks are never collected
        json.dumps({"type": "assistant", "content": [
            {"type": "tool_use", "name": "Bash"},
            {"type": "tool_result", "content": f"output {FAKE_HEX}"},
        ]}),
        # assistant text with direct secrets (each must be redacted or dropped)
        _assistant_line(f"the deploy token is {FAKE_KEY} for staging"),
        _assistant_line(f"connection postgres://admin:hunter2@db.internal/prod worked"),
        _assistant_line("见路径 C:\\Users\\someone\\private notes"),
        # assistant restating a tool secret inside a fence (fence dropped)
        _assistant_line("result was:\n```\n" + FAKE_HEX + "\n```"),
        # zero-width obfuscated key
        _assistant_line("key " + "s​k-FAKE-" + "Z9y8X7w6V5u4T3s2R1q0"),
        # boundary-straddling secret (starts at char ~MAX_LINE_CHARS-20)
        _assistant_line(boundary_prefix + " " + FAKE_KEY + " tail"),
        # clean quality-bearing line that MUST survive
        _assistant_line("验证发现：发布前本地先跑门禁脚本因为 CI 会拦，实测把三轮往返压缩成零轮"),
        _assistant_line("决定采用线性分支方案因为保护分支禁止 merge commit，备选的解除保护被否决"),
    ]
    digest = build_digest(lines)
    assert digest is not None
    canaries = [
        "hunter2", FAKE_KEY, FAKE_HEX, "db.internal", "private notes",
        "PRIVATE", "s​k", "Z9y8X7w6V5u4T3s2R1q0",
    ]
    for canary in canaries:
        assert canary not in digest, canary
    assert "my password" not in digest  # user text never collected
    assert "三轮往返压缩成零轮" in digest  # clean line survives
    assert "线性分支方案" in digest


def test_whole_digest_fails_closed_when_aggregation_still_hot():
    # a single line that sanitizes to something still shape-hot is dropped;
    # if EVERY line is dropped the digest is None (never an empty shell)
    lines = [_assistant_line(f"blob {FAKE_B64} {FAKE_HEX} {FAKE_KEY}")]
    assert build_digest(lines) is None


# ── output guard ────────────────────────────────────────────────────────────


def test_output_guard_drops_secret_shaped_candidates():
    ok, reason = output_guard_item({"sentence": f"token was {FAKE_KEY} in logs"})
    assert ok is False and reason == "output_guard_secret_shape"
    ok, _ = output_guard_item({"sentence": f"checksum {FAKE_HEX} matched"})
    assert ok is False


def test_output_guard_accepts_clean_candidates():
    ok, reason = output_guard_item({
        "sentence": "验证发现：本地先跑门禁脚本可省三轮 CI 往返",
        "summary": "会话元数据与脱敏节选",
    })
    assert ok is True and reason == ""


# ── persistence path: guard + metadata-only audit + empty evidence span ─────


def test_hook_origin_extraction_guards_audits_and_evidence(tmp_path, monkeypatch):
    import os

    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")  # audit log ON for this test

    from piia_engram.core import Engram

    eng = Engram(root=root)

    quality = (
        "验证发现：发布前本地先跑门禁脚本因为 CI 会拦，实测把三轮往返压缩成零轮\n"
        "决定采用线性分支方案因为保护分支禁止 merge commit，备选的解除保护被否决\n"
        f"验证发现：部署密钥是 {FAKE_KEY} 因为轮换失败需要记录"
    )
    result = eng.extract_session_insights(
        quality,
        source_tool="claude_code",
        source_ref="unit-hook",
        force_staging=True,
        project_folder="",
        capture_origin=CAPTURE_ORIGIN,
    )
    # the secret-bearing sentence must be dropped by the output guard
    assert result["rejected_by_output_guard"] >= 1
    # clean sentences may still be staged
    assert result["saved_lessons"] + result["saved_decisions"] >= 1

    lessons = json.loads((root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    decisions = json.loads((root / "knowledge" / "decisions.json").read_text(encoding="utf-8"))
    for item in lessons + decisions:
        blob = json.dumps(item, ensure_ascii=False)
        assert FAKE_KEY not in blob and "hunter2" not in blob
        extraction = item.get("extraction") or {}
        assert extraction.get("evidence_span", "") == ""

    audit = (root / "audit.log").read_text(encoding="utf-8")
    assert "[metadata-only]" in audit
    # no staged item text leaked into the audit trail
    for item in lessons + decisions:
        text = (item.get("summary") or item.get("question") or item.get("title") or "")[:60]
        if text:
            assert text not in audit


def test_supervised_extraction_path_unchanged(tmp_path, monkeypatch):
    """Without capture_origin, no output guard and normal audit detail."""
    import os

    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")

    from piia_engram.core import Engram

    eng = Engram(root=root)
    summary = (
        "验证发现：发布前本地先跑门禁脚本因为 CI 会拦，实测节省三轮往返\n"
        "决定采用线性分支方案因为保护分支禁止 merge commit"
    )
    result = eng.extract_session_insights(summary, source_tool="codex", force_staging=True)
    assert result["rejected_by_output_guard"] == 0
    assert result["saved_lessons"] + result["saved_decisions"] >= 1
    audit = (root / "audit.log").read_text(encoding="utf-8")
    assert "[metadata-only]" not in audit
    lessons = json.loads((root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    assert any((i.get("extraction") or {}).get("evidence_span") for i in lessons)


# ── v4.17.1 regression classes (terminal-review blockers) ──────────────────


def _real_transcript_line(text: str, i: int = 0) -> str:
    """Real Claude Code shape: top-level type AND payload under message."""
    return json.dumps({
        "type": "assistant",
        "timestamp": f"2026-08-17T12:00:{i:02d}.000Z",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    })


def test_real_transcript_shape_is_extracted():
    """Regression (terminal review): real transcript lines carry BOTH a
    top-level type and message.content — the exclusive-shape parser read
    zero blocks from real transcripts (structural no-op)."""
    blocks = extract_assistant_text_blocks([
        _real_transcript_line("验证发现：门禁先行可省三轮往返", 1),
        _real_transcript_line("决定采用线性方案因为保护分支禁 merge", 2),
    ])
    assert blocks == [
        "验证发现：门禁先行可省三轮往返",
        "决定采用线性方案因为保护分支禁 merge",
    ]


def test_real_transcript_canary_digest_is_clean():
    lines = [
        _real_transcript_line(f"deploy token: {FAKE_KEY}", 1),
        _real_transcript_line("验证发现：门禁先行因为 CI 会拦，实测省三轮", 2),
    ]
    digest = build_digest(lines)
    assert digest is not None
    assert FAKE_KEY not in digest
    assert "省三轮" in digest


def test_cross_line_secret_pair_is_dropped_in_digest():
    """Regression (terminal review): token:/value split across two lines is
    invisible to single-line patterns; the PR-2 hardening normalizes the
    key form before pairing so the value line is dropped while clean lines
    survive."""
    lines = [
        _real_transcript_line("clean context survives the pairing", 0),
        _real_transcript_line("api token:\nZmFrZS1zZWNyZXQtdmFsdWUtMDA\nkept tail", 2),
    ]
    digest = build_digest(lines)
    assert digest is not None
    assert "ZmFrZS1zZWNyZXQtdmFsdWUtMDA" not in digest
    assert "kept tail" in digest
    assert "clean context survives" in digest


def test_output_guard_window_catches_cross_line_pair():
    from piia_engram.hook_digest import sanitize_line as _sl  # noqa: F401

    ok, reason = output_guard_item({
        "sentence": "ZmFrZS1zZWNyZXQtdmFsdWUtMDA",
        "window": "api token: ZmFrZS1zZWNyZXQtdmFsdWUtMDA",
    })
    assert ok is False, "cross-line pair must be caught via the window field"


def test_staging_items_never_reach_cold_start_context(tmp_path, monkeypatch):
    """Regression (terminal review): staged items are unreviewed and must
    not surface in quick_context / generate_context (verified-only)."""
    import os

    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))

    from piia_engram.core import Engram

    eng = Engram(root=root)
    eng.add_lesson({
        "summary": "STAGED-NEVER-SHOW lesson about guardrails",
        "domain": "testing", "tier": "staging", "status": "active",
    })
    eng.add_lesson({
        "summary": "VERIFIED-SHOWS lesson about guardrails",
        "domain": "testing", "tier": "verified", "status": "active",
    })
    eng.add_decision({
        "question": "STAGED decision question?",
        "choice": "STAGED-NEVER-SHOW choice", "tier": "staging", "status": "active",
    })

    body = eng.generate_context(level="standard")
    assert "VERIFIED-SHOWS" in body
    assert "STAGED-NEVER-SHOW" not in body

    eng.refresh_quick_context(level="standard")
    quick = (root / "quick_context.md").read_text(encoding="utf-8")
    assert "VERIFIED-SHOWS" in quick
    assert "STAGED-NEVER-SHOW" not in quick
