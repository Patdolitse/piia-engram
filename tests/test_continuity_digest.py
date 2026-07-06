"""Task 2: structured session digest builder (session_digest.v1).

build_session_digest turns a free-form session summary into a deterministic,
redacted, structured digest. No LLM call, no file I/O — pure functions only.
"""

from __future__ import annotations

from piia_engram.continuity_digest import (
    build_session_digest,
    render_session_digest_markdown,
    sanitize_digest_value,
)

# Synthetic credential shapes assembled at runtime so static secret scanners
# don't flag the test source — they still exercise the redactor at runtime.
_FAKE_SK_KEY = "sk-" + "abcdef1234567890ABCDEF"
_FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


class TestBuildSessionDigest:
    def test_schema_always_present(self):
        digest = build_session_digest("")
        assert digest["schema"] == "session_digest.v1"

    def test_short_unstructured_summary_still_valid(self):
        digest = build_session_digest("did a few small things")
        assert digest["schema"] == "session_digest.v1"
        # Empty signals are stable containers, never missing keys.
        assert isinstance(digest["goal"], str)
        assert isinstance(digest["completed"], list)
        assert isinstance(digest["verification"], list)
        assert isinstance(digest["decisions"], list)
        assert isinstance(digest["lessons"], list)
        assert isinstance(digest["risks"], list)
        assert isinstance(digest["next_actions"], list)
        assert isinstance(digest["changed_files"], list)
        assert isinstance(digest["source"], dict)

    def test_structured_summary_extracts_core_fields(self):
        summary = (
            "Goal: ship the project resume pack.\n"
            "Completed: wrote the digest builder; added focused tests.\n"
            "Tests: pytest tests/test_continuity_digest.py passed.\n"
            "Next: integrate digest with save_agent_context.\n"
        )
        digest = build_session_digest(
            summary, tool="claude_code", project_id="engram"
        )
        assert "resume pack" in digest["goal"].lower()
        assert any("digest builder" in c.lower() for c in digest["completed"])
        assert any(v.get("status") == "passed" for v in digest["verification"])
        assert any("integrate" in n.lower() for n in digest["next_actions"])

    def test_source_fields_recorded(self):
        digest = build_session_digest(
            "x", tool="codex", project_id="proj", session_ref="sess-1"
        )
        assert digest["source"]["tool"] == "codex"
        assert digest["source"]["project_id"] == "proj"
        assert digest["source"]["session_ref"] == "sess-1"

    def test_unknown_tool_defaults_to_unknown(self):
        digest = build_session_digest("x")
        assert digest["source"]["tool"] in ("unknown", "")

    def test_lessons_and_decisions_are_candidates(self):
        summary = (
            "Decided to use PostgreSQL for the analytics service.\n"
            "Lesson: always pin dependency versions to avoid breakage.\n"
        )
        digest = build_session_digest(summary)
        for item in digest["decisions"] + digest["lessons"]:
            assert item.get("status") == "candidate"


class TestRedaction:
    def test_secrets_and_paths_excluded_from_render(self):
        # Sensitive shapes land in extracted fields (goal/completed), so this
        # exercises the redaction path, not mere non-extraction.
        summary = (
            f"Goal: deploy using key {_FAKE_SK_KEY}.\n"
            f"Completed: rotated {_FAKE_AWS_KEY}; "
            "wrote C:\\Users\\alice\\secret.txt; synced E:\\Private\\store.db.\n"
        )
        digest = build_session_digest(summary)
        blob = render_session_digest_markdown(digest, max_chars=8000)
        assert _FAKE_SK_KEY not in blob
        assert _FAKE_AWS_KEY not in blob
        assert "C:\\Users\\alice" not in blob
        assert "E:\\Private\\store.db" not in blob

    def test_sanitize_digest_value_scrubs_nested(self):
        value = {
            "note": f"token {_FAKE_SK_KEY}",
            "paths": ["E:\\Personal\\store.db", "fine/relative/path.py"],
        }
        out = sanitize_digest_value(value)
        flat = str(out)
        assert _FAKE_SK_KEY not in flat
        assert "E:\\Personal\\store.db" not in flat
        # Ordinary relative paths survive — only absolute/secret shapes scrub.
        assert "fine/relative/path.py" in flat

    def test_sanitize_preserves_plain_text(self):
        assert sanitize_digest_value("just a normal sentence") == (
            "just a normal sentence"
        )


class TestRenderMarkdown:
    def test_respects_max_chars(self):
        summary = "Completed: " + "; ".join(f"task number {i}" for i in range(300))
        digest = build_session_digest(summary)
        md = render_session_digest_markdown(digest, max_chars=500)
        assert len(md) <= 500

    def test_render_returns_string(self):
        digest = build_session_digest("Goal: do the thing")
        md = render_session_digest_markdown(digest)
        assert isinstance(md, str)
        assert md.strip()
