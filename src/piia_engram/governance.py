"""a0 — Agent Access Governance (scaffold).

The enforcement arm of Engram's "neutral custodian" stance: every time an AI
tool (agent) asks Engram for context, this layer decides *what it may see*,
*emits a disclosure receipt* (who/why/what/excluded), and *appends a
tamper-evident audit event*. The user can inspect and revoke.

DESIGN NOTES / honest boundaries (see design doc §1.a):
- This is a **local-first governance boundary, NOT a hardened security
  sandbox.** Agent identity over MCP stdio is self-reported, so a hostile
  local process can claim any client_type. Known clients should be bound via
  a capability token + a first-run trust handshake (later); unknown agents
  default to the most restrictive tier here.
- **Revocation is forward-only.** It stops *future* disclosure; it cannot
  recall context already returned to a model.
- This module is standalone and NOT yet wired into ``search_knowledge`` /
  ``get_*`` — that cutover is the next increment, kept separate so the live
  read path is untouched while this is validated (self-review + Codex +
  enforcement adversarial tests).

Scope of THIS scaffold: trust-level classification, the sensitivity gate,
the disclosure receipt, and an append-only hash-chained ledger — all pure /
file-backed and fully testable without touching the Engram instance.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import portalocker

# ---------------------------------------------------------------------------
# Sensitivity model
# ---------------------------------------------------------------------------

# Ordered low → high. An item's sensitivity must be <= a trust level's ceiling
# to be disclosed. Default for an unlabeled item is "work" (fail toward NOT
# leaking: unlabeled is treated as work, never as public).
SENSITIVITY_ORDER: dict[str, int] = {"public": 0, "work": 1, "private": 2, "secret": 3}
DEFAULT_SENSITIVITY = "work"


def _sens_rank(value: str) -> int:
    # Unknown labels are treated as the *most* sensitive — fail closed.
    return SENSITIVITY_ORDER.get(str(value).lower(), max(SENSITIVITY_ORDER.values()))


# ---------------------------------------------------------------------------
# Trust levels (3 presets — auto-assigned, no user config in v1)
# ---------------------------------------------------------------------------

# write: "no" | "proposed_only" | "verified"
TRUST_LEVELS: dict[str, dict[str, Any]] = {
    # The user themselves (CLI / doctor / self queries): can see everything,
    # but access is still recorded.
    "private-self": {"max_sensitivity": "secret", "read": True, "write": "verified"},
    # Primary local coding agents (Claude Code / Codex / Cursor / Windsurf):
    # read public + work, NEVER private/secret; may *propose* write-backs.
    "trusted-local": {"max_sensitivity": "work", "read": True, "write": "proposed_only"},
    # Unknown / transient / web agents: read-only, public only.
    "read-only-external": {"max_sensitivity": "public", "read": True, "write": "no"},
}
DEFAULT_TRUST_LEVEL = "read-only-external"  # fail closed for unknown agents

# Maps a self-reported client type to a default trust level. Unknown → most
# restrictive. (Binding identity cryptographically is a later increment.)
_KNOWN_LOCAL_CLIENTS = frozenset(
    {"claude_code", "claude-code", "codex", "cursor", "windsurf", "gemini_cli", "gemini-cli"}
)
_SELF_CLIENTS = frozenset({"self", "cli", "engram", "doctor"})


def classify_agent(client_type: str | None) -> str:
    """Assign a default trust level from a self-reported client type."""
    c = (client_type or "").strip().lower()
    if c in _SELF_CLIENTS:
        return "private-self"
    if c in _KNOWN_LOCAL_CLIENTS:
        return "trusted-local"
    return DEFAULT_TRUST_LEVEL


# ---------------------------------------------------------------------------
# Disclosure gate
# ---------------------------------------------------------------------------


def gate(
    items: Iterable[dict],
    trust_level: str,
    *,
    agent_id: str = "",
    client_type: str = "",
    declared_task: str = "",
    revoked: bool = False,
) -> tuple[list[dict], dict]:
    """Filter ``items`` for a trust level and build a disclosure receipt.

    Returns ``(allowed_items, receipt)``. An item is excluded if its
    ``sensitivity`` exceeds the trust level's ceiling. ``revoked`` agents get
    nothing. Unknown trust levels fall back to the most restrictive.

    The gate is the recall guarantee's safety net: it must NEVER return an
    item above the ceiling. Unlabeled items default to "work" (not public).
    """
    level = TRUST_LEVELS.get(trust_level, TRUST_LEVELS[DEFAULT_TRUST_LEVEL])
    ceiling = _sens_rank(level["max_sensitivity"])

    allowed: list[dict] = []
    excluded_by_sensitivity = 0
    by_type_allowed: dict[str, int] = {}

    if not revoked and level.get("read"):
        for it in items:
            sens = it.get("sensitivity", DEFAULT_SENSITIVITY)
            if _sens_rank(sens) > ceiling:
                excluded_by_sensitivity += 1
                continue
            allowed.append(it)
            t = str(it.get("type") or it.get("_type") or "item")
            by_type_allowed[t] = by_type_allowed.get(t, 0) + 1

    receipt = {
        "receipt_id": "ctx_" + uuid.uuid4().hex[:12],
        "ts": datetime.now().replace(microsecond=0).isoformat(),
        "agent_id": agent_id,
        "client_type": client_type,
        "trust_level": trust_level if trust_level in TRUST_LEVELS else DEFAULT_TRUST_LEVEL,
        "declared_task": declared_task[:200],
        "max_sensitivity": level["max_sensitivity"],
        "returned_count": len(allowed),
        "returned_by_type": by_type_allowed,
        "excluded_by_sensitivity": excluded_by_sensitivity,
        "revoked": bool(revoked),
    }
    return allowed, receipt


# ---------------------------------------------------------------------------
# Append-only, hash-chained governance ledger
# ---------------------------------------------------------------------------


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class LedgerCorruptionError(RuntimeError):
    """The ledger tail is unreadable — append refuses (fail-closed) rather
    than silently extending a broken chain."""


def _record_digest(seq: int, ts: str, prev_hash: str, event: dict) -> str:
    """Hash over the FULL record body (seq + ts + prev_hash + event),
    excluding only the ``hash`` field itself — so tampering with ANY of
    them (incl. the timestamp) is detected."""
    body = {"seq": seq, "ts": ts, "prev_hash": prev_hash, "event": event}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


class GovernanceLedger:
    """Append-only, tamper-evident disclosure ledger (JSONL + hash chain).

    Each line: ``{seq, ts, prev_hash, hash, event}`` where
    ``hash = sha256(prev_hash + seq + canonical(event))``. ``verify()`` re-walks
    the chain and reports the first break — enough to detect ordinary
    corruption / overwrite / reordering (not a defense against a malicious
    root user, which is out of scope for a local-first tool).

    JSON knowledge stays the source of truth; THIS ledger is the governance
    audit source and is deliberately append-only (never rewritten in place).
    """

    GENESIS = "GENESIS"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _last(self) -> tuple[int, str]:
        """Return (last_seq, last_hash) or (-1, GENESIS) if empty/missing.

        Raises LedgerCorruptionError if the tail line is unreadable — the
        caller must NOT append onto a broken chain (fail-closed)."""
        if not self.path.is_file():
            return -1, self.GENESIS
        last_line = ""
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line
        if not last_line:
            return -1, self.GENESIS
        try:
            rec = json.loads(last_line)
            return int(rec["seq"]), str(rec["hash"])
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            raise LedgerCorruptionError(
                f"governance ledger tail unreadable ({self.path.name}); "
                f"refusing to append onto a broken chain: {exc}"
            ) from exc

    def append(self, event: dict) -> dict:
        """Append one event under an exclusive lock; returns the record.

        The lock makes ``read-last → compute → write`` atomic across
        processes (multiple AI tools may write concurrently), preventing
        duplicate seq / broken chains. Raises LedgerCorruptionError if the
        existing tail is unreadable (fail-closed)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.parent / ".engram-governance-ledger.lock"
        try:
            with portalocker.Lock(lock_path, "a", timeout=5):
                last_seq, prev_hash = self._last()
                seq = last_seq + 1
                ts = datetime.now().replace(microsecond=0).isoformat()
                digest = _record_digest(seq, ts, prev_hash, event)
                rec = {"seq": seq, "ts": ts, "prev_hash": prev_hash,
                       "hash": digest, "event": event}
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                return rec
        except portalocker.LockException as exc:
            raise RuntimeError(
                f"governance ledger lock timeout (5s): {self.path.name}"
            ) from exc

    def verify(self) -> tuple[bool, str]:
        """Re-walk the chain. Returns (ok, message)."""
        if not self.path.is_file():
            return True, "empty ledger"
        prev_hash = self.GENESIS
        expected_seq = 0
        with open(self.path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    return False, f"line {lineno}: not valid JSON"
                if rec.get("seq") != expected_seq:
                    return False, f"line {lineno}: seq gap (got {rec.get('seq')}, want {expected_seq})"
                if rec.get("prev_hash") != prev_hash:
                    return False, f"line {lineno}: prev_hash mismatch (chain broken)"
                recomputed = _record_digest(
                    rec.get("seq"), rec.get("ts"), rec.get("prev_hash"), rec.get("event")
                )
                if recomputed != rec.get("hash"):
                    return False, f"line {lineno}: hash mismatch (record tampered, incl. ts)"
                prev_hash = rec["hash"]
                expected_seq += 1
        return True, f"ok ({expected_seq} events)"

    def records(self) -> list[dict]:
        if not self.path.is_file():
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    out.append(json.loads(line))
        return out


def default_ledger_path(root: str | Path | None = None) -> Path:
    base = Path(root) if root else Path(os.environ.get("ENGRAM_DIR", str(Path.home() / ".engram")))
    return base / "governance_ledger.jsonl"
