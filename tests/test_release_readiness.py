"""Tests for the local release readiness report (Phase 12).

The always-on committed guarantee: public, human-facing docs carry no
reverse-disclosure signals (personal absolute paths, the maintainer's private
drive). This complements the gitignored-file private-term scanners without
itself naming any private workspace.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from piia_engram import release_readiness as rr

ROOT = Path(__file__).resolve().parents[1]


# --- the public-doc guard against the real repo ------------------------------

def test_public_docs_have_no_disclosure_signals():
    hits = rr.scan_public_docs_for_private_terms(ROOT)
    assert hits == [], f"reverse-disclosure signal(s) in public docs: {hits}"


def test_real_repo_readiness_structure():
    report = rr.build_release_readiness(ROOT)
    assert report["version"]  # pyproject version present
    assert report["missing_files"] == []
    assert report["english_first_ok"] is True
    assert report["publish_allowlist_present"] is True
    assert report["private_term_hits"] == []
    assert "no build/tag/publish" in report["note"]


# --- the scanner detects planted disclosure shapes ---------------------------

@pytest.mark.parametrize("planted", [
    "see C:\\Users\\alice\\secret for details",
    "the file lives at D:\\private\\thing",
    "config at /home/alice/.config/private",
    "macos path /Users/bob/Library/x",
])
def test_scanner_flags_personal_paths(tmp_path, planted):
    # Build a fake repo with one scanned doc containing a disclosure shape.
    (tmp_path / "README.md").write_text(planted, encoding="utf-8")
    hits = rr.scan_public_docs_for_private_terms(tmp_path)
    assert any(h["file"] == "README.md" for h in hits)


def test_clean_doc_has_no_hits(tmp_path):
    (tmp_path / "README.md").write_text(
        "Engram stores memory under ~/.engram. Run `engram setup`.", encoding="utf-8")
    assert rr.scan_public_docs_for_private_terms(tmp_path) == []


# --- readiness composition on a synthetic tree -------------------------------

def test_missing_files_marks_not_ready(tmp_path):
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    report = rr.build_release_readiness(tmp_path)
    assert report["ready"] is False
    assert "LICENSE" in report["missing_files"]


def test_english_first_requires_english_primary(tmp_path):
    # Only the Chinese translation present → english-first fails.
    (tmp_path / "README.zh-CN.md").write_text("中文", encoding="utf-8")
    report = rr.build_release_readiness(tmp_path)
    assert report["english_first_ok"] is False


def test_render_text_smoke(tmp_path):
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    report = rr.build_release_readiness(tmp_path)
    text = rr.render_release_readiness_text(report)
    assert "Release readiness" in text
    assert "no build/tag/publish performed" in text


def test_local_private_terms_folded_in_when_present(tmp_path, monkeypatch):
    # A local .sanitizeignore adds a precise term; a doc containing it is flagged.
    (tmp_path / ".sanitizeignore").write_text("high:SuperSecretProjectX\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("mentions SuperSecretProjectX here", encoding="utf-8")
    # Point HOME away so the custom-terms file does not interfere.
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "nohome"))
    hits = rr.scan_public_docs_for_private_terms(tmp_path)
    assert any(h["match"] == "SuperSecretProjectX" for h in hits)
