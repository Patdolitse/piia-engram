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
from .sensitivity import VALID_LEVELS, annotate_items
from .storage import DataCorruptionError

_TRUTHY = ("1", "true", "yes", "on")

# The most-privileged tier — the only one allowed to receive an opaque
# whole-knowledge dump (e.g. the rendered export report), which cannot be
# filtered field-by-field.
_PRIVATE_SELF = "private-self"

# Returned in place of a single knowledge item / dump that exceeds the caller's
# trust ceiling. JSON-serializable; carries no knowledge body.
def _withheld_stub(tool: str, trust: str) -> dict:
    return {
        "governance_withheld": True,
        "tool": tool,
        "trust_level": trust,
        "reason": "item sensitivity exceeds caller trust ceiling",
    }


_DUMP_REFUSAL = (
    "【治理层】当前信任档无权读取完整知识导出报告（仅 private-self 可读）。"
    " / Governance: full knowledge export is withheld at the current trust level"
    " (private-self only)."
)


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
    """Resolve ``(agent_id, trust_level, revoked, grant_error)`` for the caller.

    An explicit ``GrantStore`` binding (by agent_id) wins; otherwise the trust
    level is auto-classified from the self-reported client type (fail-closed
    for unknown — see ``governance.classify_agent``). ``client_type=None``
    reads ``ENGRAM_CLIENT_TYPE``.

    Identity resolution is fail-closed: if the grant store is corrupt or
    unreadable, we do NOT raise (that would turn a damaged ``grants.json`` into
    a governed-read DoS). Instead we drop to the most restrictive tier
    (``read-only-external``) and surface the failure as ``grant_error`` so the
    caller can record it in the disclosure receipt.
    """
    ct = current_client_type() if client_type is None else (client_type or "")
    aid = str(agent_id or ct or "anonymous")
    try:
        store = GrantStore(root)
        trust = store.trust_level_for(aid, ct or None)
        revoked = store.is_revoked(aid)
        return aid, trust, revoked, ""
    except (DataCorruptionError, OSError, RuntimeError, ValueError) as exc:
        # Fail closed: most restrictive tier, treat identity as un-revoked so the
        # public-only ceiling still applies and filtering proceeds normally.
        return aid, DEFAULT_TRUST_LEVEL, False, str(exc)


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
    # Fail-close present-but-invalid explicit labels to ``secret``. The
    # sensitivity classifier normalizes an unknown ``sensitivity`` value to the
    # ``work`` default (so it behaves like an unlabeled item); that quietly
    # defeated ``gate``'s unknown-label fail-closed behavior once we annotate
    # before gating. A *present* label that is non-empty but not a valid level
    # is treated as the most sensitive tier; truly unlabeled items keep the
    # ``work`` default.
    for orig, ann in zip(items, annotated):
        if not isinstance(orig, dict) or not isinstance(ann, dict):
            continue
        raw = orig.get("sensitivity")
        if raw is None:
            continue
        norm = str(raw).strip().lower()
        if norm and norm not in VALID_LEVELS:
            ann["sensitivity"] = "secret"
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


def _finalize_receipt(
    root,
    *,
    tool: str,
    aid: str,
    ct: str,
    trust: str,
    declared_task: str,
    revoked,
    returned_by_type: dict,
    excluded_sens: int,
    excluded_malformed: int,
    grant_error: str = "",
) -> dict:
    """Build the one-per-call disclosure receipt and best-effort log it.

    Single source of truth for receipt shape so every governed read path
    (buckets, single list, mixed result, opaque dump) emits an identical
    receipt and exactly one audit record.
    """
    level = TRUST_LEVELS.get(trust, TRUST_LEVELS[DEFAULT_TRUST_LEVEL])
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
    if grant_error:
        receipt["grant_error"] = grant_error
    logged, err = _log_disclosure(root, receipt)
    receipt["audit_logged"] = logged
    if err:
        receipt["audit_error"] = err
    return receipt


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
    aid, trust, revoked, grant_error = resolve_caller(
        root, agent_id=agent_id, client_type=client_type
    )
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
    ct = current_client_type() if client_type is None else (client_type or "")
    receipt = _finalize_receipt(
        root, tool=tool, aid=aid, ct=ct, trust=trust, declared_task=declared_task,
        revoked=revoked, returned_by_type=returned_by_type, excluded_sens=excluded_sens,
        excluded_malformed=excluded_malformed, grant_error=grant_error,
    )
    return out, receipt


