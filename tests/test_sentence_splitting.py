"""Sentence splitting used by knowledge extraction.

A '.' inside a file name, a version number, a decimal or a URL is not a
sentence boundary; a '.', '!' or '?' followed by whitespace or the end of the
text is. Chinese full stops and full-width marks always end a sentence.
"""

from __future__ import annotations

from pathlib import Path

from piia_engram import session_filters
from piia_engram.continuity_digest import build_session_digest
from piia_engram.core import Engram

DECISION = "We decided to pin Python 3.12 in requirements.txt because the CI image changed."


def _split(text: str) -> list[str]:
    split_sentences = getattr(session_filters, "split_sentences", None)
    assert callable(split_sentences), "session_filters.split_sentences is missing"
    return [s.strip() for s in split_sentences(text) if s.strip()]


def test_file_names_versions_and_decimals_stay_inside_one_sentence():
    assert _split(DECISION) == [DECISION.rstrip(".")]


def test_urls_stay_inside_one_sentence():
    text = "Remember to install from https://pypi.org/project/piia-engram/?ref=a.b today."
    assert _split(text) == [text.rstrip(".")]


def test_sentence_ends_still_split():
    text = "We decided to use A. Then we tested B!Is it done? Yes\nNext line"
    assert _split(text) == ["We decided to use A", "Then we tested B!Is it done", "Yes", "Next line"]


def test_chinese_full_stops_always_split_and_keep_decimals():
    text = "我们决定采用 Python 3.12。注意 setup.py 的版本！可以吗？好"
    assert _split(text) == ["我们决定采用 Python 3.12", "注意 setup.py 的版本", "可以吗", "好"]


def test_extract_candidates_keeps_file_names_and_versions(tmp_path: Path):
    engram = Engram(root=tmp_path)
    candidates = engram.extract_candidates(DECISION)["candidates"]
    texts = [c.get("text", "") for c in candidates]
    assert any("3.12" in t and "requirements.txt" in t for t in texts), texts


def test_session_digest_keeps_file_names_and_versions():
    digest = build_session_digest(DECISION)
    summaries = [d["summary"] for d in digest["decisions"]]
    assert any("3.12" in s and "requirements.txt" in s for s in summaries), summaries


def test_session_insights_keep_file_names_and_versions(tmp_path: Path):
    engram = Engram(root=tmp_path)
    engram.extract_session_insights(DECISION)
    stored = " | ".join(
        f"{d.get('question', '')} {d.get('choice', '')} {d.get('title', '')}"
        for d in engram.get_decisions(limit=10)
    )
    assert "3.12" in stored and "requirements.txt" in stored, stored


def test_playbook_pitfalls_keep_file_names(tmp_path: Path):
    engram = Engram(root=tmp_path)
    pitfalls = engram._extract_pitfalls(
        "Note that the build failed because setup.py pinned version 3.12 wrongly."
    )
    assert any("setup.py" in p and "3.12" in p for p in pitfalls), pitfalls
