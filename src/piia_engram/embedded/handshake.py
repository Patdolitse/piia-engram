"""Contract handshake: the host states what it needs, the facade answers.

Fail-closed by construction. There is no partial match, no "close enough"
version comparison, and no downgrade path: either every requirement the host
declared is satisfied exactly, or the result is a mismatch carrying the specific
reasons. Callers are expected to refuse to proceed on mismatch.
"""

from __future__ import annotations

from typing import Any, Iterable

from .contract import (
    FACADE_CONTRACT_VERSION,
    RETRIEVAL_MODES,
    SNAPSHOT_SCHEMA,
    capability_witness,
)


class FacadeHandshakeError(RuntimeError):
    """Raised by :func:`require_compatible` when the handshake does not match."""

    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__(
            "embedded facade handshake mismatch: " + ",".join(result.get("problems", []))
        )


def handshake(
    *,
    required_contract: str | None = None,
    required_snapshot_schema: str | None = None,
    required_retrieval_modes: Iterable[str] = (),
) -> dict[str, Any]:
    """Compare host requirements against this facade.

    Every argument is optional, but a host that declares nothing gets no
    guarantee beyond "a facade exists" — declaring the contract version is the
    intended usage.

    Returns a mapping with ``match`` (bool), ``problems`` (list of stable codes),
    and the facade's own identifiers so a mismatch is diagnosable without a
    second call.
    """
    problems: list[str] = []

    if required_contract is not None and required_contract != FACADE_CONTRACT_VERSION:
        problems.append("facade_contract_mismatch")

    if (
        required_snapshot_schema is not None
        and required_snapshot_schema != SNAPSHOT_SCHEMA
    ):
        problems.append("snapshot_schema_mismatch")

    requested_modes = sorted({str(m).strip() for m in required_retrieval_modes if str(m).strip()})
    missing_modes = [m for m in requested_modes if m not in RETRIEVAL_MODES]
    if missing_modes:
        problems.append("retrieval_mode_unsupported:" + ",".join(missing_modes))

    return {
        "match": not problems,
        "problems": problems,
        "facade_contract": FACADE_CONTRACT_VERSION,
        "snapshot_schema": SNAPSHOT_SCHEMA,
        "retrieval_modes": list(RETRIEVAL_MODES),
        "requested": {
            "contract": required_contract,
            "snapshot_schema": required_snapshot_schema,
            "retrieval_modes": requested_modes,
        },
        "witness": capability_witness(),
    }


def require_compatible(
    *,
    required_contract: str | None = None,
    required_snapshot_schema: str | None = None,
    required_retrieval_modes: Iterable[str] = (),
) -> dict[str, Any]:
    """Handshake and raise on mismatch, for hosts that want fail-closed control flow."""
    result = handshake(
        required_contract=required_contract,
        required_snapshot_schema=required_snapshot_schema,
        required_retrieval_modes=required_retrieval_modes,
    )
    if not result["match"]:
        raise FacadeHandshakeError(result)
    return result
