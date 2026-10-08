"""Agent identity proposals, decided only through the Owner's local review."""
from __future__ import annotations

import json
import uuid
from copy import deepcopy

from . import review_boundary, tombstones, write_provenance
from .storage import (_ALLOWED_PROFILE_FIELDS, _ALLOWED_PREFERENCES_FIELDS,
                      _ALLOWED_TRUST_FIELDS, _ALLOWED_QUALITY_FIELDS,
                      _now_iso, _read_json, _write_json, hold_directory_lock)

FIELDS = {"profile": _ALLOWED_PROFILE_FIELDS, "preferences": _ALLOWED_PREFERENCES_FIELDS,
          "trust_boundaries": _ALLOWED_TRUST_FIELDS, "quality_standards": _ALLOWED_QUALITY_FIELDS,
          "work_style": frozenset({"preferences", "communication"})}
BODY_FIELDS = ("before", "after", "updates")


def _rows(eng) -> list[dict]:
    path = eng._identity_dir / "proposals.json"
    data = _read_json(path) if path.is_file() else []
    if not isinstance(data, list):
        raise ValueError("invalid identity proposal queue")
    rows = deepcopy(data)
    for row in rows:
        payload = row.pop("encrypted_body", None)
        if payload is not None:
            row.update(json.loads(eng._crypto.decrypt(payload, strict=True)))
    return rows


def _save(eng, rows):
    stored = deepcopy(rows)
    if eng._crypto.enabled:
        for row in stored:
            body = {k: row.pop(k) for k in BODY_FIELDS if k in row}
            if body:
                row["encrypted_body"] = eng._crypto.encrypt(json.dumps(body, ensure_ascii=False))
    _write_json(eng._identity_dir / "proposals.json", stored)


def _matches(current, values, missing=()):
    return all(k not in current if k in missing else k in current and current[k] == v
               for k, v in values.items())


