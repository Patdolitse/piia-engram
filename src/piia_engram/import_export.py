"""Full-store import/export helpers for Engram.

This module keeps backup, migration, and cross-machine merge planning out of
the core facade while preserving Engram.export_all/import_all compatibility.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import (
    DEFAULT_TRUST_BOUNDARIES,
    ENCRYPTED_PROFILE_FIELDS,
    MAX_KNOWLEDGE_ENTRIES,
    SCHEMA_VERSION,
    _ALLOWED_PREFERENCES_FIELDS,
    _ALLOWED_PROFILE_FIELDS,
    _ALLOWED_QUALITY_FIELDS,
    _ALLOWED_TRUST_FIELDS,
    _now_iso,
    _read_json,
    _write_json,
)


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
            "source": input_path,
        }

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
                "lessons": self._read_entries(
                    self._knowledge_dir / "lessons.json", "lesson"),
                "decisions": self._read_entries(
                    self._knowledge_dir / "decisions.json", "decision"),
                "domains": self.get_domains(),
                "playbooks": self._export_playbooks(),
            },
            "environment": {
                "tools": self._export_tools(),
            },
            "projects": {},
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

    def import_all(self, input_path: str, merge: bool = True, dry_run: bool = False) -> dict:
        """从备份文件导入 Engram 数据。

        Args:
            input_path: 备份文件路径（export_all 生成的 JSON）。
            merge: True=合并（已有数据保留，新数据追加），False=覆盖。
            dry_run: True=只返回元数据预览，不写入任何 Engram 数据。

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
        knowledge = data.get("knowledge", {})

        if knowledge.get("lessons"):
            if merge:
                existing = self._read_entries(
                    self._knowledge_dir / "lessons.json",
                    "lesson",
                    migrate=False,
                )
                existing_summaries = {l.get("summary", "") for l in existing}
                new_count = 0
                for lesson in knowledge["lessons"]:
                    if lesson.get("summary") not in existing_summaries:
                        existing.append(lesson)
                        existing_summaries.add(lesson.get("summary", ""))
                        new_count += 1
                # Keep last MAX_KNOWLEDGE_ENTRIES
                self._write_entries(self._knowledge_dir / "lessons.json", existing[-MAX_KNOWLEDGE_ENTRIES:], "lesson")
                imported.append(f"lessons(+{new_count})")
            else:
                self._write_entries(self._knowledge_dir / "lessons.json", knowledge["lessons"][-MAX_KNOWLEDGE_ENTRIES:], "lesson")
                imported.append(f"lessons({len(knowledge['lessons'])})")

        if knowledge.get("decisions"):
            if merge:
                existing = self._read_entries(
                    self._knowledge_dir / "decisions.json",
                    "decision",
                    migrate=False,
                )
                existing_questions = {d.get("question", "") for d in existing}
                new_count = 0
                for decision in knowledge["decisions"]:
                    if decision.get("question") not in existing_questions:
                        existing.append(decision)
                        existing_questions.add(decision.get("question", ""))
                        new_count += 1
                self._write_entries(self._knowledge_dir / "decisions.json", existing[-MAX_KNOWLEDGE_ENTRIES:], "decision")
                imported.append(f"decisions(+{new_count})")
            else:
                self._write_entries(self._knowledge_dir / "decisions.json", knowledge["decisions"][-MAX_KNOWLEDGE_ENTRIES:], "decision")
                imported.append(f"decisions({len(knowledge['decisions'])})")

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
            existing_index = self._read_playbook_index()
            existing_titles = {e.get("title", "") for e in existing_index}
            for pb in knowledge["playbooks"]:
                if pb.get("title") not in existing_titles:
                    pb = self._ensure_playbook_fields(pb)
                    self._write_playbook_file(self._playbooks_dir / f"{pb['id']}.json", pb)
                    existing_index.append(self._playbook_index_entry(pb))
                    existing_titles.add(pb.get("title", ""))
                    new_count += 1
            if new_count:
                self._write_playbook_index(existing_index)
            imported.append(f"playbooks(+{new_count})" if merge else f"playbooks({len(knowledge['playbooks'])})")

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

        self._audit.log("import", "all", detail=f"imported from {input_path}")
        return {
            "status": "success",
            "mode": "merge" if merge else "overwrite",
            "imported": imported,
            "summary": plan.get("summary", {}),
            "conflicts": plan.get("conflicts", []),
            "source": input_path,
        }
