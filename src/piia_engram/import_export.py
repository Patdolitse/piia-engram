"""Full-store import/export helpers for Engram.

This module keeps backup, migration, and cross-machine merge planning out of
the core facade while preserving Engram.export_all/import_all compatibility.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import capacity as _capacity
from .decision_thread import validate_edges
from .governance_store import RelationStore, ResolutionStore
from .storage import (
    DEFAULT_TRUST_BOUNDARIES,
    ENCRYPTED_PROFILE_FIELDS,
    SCHEMA_VERSION,
    _ALLOWED_PREFERENCES_FIELDS,
    _ALLOWED_PROFILE_FIELDS,
    _ALLOWED_QUALITY_FIELDS,
    _ALLOWED_TRUST_FIELDS,
    _now_iso,
    _read_json,
    _update_json,
    _write_json,
    hold_directory_lock,
)


def _metadata_source(input_path: str) -> dict[str, str]:
    """Return a metadata-only source descriptor for import previews/results."""
    name = Path(input_path).name
    return {"file_name": name} if name else {"file_name": ""}


class ImportExportMixin:
    # =====================================================================
    # Import / Export — 备份、迁移、跨机器同步
    # =====================================================================

    @staticmethod
    def _import_value_present(value: Any) -> bool:
        return value not in (None, "", [], {})

    @classmethod
    def _import_summary(cls, incoming_count: int = 0) -> dict:
        return {
            "incoming": incoming_count,
            "would_add": 0,
            "would_skip": 0,
            "conflicts": 0,
        }

    @classmethod
    def _merge_dict_preserving_existing(
        cls,
        existing: dict,
        incoming: dict,
        section: str,
        *,
        default_values: dict | None = None,
        field_prefix: str = "",
    ) -> tuple[dict, dict, list[dict]]:
        """Merge a backup section without overwriting existing non-empty values.

        Returns ``(merged, summary, conflicts)``. Conflicts are metadata-only:
        field names and the planned resolution, never local or incoming values.
        """
        merged = deepcopy(existing) if isinstance(existing, dict) else {}
        summary = cls._import_summary()
        conflicts: list[dict] = []
        defaults = default_values or {}

        if not isinstance(incoming, dict):
            return merged, summary, conflicts

        for key, incoming_value in incoming.items():
            if key.startswith("_"):
                continue
            field = f"{field_prefix}.{key}" if field_prefix else str(key)
            summary["incoming"] += 1
            if not cls._import_value_present(incoming_value):
                summary["would_skip"] += 1
                continue

            existing_value = merged.get(key)
            existing_is_default = (
                key in defaults and existing_value == defaults.get(key)
            )
            existing_present = (
                cls._import_value_present(existing_value)
                and not existing_is_default
            )

            if isinstance(existing_value, dict) and isinstance(incoming_value, dict):
                nested, nested_summary, nested_conflicts = cls._merge_dict_preserving_existing(
                    existing_value,
                    incoming_value,
                    section,
                    field_prefix=field,
                )
                merged[key] = nested
                for stat_key in summary:
                    summary[stat_key] += nested_summary[stat_key]
                conflicts.extend(nested_conflicts)
                continue

            if isinstance(existing_value, list) and isinstance(incoming_value, list):
                additions = [item for item in incoming_value if item not in existing_value]
                if additions:
                    merged[key] = existing_value + additions
                    summary["would_add"] += len(additions)
                    if len(additions) < len(incoming_value):
                        summary["would_skip"] += len(incoming_value) - len(additions)
                else:
                    summary["would_skip"] += 1
                continue

            if not existing_present:
                merged[key] = deepcopy(incoming_value)
                summary["would_add"] += 1
            elif existing_value == incoming_value:
                summary["would_skip"] += 1
            else:
                summary["conflicts"] += 1
                conflicts.append({
                    "section": section,
                    "field": field,
                    "resolution": "keep_existing",
                })

        return merged, summary, conflicts

    @classmethod
    def _plan_overwrite_dict(
        cls,
        existing: dict,
        incoming: dict,
        section: str,
    ) -> tuple[dict, list[dict]]:
        summary = cls._import_summary(len(incoming) if isinstance(incoming, dict) else 0)
        conflicts: list[dict] = []
        if not isinstance(incoming, dict):
            return summary, conflicts
        for key, incoming_value in incoming.items():
            if key.startswith("_"):
                continue
            existing_value = existing.get(key) if isinstance(existing, dict) else None
            if existing_value == incoming_value:
                summary["would_skip"] += 1
            elif cls._import_value_present(existing_value):
                summary["conflicts"] += 1
                conflicts.append({
                    "section": section,
                    "field": str(key),
                    "resolution": "overwrite_existing",
                })
            else:
                summary["would_add"] += 1
        return summary, conflicts

    def _read_profile_for_import_plan(self) -> dict:
        profile = _read_json(self._identity_dir / "profile.json")
        if not isinstance(profile, dict):
            return {}
        return self._crypto.decrypt_fields(profile, ENCRYPTED_PROFILE_FIELDS)

    def _read_trust_boundaries_for_import_plan(self) -> dict:
        existing = _read_json(self._identity_dir / "trust_boundaries.json")
        if not isinstance(existing, dict):
            existing = {}
        result = deepcopy(existing)
        for key, value in DEFAULT_TRUST_BOUNDARIES.items():
            result.setdefault(key, deepcopy(value))
        return result

    @staticmethod
    def _count_new_by_key(existing: list[dict], incoming: list[dict], key: str) -> dict:
        existing_values = {str(item.get(key, "")) for item in existing}
        new_count = 0
        skip_count = 0
        for item in incoming:
            value = str(item.get(key, ""))
            if value in existing_values:
                skip_count += 1
            else:
                existing_values.add(value)
                new_count += 1
        return {
            "incoming": len(incoming),
            "would_add": new_count,
            "would_skip": skip_count,
            "conflicts": 0,
        }

    @classmethod
    def _plan_entries_by_key(
        cls,
        existing: list[dict],
        incoming: list[dict],
        *,
        section: str,
        key_field: str,
        compare_fields: tuple[str, ...],
    ) -> tuple[dict, list[dict]]:
        """Plan merge for keyed knowledge entries without exposing entry bodies."""
        summary = cls._import_summary(len(incoming))
        conflicts: list[dict] = []
        existing_by_key = {
            str(item.get(key_field, "")): item
            for item in existing
            if item.get(key_field)
        }

        for item in incoming:
            key_value = str(item.get(key_field, ""))
            if not key_value or key_value not in existing_by_key:
                summary["would_add"] += 1
                if key_value:
                    existing_by_key[key_value] = item
                continue

            matched = existing_by_key[key_value]
            changed_fields = [
                field for field in compare_fields
                if matched.get(field) != item.get(field)
            ]
            if changed_fields:
                summary["conflicts"] += 1
                conflict = {
                    "section": section,
                    "match_key": key_field,
                    "resolution": "review_version_chain_candidate",
                    "candidate_relation": "supersedes",
                    "changed_fields": sorted(changed_fields),
                }
                if matched.get("id"):
                    conflict["existing_id"] = matched["id"]
                if item.get("id"):
                    conflict["incoming_id"] = item["id"]
                conflicts.append(conflict)
            else:
                summary["would_skip"] += 1

        return summary, conflicts

    def _build_import_plan(
        self,
        data: dict,
        *,
        merge: bool,
        input_path: str,
    ) -> dict:
        summary: dict[str, dict] = {}
        conflicts: list[dict] = []

        identity = data.get("identity", {}) if isinstance(data, dict) else {}
        identity_sections = {
            "profile": (self._read_profile_for_import_plan(), _ALLOWED_PROFILE_FIELDS, None),
            "preferences": (
                _read_json(self._identity_dir / "preferences.json") or {},
                _ALLOWED_PREFERENCES_FIELDS,
                None,
            ),
            "work_style": (_read_json(self._identity_dir / "work_style.json") or {}, None, None),
            "quality_standards": (
                _read_json(self._identity_dir / "quality_standards.json") or {},
                _ALLOWED_QUALITY_FIELDS,
                None,
            ),
            "trust_boundaries": (
                self._read_trust_boundaries_for_import_plan(),
                _ALLOWED_TRUST_FIELDS,
                DEFAULT_TRUST_BOUNDARIES,
            ),
        }
        for section, incoming_value in identity.items():
            if section not in identity_sections or not isinstance(incoming_value, dict):
                continue
            existing_value, allowed, defaults = identity_sections[section]
            incoming_section = {
                key: value
                for key, value in incoming_value.items()
                if allowed is None or key in allowed
            }
            if merge:
                _, section_summary, section_conflicts = self._merge_dict_preserving_existing(
                    existing_value,
                    incoming_section,
                    section,
                    default_values=defaults,
                )
            else:
                section_summary, section_conflicts = self._plan_overwrite_dict(
                    existing_value,
                    incoming_section,
                    section,
                )
            summary[section] = section_summary
            conflicts.extend(section_conflicts)

        knowledge = data.get("knowledge", {}) if isinstance(data, dict) else {}
        if isinstance(knowledge.get("lessons"), list):
            existing_lessons = self._read_entries(
                self._knowledge_dir / "lessons.json",
                "lesson",
                migrate=False,
            )
            if merge:
                lesson_summary, lesson_conflicts = self._plan_entries_by_key(
                    existing_lessons,
                    knowledge["lessons"],
                    section="lessons",
                    key_field="summary",
                    compare_fields=("detail", "domain", "status", "tier"),
                )
                summary["lessons"] = lesson_summary
                conflicts.extend(lesson_conflicts)
            else:
                summary["lessons"] = self._count_new_by_key(
                    existing_lessons,
                    knowledge["lessons"],
                    "__never_match__",
                )
                summary["lessons"]["would_add"] = len(knowledge["lessons"])
                summary["lessons"]["would_skip"] = 0
        if isinstance(knowledge.get("decisions"), list):
            existing_decisions = self._read_entries(
                self._knowledge_dir / "decisions.json",
                "decision",
                migrate=False,
            )
            if merge:
                decision_summary, decision_conflicts = self._plan_entries_by_key(
                    existing_decisions,
                    knowledge["decisions"],
                    section="decisions",
                    key_field="question",
                    compare_fields=(
                        "choice",
                        "reasoning",
                        "alternatives",
                        "domain",
                        "project",
                        "status",
                        "tier",
                    ),
                )
                summary["decisions"] = decision_summary
                conflicts.extend(decision_conflicts)
            else:
                summary["decisions"] = self._count_new_by_key(
                    existing_decisions,
                    knowledge["decisions"],
                    "__never_match__",
                )
                summary["decisions"]["would_add"] = len(knowledge["decisions"])
                summary["decisions"]["would_skip"] = 0
        if isinstance(knowledge.get("domains"), dict):
            existing_domains = _read_json(self._knowledge_dir / "domains.json") or {}
            incoming_domains = knowledge["domains"]
            new_count = sum(1 for name in incoming_domains if name not in existing_domains)
            summary["domains"] = {
                "incoming": len(incoming_domains),
                "would_add": new_count if merge else len(incoming_domains),
                "would_skip": len(incoming_domains) - new_count if merge else 0,
                "conflicts": 0,
            }
        if isinstance(knowledge.get("playbooks"), list):
            existing_titles = {e.get("title", "") for e in self._read_playbook_index()}
            incoming_playbooks = knowledge["playbooks"]
            new_count = sum(
                1 for pb in incoming_playbooks
                if pb.get("title", "") not in existing_titles
            )
            summary["playbooks"] = {
                "incoming": len(incoming_playbooks),
                "would_add": new_count if merge else len(incoming_playbooks),
                "would_skip": len(incoming_playbooks) - new_count if merge else 0,
                "conflicts": 0,
            }

        environment = data.get("environment", {}) if isinstance(data, dict) else {}
        if isinstance(environment.get("tools"), list):
            existing_names = {t.get("name", "").lower() for t in self._read_tools()}
            incoming_tools = environment["tools"]
            new_count = sum(
                1 for tool in incoming_tools
                if tool.get("name", "").lower() not in existing_names
            )
            summary["tools"] = {
                "incoming": len(incoming_tools),
                "would_add": new_count if merge else len(incoming_tools),
                "would_skip": len(incoming_tools) - new_count if merge else 0,
                "conflicts": 0,
            }

        projects = data.get("projects", {}) if isinstance(data, dict) else {}
        if isinstance(projects, dict) and projects:
            new_count = 0
            for pid, project_data in projects.items():
                existing = _read_json(self._projects_dir / f"{pid}.json") or {}
                if merge and existing and isinstance(project_data, dict):
                    _, project_summary, project_conflicts = self._merge_dict_preserving_existing(
                        existing,
                        project_data,
                        "projects",
                        field_prefix=str(pid),
                    )
                    conflicts.extend(project_conflicts)
                    if project_summary["would_add"]:
                        new_count += 1
                elif not existing:
                    new_count += 1
            conflict_count = sum(1 for c in conflicts if c.get("section") == "projects")
            summary["projects"] = {
                "incoming": len(projects),
                "would_add": new_count if merge else len(projects),
                "would_skip": len(projects) - new_count if merge else 0,
                "conflicts": conflict_count,
            }

        return {
            "status": "preview",
            "mode": "merge" if merge else "overwrite",
            "dry_run": True,
            "summary": summary,
            "conflicts": conflicts,
            "source": _metadata_source(input_path),
        }

    @staticmethod
    def _import_version_hash(entry: dict, entry_type: str) -> str:
        """Stable content hash for import materialization idempotency."""
        if entry_type == "decision":
            fields = {
                "question": entry.get("question", ""),
                "choice": entry.get("choice", ""),
                "reasoning": entry.get("reasoning", ""),
                "alternatives": entry.get("alternatives", []),
                "domain": entry.get("domain", ""),
                "project": entry.get("project", ""),
                "tier": entry.get("tier", ""),
            }
        else:
            fields = {
                "summary": entry.get("summary", ""),
                "detail": entry.get("detail", ""),
                "domain": entry.get("domain", ""),
                "tier": entry.get("tier", ""),
            }
        payload = json.dumps(fields, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _materialized_import_id(
        section: str,
        existing_id: str,
        version_hash: str,
    ) -> str:
        seed = f"import-version:{section}:{existing_id}:{version_hash}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]

    # -- lessons, decisions and relations in one locked section ---------------
    # An import holds the knowledge lock once: it writes a pending marker, then
    # lessons and decisions through the capacity core (import rules: no new-row
    # exemption), the export's archive segment, and relations, and removes the
    # marker. A crash leaves the marker; running the same import again is
    # idempotent. Nothing is rolled back.

    _IMPORT_PENDING_MARKER = ".import-pending.json"
    _IMPORT_ROW_SECTIONS = (("lessons", "lesson"), ("decisions", "decision"))

    @staticmethod
    def _import_identity_key(row: dict, kind: str) -> str:
        """The merge dedup key: a lesson's summary, a decision's question."""
        return str(row.get("summary" if kind == "lesson" else "question") or "")

    def _prepare_import_rows(self, kind: str, rows: Any) -> list[dict]:
        """Normalized copies of the incoming rows; a row without an id gets a stable one.

        The id is derived from the row's identity text, so importing the same
        file again finds the same rows instead of adding copies.
        """
        prepared: list[dict] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            item = deepcopy(row)
            if not item.get("id"):
                extra = item.get("choice") if kind == "decision" else item.get("domain")
                seed = f"import:{kind}:{self._entry_identity_text(item, kind)}\n{extra or ''}"
                item["id"] = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
            prepared.append(self._ensure_fields(item, kind))
        return prepared

    def _import_digest(self, row: dict, kind: str) -> tuple[str, str]:
        normalized = self._ensure_fields(deepcopy(row), kind)
        return str(normalized.get("id") or ""), _capacity.content_digest(normalized, kind)

    def _materialize_items(self, knowledge: dict, conflicts: list[dict]) -> list[dict]:
        """Version-chain candidates from the dry-run conflicts, in conflict order."""
        items: list[dict] = []
        for conflict in conflicts:
            section = str(conflict.get("section") or "")
            kind = {"lessons": "lesson", "decisions": "decision"}.get(section)
            if kind is None:
                continue
            if conflict.get("resolution") != "review_version_chain_candidate":
                continue
            if conflict.get("candidate_relation") != "supersedes":
                continue
            item = {
                "section": section,
                "existing_id": str(conflict.get("existing_id") or ""),
                "incoming_id": str(conflict.get("incoming_id") or ""),
                "changed_fields": list(conflict.get("changed_fields") or []),
                "outcome": "skipped",
                "reason": "",
                "_kind": kind,
            }
            incoming_items = knowledge.get(section)
            if not item["existing_id"] or not item["incoming_id"] or not isinstance(incoming_items, list):
                item["reason"] = "missing_ids"
            else:
                incoming = next(
                    (
                        candidate for candidate in incoming_items
                        if isinstance(candidate, dict)
                        and str(candidate.get("id") or "") == item["incoming_id"]
                    ),
                    None,
                )
                if isinstance(incoming, dict):
                    item["_incoming"] = deepcopy(incoming)
                else:
                    item["reason"] = "incoming_not_found"
            items.append(item)
        return items

    def _materialize_in_rows(
        self,
        kind: str,
        rows: list[dict],
        item: dict,
        edges: list[dict],
        new_edges: list[tuple[str, str]],
        input_path: str,
    ) -> None:
        """Add one version-chain candidate to ``rows`` inside the import section.

        A reviewed candidate retires the row it replaces and adds the edge. An
        unreviewed one records ``pending_supersedes`` and leaves that row
        active; the edge is written when the candidate is promoted.
        """
        incoming = item.get("_incoming")
        if not isinstance(incoming, dict):
            return
        section = item["section"]
        existing_id = item["existing_id"]
        existing = next((r for r in rows if str(r.get("id") or "") == existing_id), None)
        if existing is None:
            item.update(outcome="skipped", reason="existing_not_found")
            return
        if existing.get("status") != "active":
            src = next(
                (
                    edge["src"] for edge in edges
                    if edge.get("rel") == "supersedes" and edge.get("dst") == existing_id
                ),
                "",
            )
            item.update(outcome="skipped", reason="existing_not_active", new_id=src)
            return
        version_hash = self._import_version_hash(incoming, kind)
        candidate = next(
            (
                r for r in rows
                if str(r.get("supersedes") or "") == existing_id
                and str(r.get("import_version_hash") or "") == version_hash
            ),
            None,
        )
        if candidate is None:
            ids = {str(r.get("id") or "") for r in rows if r.get("id")}
            candidate = deepcopy(incoming)
            desired_id = str(candidate.get("id") or "")
            if desired_id and desired_id not in ids and desired_id != existing_id:
                new_id = desired_id
            else:
                new_id = self._materialized_import_id(section, existing_id, version_hash)
                if new_id in ids:
                    item.update(outcome="skipped", reason="id_collision")
                    return
                if desired_id:
                    candidate["source_import_id"] = desired_id
            candidate["id"] = new_id
            candidate["status"] = "active"
            candidate["supersedes"] = existing_id
            candidate["parent_id"] = existing_id
            candidate["root_id"] = existing.get("root_id") or existing_id
            candidate["import_version_hash"] = version_hash
            candidate["version_materialized_at"] = _now_iso()
            source_name = _metadata_source(input_path)["file_name"]
            candidate["import_source"] = source_name
            provenance = candidate.get("provenance")
            if not isinstance(provenance, dict):
                provenance = {}
            provenance.setdefault("source_tool", candidate.get("source_tool") or "import")
            provenance["import_source"] = source_name
            provenance["supersedes"] = existing_id
            candidate["provenance"] = provenance
            candidate = self._ensure_fields(candidate, kind)
            rows.append(candidate)
        new_id = str(candidate.get("id") or "")
        if not new_id:
            item.update(outcome="skipped", reason="materialized_id_missing")
            return
        if _capacity.pool_of(candidate) == _capacity.POOL_V:
            existing["status"] = "outdated"
            existing["last_updated"] = _now_iso()
            self._ensure_fields(existing, kind)
            new_edges.append((new_id, existing_id))
            item.update(outcome="materialized", reason="", new_id=new_id)
        else:
            candidate["pending_supersedes"] = existing_id
            item.update(outcome="materialized", reason="", new_id=new_id, pending_review=True)

    def _import_row_mutator(
        self,
        kind: str,
        incoming: list[dict],
        *,
        merge: bool,
        ctx: _capacity.CapacityContext,
        stats: dict,
        archive_keys: set[str],
        items: list[dict],
        edges: list[dict],
        new_edges: list[tuple[str, str]],
        input_path: str,
    ):
        """The lessons / decisions mutator of an import, recomputed on the current rows."""

        def _mutate(current: list[dict]) -> list[dict]:
            stats["added"] = 0
            if not merge:
                wanted = {self._import_digest(row, kind) for row in incoming}
                wanted_ids = {key[0] for key in wanted}
                for local in current:
                    key = self._import_digest(local, kind)
                    if key not in wanted and key[0] in wanted_ids:
                        ctx.extra_archive.append((local, _capacity.REASON_IMPORT_REPLACE))
                stats["added"] = len(incoming)
                return [deepcopy(row) for row in incoming]
            seen = {self._import_identity_key(row, kind) for row in current} | archive_keys
            for row in incoming:
                key = self._import_identity_key(row, kind)
                if key in seen:
                    continue
                current.append(deepcopy(row))
                seen.add(key)
                stats["added"] += 1
            for item in items:
                self._materialize_in_rows(kind, current, item, edges, new_edges, input_path)
            return current

        return _mutate

    def _import_context(self, *, merge: bool, allow_over_cap: bool) -> _capacity.CapacityContext:
        return _capacity.CapacityContext(
            import_mode=True,
            owner_override=allow_over_cap,
            source_tool="import",
            removed_reason=_capacity.REASON_REMOVED if merge else _capacity.REASON_IMPORT_REPLACE,
        )

    def _import_capacity_preview(
        self,
        kind: str,
        incoming: list[dict],
        *,
        merge: bool,
        archive_keys: set[str],
        items: list[dict],
        edges: list[dict],
        input_path: str,
        allow_over_cap: bool,
    ) -> dict:
        """What the capacity rules would do to ``kind`` for this import; writes nothing."""
        filename = "lessons.json" if kind == "lesson" else "decisions.json"
        raw = _read_json(self._knowledge_dir / filename)
        current = self._entries_for_locked_mutation(raw if isinstance(raw, list) else [], kind)
        ctx = self._import_context(merge=merge, allow_over_cap=allow_over_cap)
        mutate = self._import_row_mutator(
            kind, incoming, merge=merge, ctx=ctx, stats={"added": 0},
            archive_keys=archive_keys, items=deepcopy(items), edges=edges,
            new_edges=[], input_path=input_path,
        )
        after = mutate(deepcopy(current))
        try:
            plan = _capacity.plan_capacity(
                deepcopy(current), after, kind=kind, now=datetime.now(timezone.utc),
                limits=_capacity.limits_from_env(), ctx=ctx,
            )
        except _capacity.CapacityRefused as exc:
            return {"refused": True, "hard_cap": exc.hard_cap, "verified_active": exc.verified_active}
        placed = len(plan.placed_ids)
        return {"refused": False, "moved_to_archive": len(plan.archive) - placed, "placed_in_archive": placed}

    def _import_archive_segment(self, kind: str, rows: list[dict]) -> int:
        """Append the export's archived rows that this store does not have yet."""
        if not rows:
            return 0
        filename = "lessons.json" if kind == "lesson" else "decisions.json"
        active_ids = {
            str(row.get("id") or "")
            for row in self._read_entries(self._knowledge_dir / filename, kind, migrate=False)
        }
        present = {
            (str(row.get("id") or ""), _capacity.content_digest(row, kind))
            for row in self._archive_rows_cached(kind)
        }
        by_reason: dict[str, list[dict]] = {}
        for row in rows:
            key = (str(row.get("id") or ""), _capacity.content_digest(row, kind))
            if key[0] in active_ids or key in present:
                continue
            present.add(key)
            by_reason.setdefault(str(row.get("overflow_archive_reason") or "imported"), []).append(row)
        written = 0
        for reason, group in by_reason.items():
            written += len(self._archive_overflow_rows(kind, group, reason=reason, preserve_stamp=True))
        return written

    def _import_relations_locked(
        self,
        relations_in: list[dict] | None,
        new_edges: list[tuple[str, str]],
        *,
        merge: bool,
    ) -> str | None:
        """Merge: local edges plus new file edges. Replace: file edges plus every local edge."""
        counts = {"added": 0}

        def _key(edge: dict) -> tuple:
            return edge.get("src"), edge.get("rel"), edge.get("dst")

        def _mutate(current: Any) -> list[dict]:
            local = [edge for edge in (current if isinstance(current, list) else []) if isinstance(edge, dict)]
            if relations_in is None or merge:
                merged = {_key(edge): edge for edge in local}
                for edge in relations_in or []:
                    if _key(edge) not in merged:
                        merged[_key(edge)] = edge
                        counts["added"] += 1
            else:
                merged = {_key(edge): edge for edge in relations_in}
                for edge in local:
                    merged.setdefault(_key(edge), edge)
            for src, dst in new_edges:
                merged.setdefault((src, "supersedes", dst), {"src": src, "rel": "supersedes", "dst": dst})
            return list(merged.values())

        _update_json(self._knowledge_dir / "relations.json", _mutate, default=[])
        if relations_in is None:
            return None
        return f"relations(+{counts['added']})" if merge else f"relations({len(relations_in)})"

    def _import_knowledge_rows(
        self,
        data: dict,
        knowledge: dict,
        *,
        merge: bool,
        materialize: bool,
        conflicts: list[dict],
        input_path: str,
        allow_over_cap: bool,
    ) -> dict:
        """Import lessons, decisions, the archive segment and relations in one locked section.

        Returns the ``imported`` text per section and the materialization
        payload, or ``{"error": "capacity_full", ...}`` with nothing written
        when reviewed memories would exceed the hard cap.
        """
        incoming = {
            kind: self._prepare_import_rows(kind, knowledge.get(section))
            for section, kind in self._IMPORT_ROW_SECTIONS
            if knowledge.get(section)
        }
        segment = data.get("overflow_archive") if isinstance(data.get("overflow_archive"), dict) else {}
        archive_in = {
            kind: self._prepare_import_rows(kind, segment.get(section))
            for section, kind in self._IMPORT_ROW_SECTIONS
        }
        relations_in = validate_edges(knowledge.get("relations") or []) if "relations" in knowledge else None
        items = self._materialize_items(knowledge, conflicts) if merge and materialize else []
        report: dict[str, Any] = {}
        if merge and materialize:
            report["version_chain_materialization"] = {
                "enabled": True, "materialized": 0, "skipped": 0, "items": [],
            }
        if not incoming and not any(archive_in.values()) and relations_in is None and not items:
            return report

        new_edges: list[tuple[str, str]] = []
        with hold_directory_lock(self._knowledge_dir):
            edges = RelationStore(self.root).all_edges()
            archive_keys = {
                kind: {self._import_identity_key(row, kind) for row in self._archive_rows_cached(kind)}
                for _section, kind in self._IMPORT_ROW_SECTIONS
            }
            for kind, rows in incoming.items():
                preview = self._import_capacity_preview(
                    kind, rows, merge=merge, archive_keys=archive_keys[kind],
                    items=[i for i in items if i["_kind"] == kind], edges=edges,
                    input_path=input_path, allow_over_cap=allow_over_cap,
                )
                if preview["refused"]:
                    return {
                        "error": "capacity_full",
                        "kind": kind,
                        "hard_cap": preview["hard_cap"],
                        "verified_active": preview["verified_active"],
                        "message": (
                            "importing would put more reviewed memories in the store than the hard cap; "
                            "nothing was imported (the owner can allow it with engram import --allow-over-cap)"
                        ),
                    }
            marker = self._knowledge_dir / self._IMPORT_PENDING_MARKER
            _write_json(marker, {
                "mode": "merge" if merge else "overwrite",
                "source": _metadata_source(input_path),
                "started_at": _now_iso(),
            })
            for section, kind in self._IMPORT_ROW_SECTIONS:
                rows = incoming.get(kind)
                if rows is None:
                    continue
                ctx = self._import_context(merge=merge, allow_over_cap=allow_over_cap)
                stats = {"added": 0}
                filename = "lessons.json" if kind == "lesson" else "decisions.json"
                outcome = self._update_entries(
                    self._knowledge_dir / filename,
                    kind,
                    self._import_row_mutator(
                        kind, rows, merge=merge, ctx=ctx, stats=stats,
                        archive_keys=archive_keys[kind],
                        items=[i for i in items if i["_kind"] == kind], edges=edges,
                        new_edges=new_edges, input_path=input_path,
                    ),
                    capacity_ctx=ctx,
                )
                note = f", archived {len(outcome.archived_ids)}" if outcome.archived_ids else ""
                sign = "+" if merge else ""
                report[section] = f"{section}({sign}{stats['added']}{note})"
            for kind, rows in archive_in.items():
                self._import_archive_segment(kind, rows)
            if relations_in is not None or new_edges:
                relations_text = self._import_relations_locked(relations_in, new_edges, merge=merge)
                if relations_text is not None:
                    report["relations"] = relations_text
            marker.unlink(missing_ok=True)

        if items:
            payload = report["version_chain_materialization"]
            for item in items:
                item.pop("_incoming", None)
                item.pop("_kind", None)
                if item["outcome"] == "materialized":
                    payload["materialized"] += 1
                    detail = f"{item['section']}:{item['new_id']} supersedes {item['existing_id']}"
                    if item.get("pending_review"):
                        detail += " (pending review)"
                    self._audit.log("write", "knowledge/import_version_chain", detail=detail)
                else:
                    payload["skipped"] += 1
                payload["items"].append(item)
        return report

    def export_all(self, output_path: str | None = None) -> str:
        """导出整个 Engram 为单一 JSON 文件。

        包含：identity、knowledge、projects 所有数据。
        用于备份或迁移到另一台机器。

        Args:
            output_path: 导出文件路径。默认存到 ~/.engram/exports/engram_backup_<date>.json

        Returns:
            导出文件的完整路径。
        """
        export_data = {
            "schema_version": SCHEMA_VERSION,
            "exported_at": _now_iso(),
            "identity": {
                "profile": self.get_profile(),
                "preferences": self.get_preferences(),
                "work_style": self.get_work_style(),  # backward compat
                "quality_standards": self.get_quality_standards(),
                "trust_boundaries": self.get_trust_boundaries(),
            },
            "knowledge": {
                # Export decrypted plaintext so backups are portable across
                # different .corpus_salt / ENGRAM_SECRET combinations.
                # The backup file itself should be protected by the user.
                # v4.19.1: export HEADs only — superseded snapshots are local
                # history artifacts (reachable via get_knowledge_history), not
                # part of the portable backup.
                "lessons": [
                    l for l in self._read_entries(
                        self._knowledge_dir / "lessons.json", "lesson")
                    if l.get("status") != "superseded" and "snapshot_of" not in l
                ],
                "decisions": [
                    d for d in self._read_entries(
                        self._knowledge_dir / "decisions.json", "decision")
                    if d.get("status") != "superseded" and "snapshot_of" not in d
                ],
                "domains": self.get_domains(),
                "playbooks": self._export_playbooks(),
                "relations": RelationStore(self.root).all_edges(),
                "conflict_resolutions": ResolutionStore(self.root).all_records(),
            },
            "environment": {
                "tools": self._export_tools(),
            },
            "projects": {},
            # Rows the per-type cap moved out of the active knowledge files
            # (HEADs only, like the knowledge section above).
            "overflow_archive": {
                kind + "s": [
                    row for row in self._read_overflow_archive(kind)
                    if row.get("status") != "superseded" and "snapshot_of" not in row
                ]
                for kind in ("lesson", "decision")
            },
        }

        # 导出所有项目快照
        for f in sorted(self._projects_dir.glob("*.json")):
            data = _read_json(f)
            if data:
                export_data["projects"][f.stem] = data

        # 确定输出路径
        if output_path:
            out = Path(output_path)
        else:
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = self._exports_dir / f"engram_backup_{date_str}.json"

        out.parent.mkdir(parents=True, exist_ok=True)
        _write_json(out, export_data)
        self._audit.log("export", "all", detail=f"exported to {out}")
        return str(out)

    def import_all(
        self,
        input_path: str,
        merge: bool = True,
        dry_run: bool = False,
        materialize_version_chain: bool = False,
        allow_over_cap: bool = False,
    ) -> dict:
        """从备份文件导入 Engram 数据。

        Args:
            input_path: 备份文件路径（export_all 生成的 JSON）。
            merge: True=合并（已有数据保留，新数据追加），False=覆盖。
            dry_run: True=只返回元数据预览，不写入任何 Engram 数据。
            materialize_version_chain: True=在 merge apply 时，将 dry-run
                标出的 same-key 分歧知识导入为 supersedes 版本链。
            allow_over_cap: True=已审记忆超过硬上限时仍然导入（只由 Owner
                在命令行显式放行；MCP 工具从不传入）。

        Returns:
            导入结果摘要。
        """
        path = Path(input_path)
        if not path.is_file():
            return {"error": f"文件不存在: {input_path}"}

        data = _read_json(path)
        if not data or "schema_version" not in data:
            return {"error": "不是有效的 Engram 备份文件"}

        plan = self._build_import_plan(data, merge=merge, input_path=input_path)
        if dry_run:
            return plan

        # Lessons, decisions, their archive segment and relations first, in one
        # locked section: a refused import writes nothing at all.
        knowledge = data.get("knowledge", {})
        rows_report = self._import_knowledge_rows(
            data,
            knowledge if isinstance(knowledge, dict) else {},
            merge=merge,
            materialize=materialize_version_chain,
            conflicts=plan.get("conflicts", []),
            input_path=input_path,
            allow_over_cap=allow_over_cap,
        )
        if rows_report.get("error"):
            return rows_report

        imported = []

        # Identity
        identity = data.get("identity", {})
        if identity.get("profile"):
            if merge:
                existing, _, _ = self._merge_dict_preserving_existing(
                    self.get_profile(),
                    {
                        key: value
                        for key, value in identity["profile"].items()
                        if key in _ALLOWED_PROFILE_FIELDS
                    },
                    "profile",
                )
                self.update_profile(existing)
            else:
                profile = {
                    key: value
                    for key, value in identity["profile"].items()
                    if key in _ALLOWED_PROFILE_FIELDS
                }
                encrypted = self._crypto.encrypt_fields(profile, ENCRYPTED_PROFILE_FIELDS)
                _write_json(self._identity_dir / "profile.json", encrypted)
            imported.append("profile")

        if identity.get("preferences"):
            preferences = {
                key: value
                for key, value in identity["preferences"].items()
                if key in _ALLOWED_PREFERENCES_FIELDS
            }
            if merge:
                merged, _, _ = self._merge_dict_preserving_existing(
                    self.get_preferences(),
                    preferences,
                    "preferences",
                )
                self.update_preferences(merged)
            else:
                _write_json(self._identity_dir / "preferences.json", preferences)
            imported.append("preferences")

        if identity.get("work_style"):
            if merge:
                merged, _, _ = self._merge_dict_preserving_existing(
                    self.get_work_style(),
                    identity["work_style"],
                    "work_style",
                )
                self.update_work_style(merged)
            else:
                _write_json(self._identity_dir / "work_style.json", identity["work_style"])
            imported.append("work_style")

        if identity.get("quality_standards"):
            if merge:
                quality = {
                    key: value
                    for key, value in identity["quality_standards"].items()
                    if key in _ALLOWED_QUALITY_FIELDS
                }
                merged, _, _ = self._merge_dict_preserving_existing(
                    self.get_quality_standards(),
                    quality,
                    "quality_standards",
                )
                self.update_quality_standards(merged)
            else:
                quality = {
                    key: value
                    for key, value in identity["quality_standards"].items()
                    if key in _ALLOWED_QUALITY_FIELDS
                }
                _write_json(self._identity_dir / "quality_standards.json", quality)
            imported.append("quality_standards")

        if identity.get("trust_boundaries"):
            trust = {
                key: value
                for key, value in identity["trust_boundaries"].items()
                if key in _ALLOWED_TRUST_FIELDS
            }
            if merge:
                merged, _, _ = self._merge_dict_preserving_existing(
                    self.get_trust_boundaries(),
                    trust,
                    "trust_boundaries",
                    default_values=DEFAULT_TRUST_BOUNDARIES,
                )
                self.update_trust_boundaries(merged)
            else:
                _write_json(self._identity_dir / "trust_boundaries.json", trust)
            imported.append("trust_boundaries")

        # Knowledge
        for section in ("lessons", "decisions"):
            if section in rows_report:
                imported.append(rows_report[section])

        if knowledge.get("domains"):
            if merge:
                existing = self.get_domains()
                for name, info in knowledge["domains"].items():
                    if name not in existing:
                        existing[name] = info
                    else:
                        # 取更大的 project_count
                        existing[name]["project_count"] = max(
                            existing[name].get("project_count", 0),
                            info.get("project_count", 0),
                        )
                _write_json(self._knowledge_dir / "domains.json", existing)
            else:
                _write_json(self._knowledge_dir / "domains.json", knowledge["domains"])
            imported.append("domains")

        if knowledge.get("playbooks"):
            new_count = 0
            new_body_paths: list[Path] = []
            existing_index = self._read_playbook_index()
            existing_titles = {e.get("title", "") for e in existing_index}
            for pb in knowledge["playbooks"]:
                if pb.get("title") not in existing_titles:
                    pb = self._ensure_playbook_fields(pb)
                    body_path = self._playbooks_dir / f"{pb['id']}.json"
                    self._write_playbook_file(body_path, pb)
                    new_body_paths.append(body_path)
                    existing_index.append(self._playbook_index_entry(pb))
                    existing_titles.add(pb.get("title", ""))
                    new_count += 1
            if new_count:
                try:
                    self._write_playbook_index(existing_index)
                except Exception:
                    for p in new_body_paths:
                        try:
                            p.unlink(missing_ok=True)
                        except OSError:
                            pass
                    raise
            imported.append(f"playbooks(+{new_count})" if merge else f"playbooks({len(knowledge['playbooks'])})")

        if "relations" in rows_report:
            imported.append(rows_report["relations"])

        if "conflict_resolutions" in knowledge:
            store = ResolutionStore(self.root)
            incoming_resolutions = knowledge.get("conflict_resolutions") or {}
            if merge:
                changed = store.merge_records(incoming_resolutions)
                imported.append(f"conflict_resolutions(+{changed})")
            else:
                store.replace_all(incoming_resolutions)
                imported.append(f"conflict_resolutions({len(store.all_records())})")

        # Environment (tools registry)
        environment = data.get("environment", {})
        if environment.get("tools"):
            if merge:
                existing = self._read_tools()
                existing_names = {t.get("name", "").lower() for t in existing}
                new_count = 0
                for tool in environment["tools"]:
                    if tool.get("name", "").lower() not in existing_names:
                        tool = self._ensure_tool_fields(tool)
                        existing.append(tool)
                        existing_names.add(tool.get("name", "").lower())
                        new_count += 1
                self._write_tools(existing)
                imported.append(f"tools(+{new_count})")
            else:
                self._write_tools(environment["tools"])
                imported.append(f"tools({len(environment['tools'])})")

        # Projects
        projects = data.get("projects", {})
        if projects:
            for pid, proj_data in projects.items():
                proj_path = self._projects_dir / f"{pid}.json"
                if merge and proj_path.exists():
                    existing = _read_json(proj_path)
                    merged, _, _ = self._merge_dict_preserving_existing(
                        existing,
                        proj_data,
                        "projects",
                    )
                    _write_json(proj_path, merged)
                else:
                    _write_json(proj_path, proj_data)
            imported.append(f"projects({len(projects)})")

        version_chain_materialization = None
        if merge and materialize_version_chain:
            version_chain_materialization = rows_report["version_chain_materialization"]
            imported.append(
                "version_chains"
                f"(+{version_chain_materialization.get('materialized', 0)})"
            )

        self._audit.log("import", "all", detail=f"imported from {input_path}")
        result = {
            "status": "success",
            "mode": "merge" if merge else "overwrite",
            "imported": imported,
            "summary": plan.get("summary", {}),
            "conflicts": plan.get("conflicts", []),
            "source": _metadata_source(input_path),
        }
        if version_chain_materialization is not None:
            result["version_chain_materialization"] = version_chain_materialization
        return result
