"""Stage 3 item E — export PII/secret redaction boundary linter.

Two layers under test:

1. The pure string linter (:mod:`piia_engram.export_redaction`): given rendered
   export text, it flags credential shapes (high) and absolute-home-paths / bare
   emails (warn), returns METADATA-ONLY findings (redacted previews, no raw
   secret), and can scrub a surface in place.
2. The product wiring: ``export_identity_card`` must not emit a credential that
   leaked into a stored lesson/decision body, even though that surface renders
   free-prose summaries directly.

All fake secrets are obvious non-real placeholders; no real credentials appear.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram import export_redaction as er

# Obvious fake credentials (shape-valid, value-fake) — never real.
FAKE_OPENAI = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
FAKE_GITHUB = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
FAKE_AWS = "AKIAIOSFODNN7EXAMPLE"
FAKE_PEM = "-----BEGIN RSA PRIVATE KEY-----"
WIN_PATH = r"C:\Users\victim\secret\notes.txt"
POSIX_PATH = "/home/victim/.ssh/id_rsa"
FAKE_EMAIL = "victim@corp.example.com"


class TestScanExportText:
    def test_detects_credentials_as_high_severity(self):
        for secret in (FAKE_OPENAI, FAKE_GITHUB, FAKE_AWS, FAKE_PEM):
            findings = er.scan_export_text(f"a lesson mentioning {secret} inline")
            assert any(f["category"] == "secret" and f["severity"] == "high"
                       for f in findings), secret

    def test_preview_is_redacted_never_raw(self):
        findings = er.scan_export_text(f"token {FAKE_OPENAI} here")
        assert findings
        for f in findings:
            # the redacted preview must not reconstruct the secret
            assert FAKE_OPENAI not in f["preview"]
            assert f["preview"].endswith("***")
            assert len(f["preview"]) <= 7

    def test_detects_absolute_home_paths_as_warn(self):
        for p in (WIN_PATH, POSIX_PATH):
            findings = er.scan_export_text(f"saved to {p} yesterday")
            assert any(f["category"] == "user_path" and f["severity"] == "warn"
                       for f in findings), p

    def test_detects_bare_email_as_warn(self):
        findings = er.scan_export_text(f"contact {FAKE_EMAIL} for access")
        assert any(f["category"] == "email" for f in findings)

    def test_clean_prose_has_no_findings(self):
        text = "Always pin dependency versions; prefer GUI over CLI for the user."
        assert er.scan_export_text(text) == []
        assert er.is_export_clean(text)

    def test_empty_and_non_str_are_clean(self):
        assert er.scan_export_text("") == []
        assert er.scan_export_text(None) == []  # type: ignore[arg-type]
        assert er.is_export_clean("")

    def test_is_export_clean_blocks_high_always(self):
        text = f"oops {FAKE_OPENAI}"
        assert er.is_export_clean(text) is False
        assert er.is_export_clean(text, allow_warn=False) is False

    def test_is_export_clean_warn_only_blocks_in_strict(self):
        text = f"file at {WIN_PATH}"
        assert er.is_export_clean(text, allow_warn=True) is True
        assert er.is_export_clean(text, allow_warn=False) is False


class TestRedactExportText:
    def test_redacts_secret_in_place(self):
        out = er.redact_export_text(f"key is {FAKE_OPENAI} ok")
        assert FAKE_OPENAI not in out
        assert "[REDACTED]" in out
        assert out.startswith("key is ") and out.endswith(" ok")

    def test_redacts_multiple_and_overlapping(self):
        text = f"{FAKE_GITHUB} and {WIN_PATH} and {FAKE_EMAIL}"
        out = er.redact_export_text(text)
        for raw in (FAKE_GITHUB, WIN_PATH, FAKE_EMAIL):
            assert raw not in out

    def test_clean_text_unchanged(self):
        text = "no secrets here at all"
        assert er.redact_export_text(text) == text

    def test_custom_placeholder(self):
        out = er.redact_export_text(f"x {FAKE_AWS} y", placeholder="<X>")
        assert "<X>" in out and FAKE_AWS not in out


class TestSummarizeFindings:
    def test_metadata_only_rollup(self):
        findings = er.scan_export_text(f"{FAKE_OPENAI} {WIN_PATH}")
        summary = er.summarize_findings(findings)
        assert summary["high_severity"] >= 1
        assert summary["clean"] is False
        assert summary["by_category"].get("secret", 0) >= 1
        # rollup carries no offsets or previews of individual hits
        assert set(summary) == {"total", "high_severity", "by_category", "clean"}


class TestIdentityCardWiring:
    @pytest.fixture()
    def store(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
        root = tmp_path / "engram"
        monkeypatch.setenv("ENGRAM_DIR", str(root))
        return Engram(root=root)

    def test_identity_card_redacts_leaked_credential_in_lesson(self, store: Engram):
        store.add_lesson({
            "summary": f"deploy uses {FAKE_OPENAI} as the api key (do not lose it)",
            "domain": "ops",
            "tier": "verified",
            "status": "active",
        })
        card = store.export_identity_card()
        assert FAKE_OPENAI not in card
        assert "[REDACTED]" in card
        # the rest of the useful text survives
        assert "deploy uses" in card

    def test_identity_card_redacts_leaked_path_in_decision(self, store: Engram):
        store.add_decision({
            "question": "where to store the dump",
            "choice": f"put it under {WIN_PATH} for now",
        })
        card = store.export_identity_card()
        assert WIN_PATH not in card
        assert "[REDACTED]" in card

    def test_clean_identity_card_unaffected(self, store: Engram):
        store.add_lesson({
            "summary": "Always pin dependency versions in CI",
            "domain": "ci", "tier": "verified", "status": "active",
        })
        card = store.export_identity_card()
        assert "Always pin dependency versions in CI" in card
        assert "[REDACTED]" not in card

    def test_rendered_card_passes_linter(self, store: Engram):
        store.add_lesson({
            "summary": f"token {FAKE_GITHUB} leaked into a note",
            "domain": "secops", "tier": "verified", "status": "active",
        })
        card = store.export_identity_card()
        # after the product scrub, the linter must find no high-severity leak
        assert er.is_export_clean(card)
