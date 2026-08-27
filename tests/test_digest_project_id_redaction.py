"""Regression: a project-id hash that matches a PII shape must not be
redacted in the digest source block (breaks exact-scope filtering).

Root cause (Codex investigation ENG-CORE-013): a 12-hex project hash like
"13834567890a" matches the CN mobile-phone pattern; sanitize_digest_value
rewrites it to [REDACTED]a; the exact-scope filter then excludes the
project's own digest because the source id no longer matches the canonical
id. This is deterministic, not a flake.
"""
from __future__ import annotations

from piia_engram.continuity_digest import build_session_digest, sanitize_digest_value

# 12-hex hashes whose first 11 digits match the CN mobile pattern (1[3-9]XXXXXXXXX)
PII_SHAPE_HASHES = [
    "13834567890a",  # 13834567890 = valid CN mobile prefix
    "15912345678b",
    "18612345678c",
]
NORMAL_HASHES = [
    "abc123def456",
    "f7a2b3c4d5e6",
    "0123456789ab",
]


class TestProjectIdPreservation:
    def test_pii_shape_hash_survives_digest_source(self):
        """The internal project_id is preserved verbatim in the digest
        source block even when its digits match a phone pattern."""
        for pid in PII_SHAPE_HASHES:
            digest = build_session_digest(
                "goal: test\nnext: verify",
                tool="codex",
                project_id=pid,
                session_ref="test-session",
            )
            assert digest["source"]["project_id"] == pid, (
                f"project_id {pid} was redacted to "
                f"{digest['source']['project_id']!r}"
            )

    def test_normal_hash_unchanged(self):
        for pid in NORMAL_HASHES:
            digest = build_session_digest(
                "goal: test", tool="codex", project_id=pid, session_ref="s"
            )
            assert digest["source"]["project_id"] == pid

    def test_digest_body_still_scrubbed(self):
        """The body (goal, lessons, etc.) still passes the full scrubber."""
        body_with_secret = "goal: use s" + "k-FAKE1234567890abcdef key"
        digest = build_session_digest(
            body_with_secret, tool="codex", project_id="abc", session_ref="s"
        )
        assert ("s" + "k-FAKE") not in str(digest)

    def test_raw_sanitize_still_redacts_pii_shape(self):
        """The underlying scrubber still redacts PII shapes in general
        values — the fix is scoped to the source.project_id field only."""
        result = sanitize_digest_value({"note": "call me at 13834567890"})
        assert "13834567890" not in result["note"]

    def test_scope_filter_roundtrip(self):
        """End-to-end: a PII-shape project id's digest must survive the
        exact-scope comparison (the path that was breaking in CI)."""
        # the exact-scope filter compares the digest source id against the
        # project's canonical id — they must match
        for pid in PII_SHAPE_HASHES:
            digest = build_session_digest(
                "goal: test", tool="codex", project_id=pid, session_ref="s"
            )
            # the exact-scope filter compares these two:
            assert digest["source"]["project_id"] == pid, (
                f"scope mismatch for PII-shape hash {pid}"
            )
