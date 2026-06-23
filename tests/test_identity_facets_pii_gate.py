"""M1 security regression: get_identity_facets must not leak decrypted PII.

Code review 2026-06-23 finding S2-1/A1-1 (HIGH): get_identity_facets applied NO
governance gate and forwarded the caller-controlled ``safe`` flag straight into
get_profile(). A non-owner could call get_identity_facets(facet='profile',
safe=False) under ENGRAM_GOVERNANCE=1 and receive the owner's fully-decrypted
ENCRYPTED_PROFILE_FIELDS (email/phone/real_name/id_number/...). Two compounding
gaps:

  1. The tool let the constrained party opt out of its own redaction (safe=False).
  2. Even safe=True stripped nothing, because trust_boundaries.restricted_fields
     defaults to [] — so the "safe" projection leaked the encrypted PII anyway.
     This also leaks via resource_profile()/reports (all use get_safe_profile()).

Fix:
  - core.get_profile(safe=True) excludes ENCRYPTED_PROFILE_FIELDS ∪ restricted_fields
    (encrypted fields are PII by definition; a safe projection must never expose them).
  - get_identity_facets forces safe=True for any non-owner caller.
"""

from __future__ import annotations

import asyncio

import pytest

from piia_engram import mcp_server
from piia_engram.core import Engram

EMAIL = "secret-victim@example.com"
IDNUM = "ID-CLASSIFIED-999"
ROLE = "Indie product owner"  # non-PII, should remain visible


@pytest.fixture
def gov_engram(tmp_path, monkeypatch):
    """Fresh Engram wired as the module global with a PII-bearing profile."""
    old = mcp_server._session
    old._stop_event.set()
    if old._heartbeat_thread is not None:
        old._heartbeat_thread.join(timeout=2.0)
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    engram = Engram(root=tmp_path)
    engram.update_profile(
        {"role": ROLE, "language": "zh", "email": EMAIL, "id_number": IDNUM}
    )
    monkeypatch.setattr(mcp_server, "_engram", engram)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    return engram


def _call(tool_name, **kwargs):
    return asyncio.run(getattr(mcp_server, tool_name)(**kwargs))


# ── core get_profile(safe=) semantics ────────────────────────────────────────


def test_get_profile_safe_excludes_encrypted_pii_even_when_restricted_empty(gov_engram):
    """safe=True must drop ENCRYPTED_PROFILE_FIELDS even with restricted_fields=[]."""
    assert gov_engram.get_trust_boundaries().get("restricted_fields", []) == []
    safe = gov_engram.get_profile(safe=True)
    assert "email" not in safe and "id_number" not in safe
    assert EMAIL not in str(safe) and IDNUM not in str(safe)
    # non-PII fields survive
    assert safe.get("role") == ROLE


def test_get_profile_unsafe_still_includes_pii(gov_engram):
    """safe=False (owner/internal) keeps full decrypted PII."""
    full = gov_engram.get_profile(safe=False)
    assert full.get("email") == EMAIL
    assert full.get("id_number") == IDNUM


# ── tool-level governance gate ───────────────────────────────────────────────


def test_non_owner_cannot_optout_redaction(gov_engram, monkeypatch):
    """Governance ON + web caller: safe=False must NOT leak PII (forced safe)."""
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    out = _call("get_identity_facets", facet="profile", safe=False)
    assert EMAIL not in out, "non-owner extracted decrypted email via safe=False"
    assert IDNUM not in out, "non-owner extracted decrypted id_number via safe=False"


def test_non_owner_facet_all_is_safe(gov_engram, monkeypatch):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    out = _call("get_identity_facets", facet="all", safe=False)
    assert EMAIL not in out and IDNUM not in out


def test_non_owner_keeps_non_pii_fields(gov_engram, monkeypatch):
    """The gate redacts PII but does not blank the whole facet."""
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    out = _call("get_identity_facets", facet="profile", safe=False)
    assert ROLE in out, "non-owner lost non-PII role field (over-redaction)"


def test_owner_safe_false_still_sees_pii(gov_engram, monkeypatch):
    """Governance ON + self owner: explicit safe=False still returns full PII."""
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "self")
    out = _call("get_identity_facets", facet="profile", safe=False)
    assert EMAIL in out and IDNUM in out


def test_flag_off_owner_safe_false_sees_pii(gov_engram, monkeypatch):
    """Governance OFF: byte-identical passthrough, owner-equivalent access."""
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    out = _call("get_identity_facets", facet="profile", safe=False)
    assert EMAIL in out and IDNUM in out


# ── resource endpoint + safe surfaces ────────────────────────────────────────


def test_resource_profile_excludes_pii(gov_engram):
    """resource_profile()/get_safe_profile() must not surface encrypted PII."""
    out = mcp_server.resource_profile()
    assert EMAIL not in out and IDNUM not in out