class IdentityReviewMixin:
    def get_identity_proposals(self, *, include_decided: bool = False) -> list[dict]:
        """Local review view; pending identity is never used by automatic recall."""
        return [r for r in _rows(self) if include_decided or r.get("status") in {"pending", "applying"}]

    def recover_identity_proposals(self) -> list[dict]:
        """Finish durable approval intents locally; leave conflicting edits intact."""
        return [self.review_identity_proposal(r['id'], 'approve', expected_version=r['version'])
                for r in self.get_identity_proposals() if r.get('status') == 'applying']

    def propose_identity(self, field: str, updates: dict, source_tool: str = "") -> dict:
        """Queue a proposed identity patch; do not change approved identity."""
        if field not in FIELDS or not isinstance(updates, dict):
            return {"error": "invalid_identity_update", "changed": False}
        allowed = FIELDS[field] - {"updated_at", "migrated_from"}
        if not updates or any(k not in allowed for k in updates):
            return {"error": "invalid_identity_keys", "changed": False}
        updates = self._repair_incoming_text(deepcopy(updates))
        # JSON validation happens before any queue write.
        json.dumps(updates, allow_nan=False)
        with hold_directory_lock(self._knowledge_dir), hold_directory_lock(self._identity_dir):
            current = getattr(self, "get_" + field)()
            before = {k: deepcopy(current.get(k)) for k in updates}
            after = deepcopy(updates)
            if field == "profile" and "description" in after and current.get("description"):
                old, new = current["description"], after["description"]
                parts = [p for p in (new or "").split() if p not in set(old.split())]
                after["description"] = old + (" " + " ".join(parts) if parts else "")
            row = {"id": "identity-" + uuid.uuid4().hex, "version": 1, "field": field,
                   "updates": updates, "before": before, "after": after,
                   "missing_before": [k for k in updates if k not in current],
                   "status": "pending", "tier": "staging", "domain": "type:preference",
                   "created_at": _now_iso(), "source_tool": write_provenance.clean_client_text(source_tool),
                   "provenance": write_provenance.current()}
            stone = tombstones.lookup(self.root, "identity", row)
            if stone:
                return {"status": "rejected_before", "changed": False, "id": stone["id"]}
            rows = _rows(self)
            for existing in rows:
                if existing.get("status") == "pending" and existing.get("field") == field \
                        and existing.get("updates") == updates and existing.get("before") == before \
                        and existing.get("missing_before") == row["missing_before"]:
                    return existing
            rows.append(row)
            _save(self, rows)
        self._audit.log("write", "identity/proposal", detail=row["id"], source_tool=source_tool)
        return row

    def review_identity_proposal(self, item_id: str, action: str, *, expected_version: int | None = None,
                                 dry_run: bool = False, via: str = "cli:owner") -> dict:
        return self._review_identity_proposal(item_id, action, expected_version=expected_version,
                                             dry_run=dry_run, via=via)

    def _review_identity_proposal(self, item_id: str, action: str, *, expected_version: int | None = None,
                                  dry_run: bool = False, via: str = "cli:owner",
                                  preview: IdentityPreview | None = None) -> dict:
        """Approve/reject under identity and rejection locks, with old-value guards."""
        if review_boundary.mcp_origin() and not dry_run:
            return review_boundary.refusal(item_id, action=action)
        if self._read_only and not dry_run:
            return {"id": item_id, "status": "read_only", "changed": False}
        if action not in {"approve", "reject"}:
            return {"id": item_id, "status": "invalid_action", "changed": False}

        def decide(rows):
            row = next((r for r in rows if r.get("id") == item_id), None)
            def result(status, changed=False):
                return {"id": item_id, "status": status, "changed": changed}
            if row is None:
                return result("not_found")
            if expected_version is not None and row.get("version") != expected_version:
                return result("version_conflict")
            terminal = "approved" if action == "approve" else "rejected"
            if row.get('status') == 'applying' and action == 'reject':
                return result('approval_in_progress')
            if row.get("status") not in {"pending", "applying"}:
                return result("already_applied" if row.get("status") == terminal else "already_decided")
            stone = tombstones.by_id(self.root, item_id)
            if action == "approve" and stone:
                return result("rejected_before")
            if action == "approve":
                current = preview.current(row['field']) if preview is not None else getattr(self, "get_" + row["field"])()
                before = _matches(current, row["before"], row.get("missing_before", ()))
                after = _matches(current, row["after"])
                if not before and not after:
                    return result("identity_conflict")
            if dry_run:
                if preview is not None:
                    preview.decide(row, action, current if action == 'approve' else None)
                return result("planned")
            if action == "approve":
                if row['status'] == 'pending':
                    # Persist the intent, patch, original values and reviewed
                    # proposal version under the same locks BEFORE identity.
                    row['status'] = 'applying'
                    _save(self, rows)
                if not after:
                    kwargs = {"source_tool": row.get("source_tool", "")} if row["field"] == "profile" else {}
                    updates = deepcopy(row["after"])
                    if row["field"] == "preferences":
                        # Materializing a legacy work_style fallback must keep
                        # its untouched preferences, not replace them with a patch.
                        updates = {**current, **updates}
                    getattr(self, "update_" + row["field"])(updates, **kwargs)
            else:
                tombstones.append(self.root, "identity", row, via=via)
            row["status"] = terminal
            row["tier"] = "verified" if action == "approve" else "retired"
            row["reviewed_at"] = _now_iso()
            # Only metadata remains once decided; rejected content is never retained here.
            for key in (*BODY_FIELDS, "missing_before"):
                row.pop(key, None)
            _save(self, rows)
            return result("applied", True)

        if dry_run:
            return decide(preview.rows if preview is not None else _rows(self))
        with hold_directory_lock(self._knowledge_dir), hold_directory_lock(self._identity_dir):
            return decide(_rows(self))


class IdentityPreview:
    """A read-only simulation shared by every identity mark in a batch."""

    def __init__(self, eng):
        self.eng = eng
        self.rows = _rows(eng)
        self.values = {}
        self.written = set()

    def current(self, field):
        if field not in self.values:
            self.values[field] = deepcopy(getattr(self.eng, 'get_' + field)())
        # Preferences can still be backed by the legacy work_style file.
        if field == 'preferences' and not _read_json(self.eng._identity_dir / 'preferences.json') \
                and 'work_style' in self.values and field not in self.written:
            old = self.values['work_style']
            return {'work_patterns': old.get('preferences', {}),
                    'communication': old.get('communication', ''), 'tool_preferences': {}} if old else {}
        return self.values[field]

    def decide(self, row, action, current):
        if action == 'approve':
            self.values[row['field']] = {**current, **deepcopy(row['after'])}
            self.written.add(row['field'])
        row['status'] = 'approved' if action == 'approve' else 'rejected'
