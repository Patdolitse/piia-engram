"""Content-free runtime capability fingerprint and compatibility handshake."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Iterable

from .storage import SCHEMA_VERSION
from .tool_surface import mcp_surface_counts


CAPABILITY_SCHEMA = "engram_runtime_capabilities.v1"
MCP_SURFACE = mcp_surface_counts()
CONTRACTS = {
    "knowledge_store": f"knowledge_store.v{SCHEMA_VERSION}",
    "project_identity": "canonical_project_identity.v1",
    "project_snapshot": "project_snapshot.v2",
    "project_resume_pack": "project_resume_pack.v1",
    "session_digest": "session_digest.v1",
    "wrap_up_operation": "wrap_up_operation.v1",
    "mcp_schema": "mcp_tool_schema.v1",
    "read_path": "zero_write_read_path.v1",
}
CAPABILITY_CODES = (
    "exact_project_scope",
    "git_common_dir_worktree_identity",
    "legacy_project_alias_read_only",
    "manual_review_needed_promotion",
    "project_checkpoint_revision",
    "project_scoped_reconcile",
    "resume_freshness_arbitration",
    "wrap_up_idempotent_recovery",
    "wrap_up_status_by_idempotency_key",
    "zero_write_read_diagnostics",
)


def _runtime_version() -> str:
    try:
        from . import __version__

        if str(__version__).strip():
            return str(__version__)
    except Exception:
        pass
    try:
        return version("piia-engram")
    except PackageNotFoundError:
        return "unknown"


def _fingerprinted_contract() -> dict[str, Any]:
    return {
        "schema": CAPABILITY_SCHEMA,
        "contracts": dict(sorted(CONTRACTS.items())),
        "capability_codes": sorted(CAPABILITY_CODES),
        "mcp_surface": dict(MCP_SURFACE),
    }


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def get_runtime_capabilities() -> dict[str, Any]:
    """Return a deterministic manifest containing no user or project data."""
    contract = _fingerprinted_contract()
    return {
        **deepcopy(contract),
        "fingerprint": _fingerprint(contract),
        "runtime_version": _runtime_version(),
        "content_policy": "metadata_only_no_user_content",
    }


def check_runtime_compatibility(
    *,
    required_codes: Iterable[str] = (),
    required_contracts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compare caller requirements with the runtime's stable capability codes."""
    manifest = get_runtime_capabilities()
    available = set(manifest["capability_codes"])
    requested_codes = sorted({
        str(code).strip()
        for code in required_codes
        if str(code).strip()
    })
    missing_codes = [code for code in requested_codes if code not in available]

    expected_contracts = {
        str(key): str(value)
        for key, value in (required_contracts or {}).items()
    }
    actual_contracts = manifest["contracts"]
    contract_mismatches = {
        key: {
            "required": expected,
            "actual": actual_contracts.get(key, "missing"),
        }
        for key, expected in sorted(expected_contracts.items())
        if actual_contracts.get(key) != expected
    }
    compatible = not missing_codes and not contract_mismatches
    return {
        "schema": "engram_runtime_compatibility.v1",
        "compatible": compatible,
        "fingerprint": manifest["fingerprint"],
        "runtime_version": manifest["runtime_version"],
        "missing_codes": missing_codes,
        "contract_mismatches": contract_mismatches,
    }