def govern_list(root, items, *, tool: str, **kw) -> tuple[list, dict]:
    """Single-list convenience over :func:`govern_buckets`. Returns the
    filtered list (original objects, minus excluded) plus the receipt."""
    out, receipt = govern_buckets(root, {"items": items}, tool=tool, **kw)
    return out["items"], receipt


def govern_result(
    root,
    payload: dict,
    *,
    tool: str,
    list_fields: Iterable[str] = (),
    item_fields: Iterable[str] = (),
    agent_id: str = "",
    client_type: str | None = None,
    declared_task: str = "",
    restricted_fields: Iterable[str] = (),
) -> tuple[dict, dict]:
    """Filter named knowledge fields inside a mixed result dict, ONE receipt.

    Unlike :func:`govern_buckets` (which filters *every* list value), this
    targets only the named fields so sibling scalars / non-knowledge lists
    (e.g. ``recommended_domains: list[str]``) are passed through untouched:

    * ``list_fields`` — keys whose value is a ``list[dict]`` of knowledge items;
      each is filtered against the caller's trust ceiling.
    * ``item_fields`` — keys whose value is a single knowledge-item dict; if it
      exceeds the ceiling it is replaced with a withheld stub (so a low-trust
      caller cannot read a secret item just by echoing back its id).

    Non-dict ``payload`` is returned untouched. The returned dict is a shallow
    copy (original key order preserved); governed fields hold the ORIGINAL item
    objects minus excluded ones.
    """
    aid, trust, revoked, grant_error = resolve_caller(
        root, agent_id=agent_id, client_type=client_type
    )
    restricted = tuple(restricted_fields)
    returned_by_type: dict[str, int] = {}
    excluded_sens = 0
    excluded_malformed = 0
    out = dict(payload) if isinstance(payload, dict) else payload
    if isinstance(payload, dict):
        for field in list_fields:
            items = payload.get(field)
            if isinstance(items, list):
                allowed, rc = _filter_keep_originals(
                    items, trust, revoked=revoked, restricted_fields=restricted
                )
                out[field] = allowed
                returned_by_type[field] = len(allowed)
                excluded_sens += rc["excluded_by_sensitivity"]
                excluded_malformed += rc["excluded_malformed"]
        for field in item_fields:
            item = payload.get(field)
            if isinstance(item, dict):
                allowed, rc = _filter_keep_originals(
                    [item], trust, revoked=revoked, restricted_fields=restricted
                )
                if allowed:
                    out[field] = allowed[0]
                    returned_by_type[field] = 1
                else:
                    out[field] = _withheld_stub(tool, trust)
                    returned_by_type[field] = 0
                excluded_sens += rc["excluded_by_sensitivity"]
                excluded_malformed += rc["excluded_malformed"]
    ct = current_client_type() if client_type is None else (client_type or "")
    receipt = _finalize_receipt(
        root, tool=tool, aid=aid, ct=ct, trust=trust, declared_task=declared_task,
        revoked=revoked, returned_by_type=returned_by_type, excluded_sens=excluded_sens,
        excluded_malformed=excluded_malformed, grant_error=grant_error,
    )
    return out, receipt


