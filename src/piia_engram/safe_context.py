"""Safe Context / Lockdown pure transformer.

Safe mode redacts obvious secrets and enforces a coarse character budget on an
already-built context payload. Lockdown mode withholds all body-bearing sections
and returns counts/metadata only. This module is side-effect free and does not
decide who may receive the result; callers still use the governance layer.
"""

from __future__ import annotations

import json
from typing import Any

from .export_redaction import redact_export_text


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_export_text(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    return value


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _trim_payload(payload: dict[str, Any], max_chars: int) -> tuple[dict[str, Any], bool]:
    if _json_size(payload) <= max_chars:
        return payload, False
    out = dict(payload)
    knowledge = out.get("knowledge")
    trimmed = False
    if isinstance(knowledge, list):
        kept: list[Any] = []
        for item in knowledge:
            candidate = dict(out)
            candidate["knowledge"] = kept + [item]
            if _json_size(candidate) > max_chars:
                trimmed = True
                break
            kept.append(item)
        out["knowledge"] = kept
    return out, trimmed or _json_size(out) > max_chars


def _enforce_payload_budget(payload: dict[str, Any], max_chars: int) -> tuple[dict[str, Any], bool]:
    """Best-effort final budget pass after metadata has been attached."""
    if _json_size(payload) <= max_chars:
        return payload, False
    out = dict(payload)
    trimmed = False
    knowledge = out.get("knowledge")
    if isinstance(knowledge, list):
        kept = list(knowledge)
        while kept and _json_size(out) > max_chars:
            kept.pop()
            out["knowledge"] = kept
            trimmed = True
    for key in ("recent_activity", "identity"):
        if _json_size(out) <= max_chars:
            break
        if key in out:
            out[key] = {}
            trimmed = True
    return out, trimmed or _json_size(out) > max_chars


def build_safe_context(
    payload: dict[str, Any],
    *,
    max_chars: int = 8000,
    lockdown: bool = False,
) -> dict[str, Any]:
    """Return a safe/lockdown view of ``payload`` without mutating it."""
    if not isinstance(payload, dict):
        payload = {}
    if lockdown:
        knowledge = payload.get("knowledge")
        withheld = len(knowledge) if isinstance(knowledge, list) else 0
        meta = dict(payload.get("meta", {})) if isinstance(payload.get("meta"), dict) else {}
        meta["safe_context"] = {
            "mode": "lockdown",
            "knowledge_items_withheld": withheld,
            "trimmed": False,
        }
        return {"identity": {}, "recent_activity": {}, "knowledge": [], "meta": meta}

    safe = _redact(payload)
    if not isinstance(safe, dict):
        safe = {}
    trimmed_payload, trimmed = _trim_payload(safe, max(0, int(max_chars)))
    meta = dict(trimmed_payload.get("meta", {})) if isinstance(trimmed_payload.get("meta"), dict) else {}
    meta["safe_context"] = {
        "mode": "safe",
        "max_chars": max(0, int(max_chars)),
        "estimated_chars": _json_size(trimmed_payload),
        "trimmed": bool(trimmed),
    }
    trimmed_payload["meta"] = meta
    trimmed_payload, final_trimmed = _enforce_payload_budget(
        trimmed_payload,
        max(0, int(max_chars)),
    )
    if final_trimmed:
        meta = dict(trimmed_payload.get("meta", {})) if isinstance(trimmed_payload.get("meta"), dict) else {}
        safe_meta = dict(meta.get("safe_context", {})) if isinstance(meta.get("safe_context"), dict) else {}
        safe_meta["trimmed"] = True
        safe_meta["estimated_chars"] = _json_size(trimmed_payload)
        meta["safe_context"] = safe_meta
        trimmed_payload["meta"] = meta
    return trimmed_payload
