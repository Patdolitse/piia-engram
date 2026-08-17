"""Facade contract constants, canonical hashing, and the capability witness.

The witness is what lets an embedding host admit this runtime by *contract*
instead of by version string. It is content-free: schema names, capability
codes, counts, and source-file digests only — never store content, never local
paths.

Versioning rule: ``FACADE_CONTRACT_VERSION`` and ``SNAPSHOT_SCHEMA`` evolve
independently of the product version. A patch/minor product release that does
not change the facade surface leaves both untouched, so a host pinned to a
contract keeps matching across product upgrades.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

FACADE_CONTRACT_VERSION = "engram.embedded_host_facade.v1"
SNAPSHOT_SCHEMA = "engram.embedded_task_context_snapshot.v1"
WITNESS_SCHEMA = "engram.embedded_capability_witness.v1"

# Retrieval modes this phase supports. Keyword-only, no persistent index: the
# hybrid/vector index is excluded because it is neither byte-stable across
# environments nor writable-free (materialising it would write to the store).
RETRIEVAL_MODES = ("keyword_no_persistent_index",)

# Phase 1 exposes no store-mutating path. The one file-writing helper this
# package re-exports, ``write_capability_witness``, writes a capability
# witness to a caller-chosen path - it never writes to the Engram store.
READ_ONLY_GUARANTEE = {
    "phase": 1,
    "store_writes": "none",
    "index_materialisation": "none",
    "network": "none",
    "subprocess": "none",
    "write_paths_exposed": [
        "write_capability_witness: caller-chosen witness file; never the store",
    ],
}

# Source files whose bytes define facade behaviour. A host can re-hash these to
# detect that the runtime it loaded is the one the witness describes.
WITNESSED_SOURCES = (
    "embedded/__init__.py",
    "embedded/contract.py",
    "embedded/handshake.py",
    "embedded/snapshot.py",
)


def canonical_hash(value: Any) -> str:
    """Hash a JSON value exactly the way the consumer contract does.

    ``ensure_ascii=True``, sorted keys, tight separators and ``allow_nan=False``
    are load-bearing: the host recomputes this hash to verify a snapshot, so any
    divergence here silently breaks verification.
    """
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _runtime_version() -> str:
    try:
        from .. import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive
        return "unknown"


def _source_digests() -> dict[str, str]:
    base = Path(__file__).resolve().parent.parent
    digests: dict[str, str] = {}
    for relative in WITNESSED_SOURCES:
        path = base / relative
        try:
            digests[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            digests[relative] = "unreadable"
    return dict(sorted(digests.items()))


def capability_witness() -> dict[str, Any]:
    """Build the machine-verifiable capability witness.

    The returned mapping carries its own hash under ``witness_hash``, computed
    over every other field, so a host can detect tampering or truncation of a
    witness file it was handed out-of-band.
    """
    body: dict[str, Any] = {
        "schema": WITNESS_SCHEMA,
        "facade_contract": FACADE_CONTRACT_VERSION,
        "snapshot_schema": SNAPSHOT_SCHEMA,
        "runtime_version": _runtime_version(),
        "retrieval_modes": list(RETRIEVAL_MODES),
        "read_only_guarantee": dict(READ_ONLY_GUARANTEE),
        "source_digests": _source_digests(),
    }
    body["witness_hash"] = canonical_hash(body)
    return body


def verify_witness(witness: dict[str, Any]) -> tuple[bool, list[str]]:
    """Verify a witness mapping: shape, self-hash, and live source digests.

    Returns ``(ok, problems)``. Fail-closed: any unexpected shape is a problem
    rather than something to interpret generously.
    """
    problems: list[str] = []
    if not isinstance(witness, dict):
        return False, ["witness_not_a_mapping"]

    expected_fields = {
        "schema",
        "facade_contract",
        "snapshot_schema",
        "runtime_version",
        "retrieval_modes",
        "read_only_guarantee",
        "source_digests",
        "witness_hash",
    }
    if set(witness) != expected_fields:
        problems.append("witness_field_set_mismatch")
        return False, problems

    if witness.get("schema") != WITNESS_SCHEMA:
        problems.append("witness_schema_mismatch")

    body = {k: v for k, v in witness.items() if k != "witness_hash"}
    if witness.get("witness_hash") != canonical_hash(body):
        problems.append("witness_self_hash_mismatch")

    live = _source_digests()
    declared = witness.get("source_digests")
    if not isinstance(declared, dict):
        problems.append("witness_source_digests_invalid")
    elif declared != live:
        drifted = sorted(
            name
            for name in set(declared) | set(live)
            if declared.get(name) != live.get(name)
        )
        problems.append("witness_source_digest_drift:" + ",".join(drifted))

    return (not problems), problems


def write_capability_witness(path: str | Path) -> Path:
    """Write the witness to ``path`` as canonical JSON and return the path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    witness = capability_witness()
    target.write_text(
        json.dumps(witness, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return target
