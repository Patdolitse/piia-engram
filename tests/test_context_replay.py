"""Context Compression Replay packet tests."""

from __future__ import annotations

from piia_engram import context_replay


def test_replay_packet_redacts_and_bounds_summary():
    packet = context_replay.build_replay_packet(
        "Continue task. Secret key sk-test_1234567890abcdef1234567890abcdef. " + "x" * 500,
        source="postcompact",
        max_summary_chars=120,
    )

    assert packet["source"] == "postcompact"
    assert packet["summary_truncated"] is True
    assert len(packet["summary"]) <= 120
    assert "sk-test_" not in packet["summary"]
    assert packet["summary_sha256_12"]
    assert packet["applied"] is False


def test_replay_packet_empty_summary_is_metadata_only():
    packet = context_replay.build_replay_packet("", source="manual")

    assert packet["summary"] == ""
    assert packet["summary_truncated"] is False
    assert packet["source"] == "manual"
    assert packet["invariant"] == "replay_packet_only"