def govern_owner_only(
    root,
    payload,
    *,
    tool: str,
    agent_id: str = "",
    client_type: str | None = None,
    declared_task: str = "",
) -> tuple[object, dict]:
    """Owner-only gate for payloads that cannot be cleanly field-filtered.

    Used for two classes of read output that would otherwise be a bypass:

    * **opaque dumps** — a rendered string bundling many items (export report,
      cold-start context, resume brief): no per-field hook to filter.
    * **deeply-nested / derived aggregate views** — digests and decision
      threads that embed full items AND label-stripped preview rows several
      levels down, where field-by-field gating is error-prone and the original
      sensitivity label is not propagated to the derived rows.

    All-or-nothing: returned in full ONLY to the ``private-self`` tier (and only
    if not revoked). Every other caller gets a refusal — a string for string
    payloads, a withheld stub for dict payloads. Granular item tools
    (``get_lessons`` …) remain available and properly filtered for lower tiers,
    so this only withholds the *aggregate convenience view*, not the knowledge.
    """
    aid, trust, revoked, grant_error = resolve_caller(
        root, agent_id=agent_id, client_type=client_type
    )
    allowed_full = (not revoked) and trust == _PRIVATE_SELF
    if allowed_full:
        out = payload
    elif isinstance(payload, str):
        out = _DUMP_REFUSAL
    else:
        out = _withheld_stub(tool, trust)
    ct = current_client_type() if client_type is None else (client_type or "")
    receipt = _finalize_receipt(
        root, tool=tool, aid=aid, ct=ct, trust=trust, declared_task=declared_task,
        revoked=revoked, returned_by_type={"_owner_only": 1 if allowed_full else 0},
        excluded_sens=0 if allowed_full else 1, excluded_malformed=0,
        grant_error=grant_error,
    )
    return out, receipt


# ── flag-checked entry points (the single guard MCP read tools call) ─────────
#
# Each ``maybe_*`` is a true no-op when governance is OFF (returns the payload
# unchanged, never entering the governance module), so the disabled read path
# stays byte-identical to pre-governance Engram. When ON, ALL agent-facing
# knowledge-body read tools must route their output through exactly one of
# these — a single ungoverned sibling read tool is a full enforcement bypass
# (Codex round-15 P1). The tool→helper mapping is asserted by the read-tool
# matrix regression test.


def maybe_govern_list(root, items, *, tool: str, **kw):
    """Govern a plain ``list[dict]`` iff the flag is on; else return unchanged."""
    if not governance_enabled():
        return items
    out, _ = govern_list(root, items, tool=tool, **kw)
    return out


def maybe_govern_buckets(root, buckets, *, tool: str, **kw):
    """Govern a pure dict-of-lists iff the flag is on; else return unchanged."""
    if not governance_enabled():
        return buckets
    out, _ = govern_buckets(root, buckets, tool=tool, **kw)
    return out


def maybe_govern_result(root, payload, *, tool: str, list_fields=(), item_fields=(), **kw):
    """Govern named fields of a mixed result dict iff the flag is on."""
    if not governance_enabled():
        return payload
    out, _ = govern_result(
        root, payload, tool=tool, list_fields=list_fields, item_fields=item_fields, **kw
    )
    return out


def maybe_govern_one(root, item, *, tool: str, **kw):
    """Govern a single bare knowledge-item dict iff the flag is on.

    Returns the original item if within the caller's ceiling, else a withheld
    stub. Non-dict input is returned unchanged.
    """
    if not governance_enabled() or not isinstance(item, dict):
        return item
    wrapped, _ = govern_result(root, {"_item": item}, tool=tool, item_fields=("_item",), **kw)
    return wrapped["_item"]


def maybe_govern_owner_only(root, payload, *, tool: str, **kw):
    """Owner-only gate (private-self only) iff the flag is on; else unchanged.

    For opaque dumps and deeply-nested aggregate views that cannot be cleanly
    field-filtered. Returns a string refusal for string payloads, a withheld
    stub for dict payloads.
    """
    if not governance_enabled():
        return payload
    out, _ = govern_owner_only(root, payload, tool=tool, **kw)
    return out


# Backwards-compatible alias: the export report is the canonical string dump.
def maybe_govern_dump(root, text, *, tool: str, **kw):
    """Owner-only gate for an opaque whole-knowledge dump string."""
    return maybe_govern_owner_only(root, text, tool=tool, **kw)
