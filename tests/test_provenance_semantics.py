"""Canonical knowledge trust/provenance semantics.

Contract matrix:
- lifecycle tier/status: staging/verified/archived-like item state.
- validation maturity: unreviewed/validated/needs_review labeling state.
- confirmation evidence: human/test_signal/anchor/none owner evidence.
- temporal freshness: fresh/aging/stale/unknown derived from timestamps.

The dimensions are not substitutes for one another. A verified tier is not an
evidence-backed validation claim, and a fresh timestamp is not trust.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from piia_engram import provenance as P


NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (
            {
                "source_tool": "codex",
                "domain": "testing",
                "provenance": {"source_agent": "codex", "run_id": "run-1"},
            },
            {
                "source_kind": "agent",
                "annotation_quality": "partial",
                "validation_state": "unreviewed",
                "signals": ["has_domain", "has_run_id", "has_source_agent", "has_source_tool"],
            },
        ),
        (
            {"source_tool": "owner", "domain": "workflow"},
            {
                "source_kind": "human",
                "annotation_quality": "partial",
                "validation_state": "unreviewed",
                "signals": ["has_domain", "has_source_tool"],
            },
        ),
        (
            {"source_tool": "openclaw_import", "domain": "migration"},
            {
                "source_kind": "imported",
                "annotation_quality": "partial",
                "validation_state": "unreviewed",
                "signals": ["has_domain", "has_source_tool"],
            },
        ),
        (
            {},
            {
                "source_kind": "unknown",
                "annotation_quality": "raw",
                "validation_state": "unreviewed",
                "signals": [],
            },
        ),
        (
            {
                "source_tool": "codex",
                "domain": "release",
                "provenance": {
                    "source_agent": "codex",
                    "run_id": "run-2",
                    "last_validated_at": "2026-06-01T00:00:00Z",
                },
            },
            {
                "source_kind": "agent",
                "annotation_quality": "mature",
                "validation_state": "validated",
                "signals": [
                    "has_domain",
                    "has_last_validated_at",
                    "has_run_id",
                    "has_source_agent",
                    "has_source_tool",
                ],
            },
        ),
        (
            {
                "source_tool": "codex",
                "domain": "ops",
                "risk_level": "high",
                "tier": "staging",
                "approval_required": True,
                "approval_status": "pending",
                "provenance": {
                    "source_agent": "codex",
                    "run_id": "run-3",
                    "last_validated_at": "2026-06-01T00:00:00Z",
                },
            },
            {
                "source_kind": "agent",
                "annotation_quality": "partial",
                "validation_state": "needs_review",
                "signals": [
                    "has_domain",
                    "has_last_validated_at",
                    "has_run_id",
                    "has_source_agent",
                    "has_source_tool",
                    "high_risk",
                    "needs_owner_review",
                ],
            },
        ),
    ],
)
def test_derive_labeling_contract_matrix(entry: dict, expected: dict) -> None:
    assert P.derive_labeling(entry) == expected


def test_verified_tier_without_validation_signal_is_still_unreviewed() -> None:
    labeling = P.derive_labeling(
        {
            "summary": "verified lifecycle is not validation evidence",
            "tier": "verified",
            "status": "active",
            "created_at": "2026-06-02T00:00:00+00:00",
        }
    )
    freshness = P.compute_freshness(
        {"tier": "verified", "status": "active", "created_at": "2026-06-02T00:00:00+00:00"},
        now=NOW,
    )

    assert labeling["validation_state"] == "unreviewed"
    assert freshness["freshness_status"] == "fresh"


def test_last_validated_at_validates_maturity_without_confirmation_source() -> None:
    entry = {
        "summary": "descriptive validation timestamp",
        "domain": "docs",
        "provenance": {
            "source_agent": "codex",
            "last_validated_at": "2026-06-01T00:00:00+00:00",
        },
    }

    assert P.derive_labeling(entry)["validation_state"] == "validated"
    trust = P.project_trust(entry, now=NOW)
    assert trust["validated_at"] == "2026-06-01T00:00:00+00:00"
    assert "confirmation_source" not in trust


@pytest.mark.parametrize(
    ("provenance", "expected"),
    [
        ({"confirmation_source": "human"}, {"confirmation_source": "human"}),
        ({"confirmation_source": "test_signal"}, {"confirmation_source": "test_signal"}),
        (
            {
                "confirmation_source": "anchor",
                "anchor_ref": "dep:react",
                "anchor_status": "valid",
                "anchor_project_id": "github.com/acme/app",
            },
            {
                "confirmation_source": "anchor",
                "anchor": "dep:react",
                "anchor_status": "valid",
                "anchor_project_id": "github.com/acme/app",
            },
        ),
    ],
)
def test_owner_confirmation_evidence_projects_when_valid(
    provenance: dict, expected: dict
) -> None:
    trust = P.project_trust({"provenance": provenance}, now=NOW)
    for key, value in expected.items():
        assert trust[key] == value


@pytest.mark.parametrize(
    "anchor_ref",
    ["dep:react", "dep:jest", "file:README.md", "file:package.json", "github:actions"],
)
def test_legal_anchor_refs_remain_compatible(anchor_ref: str) -> None:
    trust = P.project_trust(
        {
            "provenance": {
                "confirmation_source": "anchor",
                "anchor_ref": anchor_ref,
                "anchor_status": "valid",
            }
        },
        now=NOW,
    )
    assert trust["anchor"] == anchor_ref


@pytest.mark.parametrize("project_id", ["github.com/acme/app", "id:x"])
def test_legal_anchor_project_ids_remain_compatible(project_id: str) -> None:
    trust = P.project_trust(
        {
            "provenance": {
                "confirmation_source": "anchor",
                "anchor_ref": "dep:jest",
                "anchor_status": "valid",
                "anchor_project_id": project_id,
            }
        },
        now=NOW,
    )
    assert trust["anchor_project_id"] == project_id


def test_recall_provenance_and_trust_fail_closed_without_echoing_unsafe_refs() -> None:
    unsafe = "C:/Users/victim/sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\nsecret"
    entry = {
        "source_tool": unsafe,
        "provenance": {
            "source_agent": unsafe,
            "run_id": "../escape",
            "last_validated_at": "not-a-date",
            "confirmation_source": "robot",
            "anchor_ref": unsafe,
            "anchor_status": "trusted",
            "anchor_project_id": unsafe,
            "anchor_event": "superseded",
            "anchor_successor_ref": unsafe,
        },
    }

    projected = P.project_recall_provenance(entry)
    trust = P.project_trust(entry, now=NOW)
    rendered = repr({"projected": projected, "trust": trust})

    assert projected == {}
    assert "confirmation_source" not in trust
    assert "anchor" not in trust
    assert "anchor_status" not in trust
    assert "anchor_project_id" not in trust
    assert "superseded_by" not in trust
    assert "victim" not in rendered
    assert "sk-proj-" not in rendered
    assert "escape" not in rendered


@pytest.mark.parametrize(
    "value",
    ["codex", "claude_code", "cursor-sub", "wf-123", "org/tool:v1", "github:actions"],
)
def test_identifier_sanitizer_preserves_existing_legal_producers(value: str) -> None:
    assert P._clean_identifier(value) == value


@pytest.mark.parametrize("field", ["source_agent", "run_id"])
@pytest.mark.parametrize(
    ("case", "unsafe"),
    [
        ("path", "C:/Users/SENTINEL/secret"),
        ("newline", "codex\nSENTINEL"),
        ("free_text", "free text SENTINEL"),
        ("api_key_label", "api_key=SENTINEL"),
        ("bearer_label", "Authorization=Bearer_SENTINEL"),
        ("token_colon", "token:SENTINEL"),
        ("api_key_colon", "api_key:SENTINEL"),
        ("password_colon", "password:SENTINEL"),
        ("passwd_colon", "passwd:SENTINEL"),
        ("secret_key_colon", "secret_key:SENTINEL"),
        ("access_key_colon", "access_key:SENTINEL"),
        ("credential_colon", "credential:SENTINEL"),
        ("credentials_colon", "credentials:SENTINEL"),
        ("bearer_colon", "bearer:SENTINEL"),
        ("authorization_colon", "Authorization:Bearer_SENTINEL"),
        ("namespaced_token_colon", "org/token:SENTINEL"),
        ("namespaced_api_key_colon", "org/api_key:SENTINEL"),
        ("namespaced_secret_key_colon", "org/secret_key:SENTINEL"),
        ("namespaced_access_key_colon", "org/access-key:SENTINEL"),
        ("namespaced_credential_colon", "org/credential:SENTINEL"),
        ("namespaced_bearer_colon", "org/bearer:SENTINEL"),
        ("namespaced_client_secret_equal", "org/client-secret=SENTINEL"),
        ("namespaced_private_key_colon", "org/private_key:SENTINEL"),
        ("nested_colon_token", "org:tool:token:SENTINEL"),
        ("nested_colon_api_key", "id:scope:api_key:SENTINEL"),
    ],
)
def test_recall_provenance_identifier_fields_fail_closed_by_shape(
    field: str, case: str, unsafe: str
) -> None:
    provenance = {"source_agent": "codex"} if field == "run_id" else {}
    provenance[field] = unsafe
    entry = {"provenance": provenance}

    projected = P.project_recall_provenance(entry)
    rendered = repr(projected)

    assert projected.get(field) != unsafe, case
    assert "SENTINEL" not in rendered
    if field == "source_agent":
        assert "source_agent" not in projected
    else:
        assert projected == {"source_agent": "codex"}


@pytest.mark.parametrize("field", ["anchor_ref", "anchor_project_id"])
@pytest.mark.parametrize(
    ("case", "unsafe"),
    [
        ("path", "C:/Users/SENTINEL/secret"),
        ("newline", "dep:jest\nSENTINEL"),
        ("free_text", "free text SENTINEL"),
        ("api_key_label", "api_key=SENTINEL"),
        ("bearer_label", "Authorization=Bearer_SENTINEL"),
        ("token_colon", "token:SENTINEL"),
        ("api_key_colon", "api_key:SENTINEL"),
        ("password_colon", "password:SENTINEL"),
        ("secret_colon", "secret:SENTINEL"),
        ("secret_key_colon", "secret_key:SENTINEL"),
        ("access_key_colon", "access_key:SENTINEL"),
        ("credential_colon", "credential:SENTINEL"),
        ("credentials_colon", "credentials:SENTINEL"),
        ("bearer_colon", "bearer:SENTINEL"),
        ("passwd_colon", "passwd:SENTINEL"),
        ("authorization_colon", "Authorization:Bearer_SENTINEL"),
        ("client_secret_colon", "client_secret:SENTINEL"),
        ("private_key_colon", "private-key:SENTINEL"),
        ("namespaced_token_colon", "org/token:SENTINEL"),
        ("namespaced_api_key_colon", "org/api_key:SENTINEL"),
        ("namespaced_secret_key_colon", "org/secret_key:SENTINEL"),
        ("namespaced_access_key_colon", "org/access-key:SENTINEL"),
        ("namespaced_credential_colon", "org/credential:SENTINEL"),
        ("namespaced_bearer_colon", "org/bearer:SENTINEL"),
        ("namespaced_client_secret_equal", "org/client-secret=SENTINEL"),
        ("namespaced_private_key_colon", "org/private_key:SENTINEL"),
        ("nested_colon_token", "org:tool:token:SENTINEL"),
        ("nested_colon_api_key", "id:scope:api_key:SENTINEL"),
    ],
)
def test_trust_reference_fields_fail_closed_by_shape(
    field: str, case: str, unsafe: str
) -> None:
    provenance = {
        "confirmation_source": "anchor",
        "anchor_ref": "dep:jest",
        "anchor_status": "valid",
        "anchor_project_id": "github.com/acme/app",
    }
    provenance[field] = unsafe
    entry = {"provenance": provenance}

    trust = P.project_trust(entry, now=NOW)
    rendered = repr(trust)

    assert "SENTINEL" not in rendered
    if field == "anchor_ref":
        assert "anchor" not in trust
        assert trust.get("anchor_project_id") == "github.com/acme/app"
    else:
        assert trust.get("anchor") == "dep:jest"
        assert "anchor_project_id" not in trust


@pytest.mark.parametrize(
    "unsafe",
    [
        "dep:token:SENTINEL",
        "dep:api_key:SENTINEL",
        "dep:password:SENTINEL",
        "dep:secret_key:SENTINEL",
        "dep:access-key:SENTINEL",
        "dep:credential:SENTINEL",
        "dep:credentials:SENTINEL",
        "dep:bearer:SENTINEL",
        "dep:passwd:SENTINEL",
        "github:Authorization:Bearer_SENTINEL",
        "file:client_secret=SENTINEL",
        "file:private-key:SENTINEL",
    ],
)
def test_trust_anchor_ref_credential_label_variants_fail_closed(unsafe: str) -> None:
    entry = {
        "provenance": {
            "confirmation_source": "anchor",
            "anchor_ref": unsafe,
            "anchor_status": "valid",
            "anchor_project_id": "github.com/acme/app",
        }
    }

    trust = P.project_trust(entry, now=NOW)
    rendered = repr(trust)

    assert "anchor" not in trust
    assert trust.get("anchor_project_id") == "github.com/acme/app"
    assert "SENTINEL" not in rendered


def test_projection_helpers_do_not_mutate_inputs() -> None:
    entry = {
        "summary": "keep original",
        "labeling": {
            "source_kind": "agent",
            "annotation_quality": "partial",
            "validation_state": "unreviewed",
            "signals": ["has_source_agent"],
        },
        "provenance": {
            "source_agent": "codex",
            "run_id": "run-1",
            "confirmation_source": "anchor",
            "anchor_ref": "dep:react",
            "anchor_status": "valid",
        },
    }
    before = deepcopy(entry)

    P.project_recall_provenance(entry)
    P.project_labeling(entry)
    P.project_trust(entry, now=NOW)

    assert entry == before


def test_provenance_module_imports_stdlib_only() -> None:
    tree = ast.parse(Path(P.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("piia_engram")
            assert node.level == 0
