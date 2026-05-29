"""a0 runtime — wire the governance gate into the live agent read path.

OFF by default. When ``ENGRAM_GOVERNANCE`` is unset/false, the read path is
left completely alone: the MCP tools never call into this module, so callers
receive their items byte-identical to pre-governance Engram. When ON, each
agent-facing read is:

  1. annotated with a sensitivity level (``sensitivity.classify_item``),
  2. filtered against the calling agent's trust ceiling (``governance.gate``),
     returning the ORIGINAL item objects minus the excluded ones — no shape
     change and no added field; a governed result differs from an ungoverned
     one only by *omission*, and
  3. recorded as a tamper-evident disclosure receipt in the ledger
     (best-effort: filtering is the hard guarantee, so a failed or corrupt
     audit log must NEVER block a correctly-filtered read).

Identity is self-reported over MCP stdio (see governance.py honest
boundaries): the MCP layer passes the engram root plus a client type from
``ENGRAM_CLIENT_TYPE``. Unknown/empty client → most restrictive tier (fail
closed). An explicit per-agent grant (``GrantStore``) overrides the
auto-classification.

This module is side-effect-explicit (root is always passed in), so it is
fully testable without standing up the MCP server.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Iterable

from .governance import (
    DEFAULT_TRUST_LEVEL,
    TRUST_LEVELS,
    GovernanceLedger,
    LedgerCorruptionError,
    default_ledger_path,
    gate,
)
from .governance_store import GrantStore
from .sensitivity import annotate_items

_TRUTHY = ("1", "true", "yes", "on")


def governance_enabled() -> bool:
    """True iff ``ENGRAM_GOVERNANCE`` is set truthy. OFF (default) means the
    read path stays byte-identical — callers must guard every governance call
    with this so the disabled path is a true no-op."""
    return os.environ.get("ENGRAM_GOVERNANCE", "").strip().lower() in _TRUTHY


def current_client_type() -> str:
    """Self-reported calling client, from ``ENGRAM_CLIENT_TYPE`` (may be empty,
    in which case the agent fails closed to the most restrictive tier)."""
    return os.environ.get("ENGRAM_CLIENT_TYPE", "").strip()


def resolve_caller(root, *, agent_id: str = "", client_type: str | None = None):
    """Resolve ``(agent_id, trust_level, revoked)`` for the calling agent.

    An explicit ``GrantStore`` binding (by agent_id) wins; otherwise the trust
    level is auto-classified from the self-reported client type (fail-closed
    for unknown — see ``governance.classify_agent``). ``client_type=None``
    reads ``ENGRAM_CLIENT_TYPE``.
    """
    ct = current_client_type() if client_type is None else (client_type or "")
    store = GrantStore(root)
    aid = str(agent_id or ct or "anonymous")
    trust = store.trust_level_for(aid, ct or None)
    revoked = store.is_revoked(aid)
    return aid, trust, revoked


def _filter_keep_originals(items, trust_level, *, revoked, restricted_fields):
    """Annotate + gate, but return the ORIGINAL objects (filtered).

    The gate decision is delegated wholesale to ``governance.gate`` (the single
    enforcement source of truth) operating on disposable annotated copies; we
    then map the allowed copies back to the originals by object identity, so
    the returned items carry no injected ``sensitivity`` field and are shape-
    identical to the ungoverned ones — the result differs only by omission.

    Identity mapping is safe: ``items``, ``annotated`` and ``allowed_copies``
    are all alive here, so their ``id()`` values are distinct, and every
    allowed copy is an element of ``annotated`` (1:1 with ``items``).
    """
    items = list(items)
    annotated = annotate_items(items, restricted_fields)  # 1:1 disposable copies
    allowed_copies, receipt = gate(annotated, trust_level, revoked=revoked)
    allowed_ids = {id(c) for c in allowed_copies}
    allowed = [orig for orig, ann in zip(items, annotated) if id(ann) in allowed_ids]
    return allowed, receipt


def _log_disclosure(root, receipt) -> tuple[bool, str]:
    """Best-effort append of a disclosure event to the governance ledger.

    Filtering is the hard leak-prevention guarantee and has already happened by
    the time we get here; a failed or corrupt audit log must NOT block a
    correctly-filtered read. Returns ``(logged_ok, error_message)`` so the
    caller can surface the failure in the receipt without crashing retrieval.
    """
    try:
        GovernanceLedger(default_ledger_path(root)).append({"kind": "disclosure", **receipt})
        return True, ""
    except (LedgerCorruptionError, OSError, RuntimeError, ValueError) as exc:
        return False, str(exc)


def govern_buckets(
    root,
    buckets: dict,
    *,
    tool: str,
    agent_id: str = "",
    client_type: str | None = None,
    declared_task: str = "",
    restricted_fields: Iterable[str] = (),
) -> tuple[dict, dict]:
    """Filter each named list in ``buckets`` for the caller's trust level and
    log ONE disclosure receipt for the call.

    ``buckets`` maps a name (e.g. ``"lessons"``) to a ``list[dict]``. Non-list
    values are passed through untouched. Returns ``(filtered_buckets, receipt)``
    where ``filtered_buckets`` preserves key order and the original item
    objects (minus excluded ones).
    """
    aid, trust, revoked = resolve_caller(root, agent_id=agent_id, client_type=client_type)
    restricted = tuple(restricted_fields)
    out: dict = {}
    returned_by_type: dict[str, int] = {}
    excluded_sens = 0
    excluded_malformed = 0
    for name, items in buckets.items():
        if isinstance(items, list):
            allowed, rc = _filter_keep_originals(
                items, trust, revoked=revoked, restricted_fields=restricted
            )
            out[name] = allowed
            returned_by_type[name] = len(allowed)
            excluded_sens += rc["excluded_by_sensitivity"]
            excluded_malformed += rc["excluded_malformed"]
        else:
            out[name] = items
    level = TRUST_LEVELS.get(trust, TRUST_LEVELS[DEFAULT_TRUST_LEVEL])
    ct = current_client_type() if client_type is None else (client_type or "")
    receipt = {
        "receipt_id": "ctx_" + uuid.uuid4().hex[:12],
        "ts": datetime.now().replace(microsecond=0).isoformat(),
        "tool": tool,
        "agent_id": aid,
        "client_type": ct,
        "trust_level": trust if trust in TRUST_LEVELS else DEFAULT_TRUST_LEVEL,
        "declared_task": declared_task[:200],
        "max_sensitivity": level["max_sensitivity"],
        "returned_count": sum(returned_by_type.values()),
        "returned_by_type": returned_by_type,
        "excluded_by_sensitivity": excluded_sens,
        "excluded_malformed": excluded_malformed,
        "revoked": bool(revoked),
    }
    logged, err = _log_disclosure(root, receipt)
    receipt["audit_logged"] = logged
    if err:
        receipt["audit_error"] = err
    return out, receipt


def govern_list(root, items, *, tool: str, **kw) -> tuple[list, dict]:
    """Single-list convenience over :func:`govern_buckets`. Returns the
    filtered list (original objects, minus excluded) plus the receipt."""
    out, receipt = govern_buckets(root, {"items": items}, tool=tool, **kw)
    return out["items"], receipt
