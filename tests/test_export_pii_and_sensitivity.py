"""M6+M7 security regression: export surfaces must catch phone/ID/card PII
and reject invalid max_sensitivity values.

M6 (S3-1): export_redaction._SCANNERS misses CN phone, CN ID, and Luhn-valid
card numbers that sensitivity.py already detects. Export surfaces can leak
these PII types without any warning.

M7 (A2-2): agents_md_export --max-sensitivity accepts arbitrary strings
including "secret" and "unknown", silently falling back to default. Must
validate against allowlist and reject secret/unknown values for export.
"""

from __future__ import annotations

import pytest

from piia_engram import export_redaction as er
from piia_engram.agents_md_export import select_exportable, build_agents_md_export


# ── M6: PII pattern coverage ───────────────────────────────────────────────

CN_PHONE = "13800138000"
CN_ID = "110105194912310021"  # valid format, fake data
# 4111111111111111 passes Luhn (classic test card number)
LUHN_CARD = "4111111111111111"
# 1234567890123456 does NOT pass Luhn
NON_LUHN_CARD = "1234567890123456"


class TestExportDetectsPhonePII:
    def test_scan_detects_cn_phone(self):
        findings = er.scan_export_text(f"call me at {CN_PHONE} for access")
        assert any(f["category"] == "phone" for f in findings), \
            "CN phone number not detected in export scan"

    def test_redact_scrubs_cn_phone(self):
        out = er.redact_export_text(f"call {CN_PHONE} now")
        assert CN_PHONE not in out
        assert "[REDACTED]" in out


class TestExportDetectsIdPII:
    def test_scan_detects_cn_id(self):
        findings = er.scan_export_text(f"ID number is {CN_ID}")
        assert any(f["category"] == "id_number" for f in findings), \
            "CN national ID not detected in export scan"

    def test_redact_scrubs_cn_id(self):
        out = er.redact_export_text(f"resident ID {CN_ID} on file")
        assert CN_ID not in out


class TestExportDetectsCardPII:
    def test_scan_detects_luhn_valid_card(self):
        findings = er.scan_export_text(f"card number {LUHN_CARD}")
        assert any(f["category"] == "card" for f in findings), \
            "Luhn-valid card number not detected in export scan"

    def test_scan_ignores_non_luhn_digit_run(self):
        """13-19 digit runs that fail Luhn must NOT be flagged as cards."""
        findings = er.scan_export_text(f"ref number {NON_LUHN_CARD}")
        assert not any(f["category"] == "card" for f in findings), \
            "Non-Luhn digit run should not be flagged as card"

    def test_redact_scrubs_luhn_valid_card(self):
        out = er.redact_export_text(f"pay with {LUHN_CARD}")
        assert LUHN_CARD not in out


class TestExportCleanWithPII:
    def test_is_export_clean_false_for_phone(self):
        assert er.is_export_clean(f"phone {CN_PHONE}", allow_warn=False) is False

    def test_is_export_clean_false_for_id(self):
        assert er.is_export_clean(f"id {CN_ID}", allow_warn=False) is False

    def test_is_export_clean_false_for_card(self):
        assert er.is_export_clean(f"card {LUHN_CARD}", allow_warn=False) is False


# ── M7: max_sensitivity validation ─────────────────────────────────────────

SAMPLE_LESSON = {
    "summary": "Always pin deps",
    "domain": "ci",
    "tier": "verified",
    "status": "active",
}


class TestMaxSensitivityValidation:
    def test_valid_levels_accepted(self):
        """public, work, private should not raise."""
        for level in ("public", "work", "private"):
            select_exportable([SAMPLE_LESSON], max_sensitivity=level)

    def test_secret_level_rejected_for_export(self):
        """Exporting at sensitivity=secret must raise — secrets are not exportable."""
        with pytest.raises(ValueError, match=r"secret.*not.*allow|invalid.*sensitivity"):
            select_exportable([SAMPLE_LESSON], max_sensitivity="secret")

    def test_unknown_level_rejected(self):
        """Arbitrary strings must raise, not silently fall back to default."""
        with pytest.raises(ValueError, match=r"(?i)invalid.*sensitivity|unknown.*level"):
            select_exportable([SAMPLE_LESSON], max_sensitivity="banana")

    def test_build_agents_md_rejects_secret(self):
        with pytest.raises(ValueError):
            build_agents_md_export(lessons=[SAMPLE_LESSON], max_sensitivity="secret")

    def test_build_agents_md_rejects_unknown(self):
        with pytest.raises(ValueError):
            build_agents_md_export(lessons=[SAMPLE_LESSON], max_sensitivity="xyzzy")
