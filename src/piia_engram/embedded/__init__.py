"""Embedded host facade (phase 1) — contract-admitted, zero-write memory reads.

An embedding host admits this runtime by contract, not by version string:

    from piia_engram.embedded import require_compatible, retrieve_task_context_snapshot

    require_compatible(required_contract="engram.embedded_host_facade.v1")
    snapshot = retrieve_task_context_snapshot(
        engram_root=..., project_folder=..., project_id=..., task_id=...,
        task_class="software_development", objective="...", limit=8,
    )

Phase 1 exposes no store-mutating path of any kind. The one file-writing
helper re-exported here, ``write_capability_witness``, writes a capability
witness to a caller-chosen path and never touches the Engram store. The
stability promise covers only the names re-exported here; everything else in
this package is implementation detail, and the product version may move
underneath a stable facade contract.
"""

from __future__ import annotations

from .contract import (
    FACADE_CONTRACT_VERSION,
    RETRIEVAL_MODES,
    SNAPSHOT_SCHEMA,
    WITNESS_SCHEMA,
    canonical_hash,
    capability_witness,
    verify_witness,
    write_capability_witness,
)
from .handshake import FacadeHandshakeError, handshake, require_compatible
from .snapshot import (
    MAX_ITEMS,
    FacadeContextError,
    retrieve_task_context_snapshot,
    validate_snapshot,
)

__all__ = [
    "FACADE_CONTRACT_VERSION",
    "SNAPSHOT_SCHEMA",
    "WITNESS_SCHEMA",
    "RETRIEVAL_MODES",
    "MAX_ITEMS",
    "canonical_hash",
    "capability_witness",
    "verify_witness",
    "write_capability_witness",
    "handshake",
    "require_compatible",
    "FacadeHandshakeError",
    "retrieve_task_context_snapshot",
    "validate_snapshot",
    "FacadeContextError",
]
