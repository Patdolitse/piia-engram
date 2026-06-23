"""Playbook storage, scoping, management, and execution plans (PlaybookMixin)."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .storage import (
    SIMILARITY_THRESHOLD,
    _ALLOWED_PLAYBOOK_UPDATE_FIELDS,
    _now_iso,
    _project_id,
    _read_json,
    _update_json,
    _write_json,
)

_BUILTIN_PLAYBOOKS: dict[str, dict] = {}


class PlaybookMixin:
    """Playbook CRUD / scope governance / management / execution plans."""

    # ------------------------------------------------------------------
    # Playbook CRUD — independent file-per-playbook storage
    # ------------------------------------------------------------------

    def _read_playbook_index(self) -> list[dict]:
        """Read the lightweight playbook index (with corpus decryption of title)."""
        data = _read_json(self._playbooks_dir / "_index.json")
        if not isinstance(data, list):
            return []
        if self._corpus_key:
            for entry in data:
                if "title" in entry and isinstance(entry["title"], str):
                    entry["title"] = self._crypto.corpus_decrypt(
                        entry["title"], self._corpus_key)
        return data

    def _write_playbook_index(self, entries: list[dict]) -> None:
        """Write the playbook index (with corpus encryption of title)."""
        if self._corpus_key:
            entries = [dict(e) for e in entries]
            for e in entries:
                if "title" in e and isinstance(e["title"], str):
                    e["title"] = self._crypto.corpus_encrypt(
                        e["title"], self._corpus_key)
        _write_json(self._playbooks_dir / "_index.json", entries)

    def _playbook_index_for_locked_mutation(self, entries: Any) -> list[dict]:
        if not isinstance(entries, list):
            return []
        result: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            if self._corpus_key and "title" in item and isinstance(item["title"], str):
                item["title"] = self._crypto.corpus_decrypt(item["title"], self._corpus_key)
            result.append(item)
        return result

    def _playbook_index_for_storage(self, entries: list[dict]) -> list[dict]:
        if not self._corpus_key:
            return entries
        result = [dict(entry) for entry in entries]
        for entry in result:
            if "title" in entry and isinstance(entry["title"], str):
                entry["title"] = self._crypto.corpus_encrypt(entry["title"], self._corpus_key)
        return result

    def _update_playbook_index(self, mutator) -> None:
        def _locked(current: Any) -> list[dict]:
            index = self._playbook_index_for_locked_mutation(current)
            updated = mutator(index)
            if updated is None:
                updated = index
            return self._playbook_index_for_storage(updated)

        _update_json(self._playbooks_dir / "_index.json", _locked, default=[])

    def _read_playbook_by_id(self, playbook_id: str) -> dict | None:
        """Read a single playbook file by ID (with corpus decryption)."""
        path = self._playbooks_dir / f"{playbook_id}.json"
        if not path.exists():
            return None
        pb = self._read_playbook_file(path) or None
        if pb:
            pb = self._ensure_playbook_fields(pb)
        return pb

    def _playbook_for_storage(self, playbook: dict) -> dict:
        if self._corpus_key:
            return self._crypto.encrypt_entry(playbook, self._corpus_key, "playbook")
        return playbook

    def _update_playbook_file_by_id(self, playbook_id: str, mutator) -> dict | None:
        """Apply ``mutator`` to one playbook file under that file's write lock."""
        path = self._playbooks_dir / f"{playbook_id}.json"
        if not path.exists():
            return None

        result_box: dict[str, dict | None] = {}

        def _locked(current: Any) -> dict:
            if not isinstance(current, dict):
                result_box["result"] = None
                return {}
            playbook = current
            if self._corpus_key:
                playbook = self._crypto.decrypt_entry(playbook, self._corpus_key, "playbook")
            playbook = self._ensure_playbook_fields(playbook)
            updated = mutator(playbook)
            if updated is None:
                updated = playbook
            result_box["result"] = updated
            return self._playbook_for_storage(updated)

        _update_json(path, _locked, default={})
        return result_box.get("result")

    @staticmethod
    def _extract_parameters(playbook: dict) -> list[str]:
        """Extract ${variable} placeholders from playbook steps and description."""
        _PARAM_RE = re.compile(r"\$\{(\w+)\}")
        params: list[str] = []
        seen: set[str] = set()
        # Scan steps (handle both string and dict formats)
        for step in playbook.get("steps", []):
            if isinstance(step, str):
                texts = [step]
            else:
                texts = [step.get(f) or "" for f in ("action", "detail")]
            for text in texts:
                for m in _PARAM_RE.finditer(text):
                    name = m.group(1)
                    if name not in seen:
                        params.append(name)
                        seen.add(name)
        # Scan description and outcome
        for field in ("description", "outcome"):
            text = playbook.get(field) or ""
            for m in _PARAM_RE.finditer(text):
                name = m.group(1)
                if name not in seen:
                    params.append(name)
                    seen.add(name)
        return params

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        """Return a clean list of strings from a scalar or sequence."""
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            values = value
        else:
            values = [value]
        return [str(item).strip() for item in values if str(item).strip()]

    @staticmethod
    def _playbook_text_list(
        value: Any,
        *,
        split_punctuation: bool = True,
    ) -> list[str]:
        """Normalize Playbook list-like text while preserving item order."""
        raw_items: list[Any]
        if value is None:
            raw_items = []
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]

        result: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            if isinstance(item, str):
                if split_punctuation:
                    parts = re.split(r"[\n,;，、；]+", item)
                else:
                    parts = re.split(r"[\n]+", item)
            else:
                parts = [str(item)]
            for part in parts:
                text = str(part).strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                result.append(text)
        return result

    @staticmethod
    def _normalize_playbook_steps(value: Any) -> list[dict]:
        """Normalize Playbook steps into {order, action, detail} dictionaries."""
        if not isinstance(value, list):
            return []
        steps: list[dict] = []
        next_order = 1
        for raw in value:
            if isinstance(raw, str):
                action = raw.strip()
                detail = ""
                order = next_order
            elif isinstance(raw, dict):
                action = str(raw.get("action") or "").strip()
                detail = str(raw.get("detail") or "").strip()
                try:
                    order = int(raw.get("order", next_order))
                except (TypeError, ValueError):
                    order = next_order
            else:
                continue
            if not action:
                continue
            steps.append({"order": order, "action": action, "detail": detail})
            next_order = max(next_order + 1, order + 1)
        return steps

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    @classmethod
    def _normalize_playbook_required_tools(
        cls,
        required_tools: Any,
        tool_refs: Any = None,
    ) -> list[dict]:
        """Normalize Playbook tool dependencies without resolving local paths."""
        tools: list[dict] = []
        seen: set[str] = set()

        def add_tool(raw: Any) -> None:
            if isinstance(raw, dict):
                name = str(raw.get("name") or "").strip()
                if not name:
                    return
                item = {
                    "name": name,
                    "purpose": str(raw.get("purpose") or "").strip(),
                    "optional": cls._truthy(raw.get("optional", False)),
                    "min_version": str(raw.get("min_version") or "").strip(),
                    "query": str(raw.get("query") or "").strip(),
                }
            else:
                name = str(raw or "").strip()
                if not name:
                    return
                item = {
                    "name": name,
                    "purpose": "",
                    "optional": False,
                    "min_version": "",
                    "query": "",
                }
            key = item["name"].lower()
            if key in seen:
                return
            seen.add(key)
            tools.append(item)

        raw_required = required_tools
        if raw_required is None:
            raw_required = []
        elif not isinstance(raw_required, (list, tuple, set)):
            raw_required = [raw_required]
        for raw in raw_required:
            add_tool(raw)

        raw_refs = tool_refs
        if raw_refs is None:
            raw_refs = []
        elif isinstance(raw_refs, str):
            raw_refs = re.split(r"[\n,;，、；]+", raw_refs)
        elif not isinstance(raw_refs, (list, tuple, set)):
            raw_refs = [raw_refs]
        for raw in raw_refs:
            add_tool(raw)

        return tools

    @staticmethod
    def _playbook_contract(entry: dict) -> dict:
        """Return metadata-only structural quality signals for a Playbook."""
        from . import quality_eval as _quality_eval

        verdict = _quality_eval.evaluate_candidate(entry)
        return {
            "schema_version": entry.get("schema_version", 1),
            "entry_type": verdict.get("entry_type", "playbook"),
            "accept": bool(verdict.get("accept", True)),
            "reasons": list(verdict.get("reasons") or []),
            "warnings": list(verdict.get("warnings") or []),
        }

    @staticmethod
    def _playbook_schema_version(value: Any) -> int:
        """Return the current Playbook contract version for bad legacy markers."""
        try:
            version = int(value or 1)
        except (TypeError, ValueError):
            return 1
        return version if version > 0 else 1

    def _normalize_playbook_scope(
        self,
        entry: dict,
        scope_type: str | None = None,
        project_folder: str | None = None,
        project_id: str | None = None,
        project_folders: list[str] | None = None,
        project_ids: list[str] | None = None,
    ) -> dict:
        """Return the canonical scope dict for a playbook.

        Legacy playbooks have no scope metadata; they read as global so older
        stores remain visible until the user runs a classification migration.
        """
        raw_scope = entry.get("scope") if isinstance(entry.get("scope"), dict) else {}
        folder = (
            project_folder
            if project_folder is not None
            else entry.get("project_folder") or raw_scope.get("project_folder")
        )
        pid = project_id or entry.get("project_id") or raw_scope.get("project_id")
        raw_type = (
            scope_type
            or entry.get("scope_type")
            or raw_scope.get("type")
            or ("project" if folder or pid else "global")
        )
        raw_type = str(raw_type or "global").strip().lower()
        if raw_type == "shared":
            folders = (
                self._string_list(project_folders)
                or self._string_list(entry.get("project_folders"))
                or self._string_list(raw_scope.get("project_folders"))
            )
            ids = (
                self._string_list(project_ids)
                or self._string_list(entry.get("project_ids"))
                or self._string_list(raw_scope.get("project_ids"))
            )
            ordered_ids: list[str] = []
            folders_by_id: dict[str, str] = {}
            seen: set[str] = set()
            for shared_folder in folders:
                shared_id = _project_id(shared_folder)
                if not shared_id or shared_id in seen:
                    continue
                seen.add(shared_id)
                ordered_ids.append(shared_id)
                folders_by_id[shared_id] = shared_folder
            for shared_id in ids:
                if not shared_id or shared_id in seen:
                    continue
                seen.add(shared_id)
                ordered_ids.append(shared_id)
            if not ordered_ids:
                return {"type": "global"}
            scope = {"type": "shared", "project_ids": ordered_ids}
            ordered_folders = [
                folders_by_id[shared_id]
                for shared_id in ordered_ids
                if shared_id in folders_by_id
            ]
            if ordered_folders:
                scope["project_folders"] = ordered_folders
            return scope
        if raw_type != "project":
            return {"type": "global"}
        if not pid and folder:
            pid = _project_id(str(folder))
        if not pid:
            return {"type": "global"}
        scope = {"type": "project", "project_id": str(pid)}
        if folder:
            scope["project_folder"] = str(folder)
        return scope

    @staticmethod
    def _apply_playbook_scope(entry: dict, scope: dict) -> dict:
        """Mirror canonical scope fields onto a playbook or index entry."""
        entry["scope"] = dict(scope)
        entry["scope_type"] = scope.get("type", "global")
        if scope.get("type") == "project":
            entry["project_id"] = scope.get("project_id", "")
            if scope.get("project_folder"):
                entry["project_folder"] = scope["project_folder"]
            entry.pop("project_ids", None)
            entry.pop("project_folders", None)
        elif scope.get("type") == "shared":
            entry["project_id"] = ""
            entry.pop("project_folder", None)
            entry["project_ids"] = list(scope.get("project_ids") or [])
            if scope.get("project_folders"):
                entry["project_folders"] = list(scope.get("project_folders") or [])
            else:
                entry.pop("project_folders", None)
        else:
            entry["project_id"] = ""
            entry.pop("project_folder", None)
            entry.pop("project_ids", None)
            entry.pop("project_folders", None)
        return entry

    def _playbook_visible_for_project(
        self, playbook: dict, project_folder: str | None = None,
    ) -> bool:
        """Whether a playbook should be visible in a project context."""
        scope = self._normalize_playbook_scope(playbook)
        if scope.get("type") == "global":
            return True
        if not project_folder:
            return False
        if scope.get("type") == "shared":
            return _project_id(project_folder) in set(scope.get("project_ids") or [])
        return scope.get("project_id") == _project_id(project_folder)

    def _same_playbook_scope(self, left: dict, right: dict) -> bool:
        """Scope equality for duplicate detection."""
        l_scope = self._normalize_playbook_scope(left)
        r_scope = self._normalize_playbook_scope(right)
        if l_scope.get("type") != r_scope.get("type"):
            return False
        if l_scope.get("type") == "project":
            return l_scope.get("project_id") == r_scope.get("project_id")
        if l_scope.get("type") == "shared":
            return set(l_scope.get("project_ids") or []) == set(
                r_scope.get("project_ids") or []
            )
        return True

    def _ensure_playbook_fields(self, entry: dict) -> dict:
        """Backfill metadata fields on a playbook entry."""
        if not isinstance(entry, dict):
            entry = {}
        entry["schema_version"] = self._playbook_schema_version(
            entry.get("schema_version")
        )
        if not entry.get("timestamp"):
            entry["timestamp"] = _now_iso()
        entry.setdefault("created_at", entry.get("timestamp", _now_iso()))
        entry.setdefault("last_reviewed", entry.get("created_at", _now_iso()))
        if not entry.get("id"):
            title = str(entry.get("title") or "")
            seed = f"{title}{entry.get('timestamp', '')}"
            entry["id"] = hashlib.sha256(seed.encode()).hexdigest()[:12]
        entry.setdefault("status", "active")
        entry.setdefault("access_count", 0)
        entry.setdefault("tier", "verified")
        if not isinstance(entry.get("related_ids"), list):
            entry["related_ids"] = []
        entry["triggers"] = self._playbook_text_list(
            entry.get("triggers"),
            split_punctuation=True,
        )
        entry["steps"] = self._normalize_playbook_steps(entry.get("steps"))
        entry["preconditions"] = self._playbook_text_list(
            entry.get("preconditions"),
            split_punctuation=False,
        )
        entry["pitfalls"] = self._playbook_text_list(
            entry.get("pitfalls"),
            split_punctuation=False,
        )
        required_tools = self._normalize_playbook_required_tools(
            entry.get("required_tools"),
            entry.get("tool_refs"),
        )
        if required_tools:
            entry["required_tools"] = required_tools
        else:
            entry.pop("required_tools", None)
        entry.pop("tool_refs", None)
        entry.setdefault("version", 1)
        scope = self._normalize_playbook_scope(entry)
        self._apply_playbook_scope(entry, scope)
        entry["contract"] = self._playbook_contract(entry)
        return entry

    def _playbook_index_entry(self, pb: dict) -> dict:
        """Extract lightweight index entry from a full playbook."""
        entry = {
            "id": pb.get("id", ""),
            "title": pb.get("title", ""),
            "triggers": pb.get("triggers", []),
            "domain": pb.get("domain", ""),
            "status": pb.get("status", "active"),
            "updated_at": pb.get("last_updated") or pb.get("created_at") or _now_iso(),
        }
        if pb.get("builtin_name"):
            entry["builtin_name"] = pb.get("builtin_name", "")
        return self._apply_playbook_scope(entry, self._normalize_playbook_scope(pb))

    def add_playbook(
        self,
        playbook: dict,
        source_tool: str = "",
        **extra: Any,
    ) -> dict:
        """Add an operational playbook.

        Each playbook is stored as an individual file in ~/.engram/playbooks/.
        An index file (_index.json) is maintained for fast search.
        """
        allow_internal_provenance = extra.pop("_allow_internal_provenance", False) is True
        new_pb = dict(playbook)
        if source_tool:
            new_pb["source_tool"] = source_tool
        for key, value in extra.items():
            if value is not None:
                new_pb[key] = value
        if not allow_internal_provenance:
            from .core import _strip_untrusted_freshness_provenance

            _strip_untrusted_freshness_provenance(new_pb)

        new_pb = self._repair_incoming_text(new_pb)
        if not new_pb.get("title"):
            return {"error": "Playbook must have a title"}

        new_pb["timestamp"] = new_pb.get("timestamp") or _now_iso()
        new_pb = self._ensure_playbook_fields(new_pb)

        # Duplicate detection against existing playbooks
        index = self._read_playbook_index()
        new_title = str(new_pb.get("title", ""))
        for entry in index:
            if entry.get("status") != "active":
                continue
            if not self._same_playbook_scope(new_pb, entry):
                continue
            sim = self._bigram_similarity(new_title, entry.get("title", ""))
            if sim >= SIMILARITY_THRESHOLD:
                return {
                    "status": "duplicate",
                    "similarity": round(sim, 2),
                    "existing_id": entry.get("id"),
                    "existing_title": entry.get("title"),
                    "message": f"与现有 Playbook 相似度 {sim:.0%}，未重复添加",
                }

        self._write_playbook_and_index(new_pb)

        self._audit.log("write", "playbooks", detail=new_title[:100])
        if new_pb.get("domain"):
            for _d in new_pb["domain"].split(","):
                _d = _d.strip()
                if _d:
                    self.increment_domain_usage(_d)
        return new_pb

    @staticmethod
    def available_builtin_playbooks() -> list[str]:
        """Return built-in Playbook template names."""
        return sorted(_BUILTIN_PLAYBOOKS)

    def builtin_playbook_template(
        self,
        name: str,
        project_folder: str | None = None,
    ) -> dict:
        """Return a normalized built-in Playbook template without writing it."""
        key = str(name or "").strip().lower()
        if key not in _BUILTIN_PLAYBOOKS:
            return {
                "error": f"Unknown builtin playbook: {name}",
                "available": self.available_builtin_playbooks(),
            }
        template = deepcopy(_BUILTIN_PLAYBOOKS[key])
        scope_type = "project" if project_folder else "global"
        scope = self._normalize_playbook_scope(
            template, scope_type=scope_type, project_folder=project_folder,
        )
        self._apply_playbook_scope(template, scope)
        return self._ensure_playbook_fields(template)

    def _find_existing_builtin_playbook(self, template: dict) -> dict | None:
        builtin_name = str(template.get("builtin_name") or "").strip().lower()
        title = str(template.get("title") or "").strip().lower()
        for entry in self._read_playbook_index():
            if entry.get("status") != "active":
                continue
            entry_builtin_name = str(entry.get("builtin_name") or "").strip().lower()
            if builtin_name and entry_builtin_name and entry_builtin_name != builtin_name:
                continue
            if not entry_builtin_name:
                if str(entry.get("title") or "").strip().lower() != title:
                    continue
            if self._same_playbook_scope(template, entry):
                return entry
        return None

    def install_builtin_playbook(
        self,
        name: str,
        *,
        project_folder: str | None = None,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        """Install a built-in Playbook template with dry-run and idempotency.

        Built-ins are verified local templates, but installation still defaults
        to preview-only so setup/CLI callers never mutate stores by accident.
        """
        template = self.builtin_playbook_template(name, project_folder=project_folder)
        if "error" in template:
            return template

        existing = self._find_existing_builtin_playbook(template)
        if existing:
            return {
                "dry_run": bool(dry_run or not confirm),
                "status": "already_installed",
                "existing_id": existing.get("id", ""),
                "playbook": existing,
            }

        effective_dry_run = bool(dry_run or not confirm)
        if effective_dry_run:
            return {
                "dry_run": True,
                "requires_confirmation": not confirm,
                "status": "would_install",
                "playbook": template,
            }

        installed = self.add_playbook(
            template,
            source_tool=str(template.get("source_tool") or "engram_builtin"),
        )
        if "error" in installed or installed.get("status") == "duplicate":
            return {
                "dry_run": False,
                "status": installed.get("status", "error"),
                "playbook": installed,
                "existing_id": installed.get("existing_id", ""),
            }
        return {
            "dry_run": False,
            "status": "installed",
            "playbook_id": installed.get("id", ""),
            "playbook": installed,
        }

    def get_playbooks(
        self,
        domain: str | None = None,
        limit: int | None = 20,
        project_folder: str | None = None,
        _update_access: bool = True,
    ) -> list[dict]:
        """List active playbooks, optionally filtered by domain."""
        index = self._read_playbook_index()
        result = []
        for entry in index:
            if entry.get("status") != "active":
                continue
            if domain:
                pb_domains = {d.strip() for d in (entry.get("domain") or "").split(",") if d.strip()}
                if domain not in pb_domains:
                    continue
            pb = self._read_playbook_by_id(entry.get("id", ""))
            if pb and self._playbook_visible_for_project(pb, project_folder):
                result.append(pb)

        result = result[-limit:] if limit is not None else result

        if _update_access and result:
            now = _now_iso()
            for pb in result:
                pb["last_reviewed"] = now
                pb["access_count"] = pb.get("access_count", 0) + 1
                self._write_playbook_file(self._playbooks_dir / f"{pb['id']}.json", pb)

        self._audit.log("read", "playbooks", detail=f"returned {len(result)} items")
        if _update_access:
            # Model-facing read: write-back above used the raw (still-encrypted)
            # objects; sanitize only the returned copies so leaked ciphertext is
            # never surfaced as content.
            result = self._display_sanitize(result, "playbook")
        return result

    def get_playbook(
        self,
        playbook_id: str,
        _update_access: bool = True,
        project_folder: str | None = None,
        confirm_cross_project: bool = False,
    ) -> dict:
        """Get a single playbook by ID. Includes extracted parameters list.

        When a project_folder is supplied, project-scoped playbooks from other
        projects are refused unless confirm_cross_project=True. Calls without a
        project context preserve the legacy direct-ID behavior.
        """
        pb = self._read_playbook_by_id(playbook_id)
        if pb is None:
            return {"error": f"Playbook not found: {playbook_id}"}
        if project_folder is not None and not self._playbook_visible_for_project(pb, project_folder):
            if not confirm_cross_project:
                return {
                    "error": "cross_project_playbook",
                    "playbook_id": playbook_id,
                    "scope": pb.get("scope", {"type": "global"}),
                    "project_folder": project_folder,
                    "message": "Playbook belongs to another project; pass confirm_cross_project=True to use it explicitly.",
                }

        if _update_access:
            pb["last_reviewed"] = _now_iso()
            pb["access_count"] = pb.get("access_count", 0) + 1
            self._write_playbook_file(self._playbooks_dir / f"{playbook_id}.json", pb)
            # Write-back above used the raw (still-encrypted) object; from here
            # on operate on a display-safe copy so leaked ciphertext is never
            # surfaced as content.
            pb = self._display_sanitize_one(pb, "playbook")

        # Always include dynamic parameters extraction
        pb["parameters"] = self._extract_parameters(pb)
        return pb

    def get_recent_playbooks(
        self, limit: int = 5, project_folder: str | None = None,
    ) -> list[dict]:
        """Return recently used active playbooks, sorted by last_reviewed descending."""
        all_pbs = self._export_playbooks()
        active = [
            pb for pb in all_pbs
            if pb.get("status") == "active"
            and self._playbook_visible_for_project(pb, project_folder)
        ]
        active.sort(key=lambda pb: pb.get("last_reviewed", ""), reverse=True)
        result = active[:limit]
        for pb in result:
            pb["parameters"] = self._extract_parameters(pb)
        return result

    @staticmethod
    def _playbook_text_for_classification(pb: dict) -> str:
        parts: list[str] = [
            str(pb.get("title", "")),
            str(pb.get("domain", "")),
            str(pb.get("description", "")),
            " ".join(str(t) for t in pb.get("triggers", []) if t),
            " ".join(str(p) for p in pb.get("pitfalls", []) if p),
        ]
        for step in pb.get("steps", []):
            if isinstance(step, str):
                parts.append(step)
            elif isinstance(step, dict):
                parts.append(str(step.get("action", "")))
                parts.append(str(step.get("detail", "")))
        return " ".join(parts).lower()

    def classify_legacy_playbooks(
        self,
        project_folders: list[str] | None = None,
    ) -> dict:
        """Dry-run legacy playbook scope classification.

        This intentionally does not mutate stored playbooks. It produces a
        reviewable migration plan with confidence and evidence so old users can
        batch-apply only the high-confidence items later.
        """
        projects: list[dict] = []
        if project_folders is not None:
            for folder in project_folders:
                projects.append({
                    "folder": str(folder),
                    "title": self._sanitize_project(str(folder)),
                })
        else:
            projects = self.list_projects()

        project_terms: list[dict] = []
        for project in projects:
            folder = str(project.get("folder") or project.get("project_folder") or "")
            title = str(project.get("title") or "")
            terms = {term.strip().lower() for term in [title, Path(folder).name] if term}
            terms = {term for term in terms if len(term) >= 3}
            if folder and terms:
                project_terms.append({
                    "folder": folder,
                    "project_id": _project_id(folder),
                    "terms": sorted(terms),
                })

        global_markers = {
            "global", "universal", "common", "general", "shared",
            "cross-project", "通用", "共通", "全局",
        }

        suggestions = []
        for pb in self._export_playbooks():
            if pb.get("status") != "active":
                continue
            text = self._playbook_text_for_classification(pb)
            project_matches: list[dict] = []
            for project in project_terms:
                evidence = [term for term in project["terms"] if term in text]
                if evidence:
                    project_matches.append({
                        "folder": project["folder"],
                        "project_id": project["project_id"],
                        "evidence": evidence,
                    })

            project_matches.sort(key=lambda match: len(match["evidence"]), reverse=True)
            if (
                len(project_matches) >= 2
                and len(project_matches[0]["evidence"]) == len(project_matches[1]["evidence"])
            ):
                top_count = len(project_matches[0]["evidence"])
                shared_matches = [
                    match for match in project_matches
                    if len(match["evidence"]) == top_count
                ]
                confidence = min(
                    0.95,
                    0.7 + 0.05 * len(shared_matches)
                    + 0.03 * sum(len(match["evidence"]) for match in shared_matches),
                )
                suggested_scope = {
                    "type": "shared",
                    "project_ids": [match["project_id"] for match in shared_matches],
                    "project_folders": [match["folder"] for match in shared_matches],
                }
                evidence = [
                    f"matched project term: {term}"
                    for match in shared_matches
                    for term in match["evidence"]
                ]
            elif project_matches:
                match = project_matches[0]
                confidence = min(0.95, 0.65 + 0.1 * len(match["evidence"]))
                suggested_scope = {
                    "type": "project",
                    "project_id": match["project_id"],
                    "project_folder": match["folder"],
                }
                evidence = [f"matched project term: {term}" for term in match["evidence"]]
            elif any(marker in text for marker in global_markers):
                confidence = 0.75
                suggested_scope = {"type": "global"}
                evidence = ["matched global/common marker"]
            else:
                confidence = 0.35
                suggested_scope = {"type": "needs_review"}
                evidence = ["no strong project or global evidence"]

            suggestions.append({
                "id": pb.get("id", ""),
                "title": pb.get("title", ""),
                "current_scope": pb.get("scope", {"type": "global"}),
                "suggested_scope": suggested_scope,
                "confidence": round(confidence, 2),
                "evidence": evidence,
                "apply_ready": confidence >= 0.7 and suggested_scope.get("type") != "needs_review",
            })

        return {
            "dry_run": True,
            "total": len(suggestions),
            "suggestions": suggestions,
        }

    def _write_playbook_and_index(self, pb: dict) -> None:
        """Persist a playbook and keep the lightweight index in sync."""
        playbook_id = str(pb.get("id") or "")
        if not playbook_id:
            raise ValueError("missing playbook id")
        self._write_playbook_file(self._playbooks_dir / f"{playbook_id}.json", pb)

        idx_entry = self._playbook_index_entry(pb)

        def _upsert(index: list[dict]) -> list[dict]:
            for i, entry in enumerate(index):
                if entry.get("id") == playbook_id:
                    index[i] = idx_entry
                    break
            else:
                index.append(idx_entry)
            return index

        self._update_playbook_index(_upsert)

    @staticmethod
    def _scope_impact_summary(
        *,
        pending_key: str,
        completed_key: str,
        pending: list[dict],
        completed: list[dict],
        skipped: list[dict],
        requires_confirmation: bool,
    ) -> dict:
        """Return metadata-only batch impact counts for management surfaces."""
        target_scope_counts: dict[str, int] = {}
        for change in [*pending, *completed]:
            scope = change.get("to_scope")
            if not isinstance(scope, dict):
                continue
            scope_type = str(scope.get("type") or "unknown")
            target_scope_counts[scope_type] = target_scope_counts.get(scope_type, 0) + 1

        skipped_reason_counts: dict[str, int] = {}
        for item in skipped:
            reason = str(item.get("reason") or "unknown")
            skipped_reason_counts[reason] = skipped_reason_counts.get(reason, 0) + 1

        return {
            pending_key: len(pending),
            completed_key: len(completed),
            "skipped_count": len(skipped),
            "target_scope_counts": target_scope_counts,
            "skipped_reason_counts": skipped_reason_counts,
            "requires_confirmation": bool(requires_confirmation),
        }

    def apply_legacy_playbook_scope_suggestions(
        self,
        project_folders: list[str] | None = None,
        playbook_ids: list[str] | None = None,
        min_confidence: float = 0.7,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        """Apply high-confidence legacy Playbook scope suggestions.

        The default is a write-free preview. Actual migration requires
        ``dry_run=False`` and ``confirm=True`` so old-user data is never
        silently reorganized.
        """
        effective_dry_run = bool(dry_run or not confirm)
        selected_ids = set(playbook_ids or [])
        classification = self.classify_legacy_playbooks(project_folders=project_folders)
        now = _now_iso()
        would_apply: list[dict] = []
        applied: list[dict] = []
        skipped: list[dict] = []

        for suggestion in classification.get("suggestions", []):
            playbook_id = str(suggestion.get("id") or "")
            if selected_ids and playbook_id not in selected_ids:
                continue

            if (
                not suggestion.get("apply_ready")
                or float(suggestion.get("confidence") or 0) < min_confidence
            ):
                skipped.append({
                    "id": playbook_id,
                    "title": suggestion.get("title", ""),
                    "reason": "not_apply_ready",
                    "suggested_scope": suggestion.get("suggested_scope"),
                    "confidence": suggestion.get("confidence", 0),
                })
                continue

            pb = self._read_playbook_by_id(playbook_id)
            if pb is None:
                skipped.append({
                    "id": playbook_id,
                    "title": suggestion.get("title", ""),
                    "reason": "not_found",
                })
                continue

            current_scope = self._normalize_playbook_scope(pb)
            if current_scope.get("type") != "global":
                skipped.append({
                    "id": playbook_id,
                    "title": pb.get("title", ""),
                    "reason": "already_scoped",
                    "current_scope": current_scope,
                    "suggested_scope": suggestion.get("suggested_scope"),
                    "confidence": suggestion.get("confidence", 0),
                })
                continue

            target_scope = self._normalize_playbook_scope(
                {"scope": suggestion.get("suggested_scope") or {}}
            )
            if target_scope.get("type") not in {"global", "project", "shared"}:
                skipped.append({
                    "id": playbook_id,
                    "title": suggestion.get("title", ""),
                    "reason": "invalid_scope",
                    "suggested_scope": suggestion.get("suggested_scope"),
                })
                continue

            if current_scope == target_scope:
                skipped.append({
                    "id": playbook_id,
                    "title": pb.get("title", ""),
                    "reason": "unchanged",
                    "suggested_scope": target_scope,
                    "confidence": suggestion.get("confidence", 0),
                })
                continue

            change = {
                "id": playbook_id,
                "title": pb.get("title", suggestion.get("title", "")),
                "from_scope": current_scope,
                "to_scope": target_scope,
                "confidence": suggestion.get("confidence", 0),
                "evidence": suggestion.get("evidence", []),
            }
            if effective_dry_run:
                would_apply.append(change)
                continue

            history = list(pb.get("scope_migration_history") or [])
            history.append({
                "timestamp": now,
                "from_scope": current_scope,
                "to_scope": target_scope,
                "confidence": suggestion.get("confidence", 0),
                "evidence": suggestion.get("evidence", []),
                "reason": "legacy_playbook_scope_classification",
            })
            self._apply_playbook_scope(pb, target_scope)
            pb["scope_migration_history"] = history
            pb["last_updated"] = now
            pb["version"] = pb.get("version", 1) + 1
            self._write_playbook_and_index(pb)
            applied.append(change)

        if applied:
            self._audit.log(
                "write", "playbooks",
                detail=f"applied scope migration to {len(applied)} playbooks",
            )

        return {
            "dry_run": effective_dry_run,
            "requires_confirmation": not confirm,
            "total": classification.get("total", 0),
            "would_apply": would_apply,
            "applied": applied,
            "skipped": skipped,
            "impact": self._scope_impact_summary(
                pending_key="would_apply_count",
                completed_key="applied_count",
                pending=would_apply,
                completed=applied,
                skipped=skipped,
                requires_confirmation=not confirm,
            ),
        }

    def rollback_playbook_scope_migration(
        self,
        playbook_ids: list[str] | None = None,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        """Rollback the latest scope migration for selected Playbooks."""
        effective_dry_run = bool(dry_run or not confirm)
        selected_ids = set(playbook_ids or [])
        candidates = self._export_playbooks()
        if selected_ids:
            seen = {pb.get("id") for pb in candidates}
            for missing_id in sorted(selected_ids - seen):
                candidates.append({"id": missing_id, "_missing": True})

        would_rollback: list[dict] = []
        rolled_back: list[dict] = []
        skipped: list[dict] = []
        now = _now_iso()

        for pb in candidates:
            playbook_id = str(pb.get("id") or "")
            if selected_ids and playbook_id not in selected_ids:
                continue
            if pb.get("_missing"):
                skipped.append({"id": playbook_id, "reason": "not_found"})
                continue

            history = list(pb.get("scope_migration_history") or [])
            if not history:
                skipped.append({
                    "id": playbook_id,
                    "title": pb.get("title", ""),
                    "reason": "no_migration_history",
                })
                continue

            last = history[-1]
            target_scope = self._normalize_playbook_scope(
                {"scope": last.get("from_scope") or {}}
            )
            current_scope = self._normalize_playbook_scope(pb)
            change = {
                "id": playbook_id,
                "title": pb.get("title", ""),
                "from_scope": current_scope,
                "to_scope": target_scope,
                "rolled_back_migration": last,
            }
            if effective_dry_run:
                would_rollback.append(change)
                continue

            history.pop()
            self._apply_playbook_scope(pb, target_scope)
            pb["scope_migration_history"] = history
            pb["last_updated"] = now
            pb["version"] = pb.get("version", 1) + 1
            self._write_playbook_and_index(pb)
            rolled_back.append(change)

        if rolled_back:
            self._audit.log(
                "write", "playbooks",
                detail=f"rolled back scope migration for {len(rolled_back)} playbooks",
            )

        return {
            "dry_run": effective_dry_run,
            "requires_confirmation": not confirm,
            "would_rollback": would_rollback,
            "rolled_back": rolled_back,
            "skipped": skipped,
            "impact": self._scope_impact_summary(
                pending_key="would_rollback_count",
                completed_key="rolled_back_count",
                pending=would_rollback,
                completed=rolled_back,
                skipped=skipped,
                requires_confirmation=not confirm,
            ),
        }

    @staticmethod
    def _playbook_review_summary(pb: dict, max_chars: int = 160) -> str:
        """Short metadata preview so reviewers can judge scope without
        fetching the full playbook body for every queue item."""
        summary = str(pb.get("description") or "").strip()
        if not summary:
            steps = pb.get("steps") or []
            if isinstance(steps, list):
                parts = [
                    str(s.get("action") or "").strip()
                    for s in steps if isinstance(s, dict)
                ]
                summary = " → ".join(p for p in parts if p)
        if not summary:
            raw = pb.get("steps_json")
            if isinstance(raw, str) and raw.strip():
                try:
                    arr = json.loads(raw)
                    if isinstance(arr, list) and arr:
                        summary = str(arr[0]).strip()
                except (ValueError, TypeError):
                    pass
        if not summary:
            summary = str(pb.get("domain") or "").strip()
        if len(summary) > max_chars:
            summary = summary[: max_chars - 1] + "…"
        return summary

    def get_playbook_scope_review_queue(
        self,
        project_folders: list[str] | None = None,
        include_resolved: bool = False,
        limit: int | None = None,
    ) -> dict:
        """Return unresolved legacy Playbooks that need manual scope review."""
        classification = self.classify_legacy_playbooks(project_folders=project_folders)
        items: list[dict] = []
        for suggestion in classification.get("suggestions", []):
            playbook_id = str(suggestion.get("id") or "")
            pb = self._read_playbook_by_id(playbook_id)
            if pb is None or pb.get("status") != "active":
                continue
            current_scope = self._normalize_playbook_scope(pb)
            if current_scope.get("type") != "global":
                continue
            review_status = str(pb.get("scope_review_status") or "unresolved")
            if review_status in {"resolved", "skipped"} and not include_resolved:
                continue
            if (
                suggestion.get("suggested_scope", {}).get("type") != "needs_review"
                and not include_resolved
            ):
                continue
            item = dict(suggestion)
            item["scope_review_status"] = review_status
            item["scope_review_history"] = list(pb.get("scope_review_history") or [])
            item["summary"] = self._playbook_review_summary(pb)
            items.append(item)

        if limit is not None:
            items = items[:limit]
        return {
            "dry_run": True,
            "total": len(items),
            "items": items,
        }

    def resolve_playbook_scope_review(
        self,
        playbook_id: str,
        action: str,
        project_folder: str | None = None,
        project_folders: list[str] | None = None,
        note: str = "",
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        """Resolve one Playbook scope review item by keeping, assigning, or skipping."""
        action = str(action or "").strip().lower()
        if action not in {"accept_global", "accept_project", "accept_shared", "skip"}:
            return {
                "error": "invalid_action",
                "allowed_actions": [
                    "accept_global",
                    "accept_project",
                    "accept_shared",
                    "skip",
                ],
            }
        # Tolerant aliasing: callers often mix up the singular/plural folder
        # parameters. When intent is unambiguous, accept either spelling.
        if action == "accept_project" and not project_folder:
            folders = [f for f in (project_folders or []) if str(f).strip()]
            if len(folders) == 1:
                project_folder = folders[0]
        if action == "accept_shared" and not project_folders and project_folder:
            project_folders = [project_folder]

        if action == "accept_project" and not project_folder:
            return {
                "error": "project_folder_required",
                "hint": "accept_project needs project_folder='<folder path>' "
                        "(a single-item project_folders list is also accepted)",
            }
        if action == "accept_shared" and not project_folders:
            return {
                "error": "project_folders_required",
                "hint": "accept_shared needs project_folders=['<folder>', ...] "
                        "(project_folder alone is also accepted as one entry)",
            }

        pb = self._read_playbook_by_id(playbook_id)
        if pb is None:
            return {"error": f"Playbook not found: {playbook_id}"}

        current_scope = self._normalize_playbook_scope(pb)
        if action == "accept_project":
            target_scope = self._normalize_playbook_scope(
                {}, scope_type="project", project_folder=project_folder,
            )
        elif action == "accept_shared":
            target_scope = self._normalize_playbook_scope(
                {}, scope_type="shared", project_folders=project_folders,
            )
        elif action == "accept_global":
            target_scope = self._normalize_playbook_scope({}, scope_type="global")
        else:
            target_scope = current_scope

        change = {
            "id": playbook_id,
            "title": pb.get("title", ""),
            "action": action,
            "from_scope": current_scope,
            "to_scope": target_scope,
            "note": note,
        }
        effective_dry_run = bool(dry_run or not confirm)
        if effective_dry_run:
            return {
                "dry_run": True,
                "requires_confirmation": not confirm,
                "would_update": change,
            }

        now = _now_iso()
        history = list(pb.get("scope_review_history") or [])
        history.append({
            "timestamp": now,
            "action": action,
            "from_scope": current_scope,
            "to_scope": target_scope,
            "note": note,
            "reason": "manual_playbook_scope_review",
        })
        if action != "skip":
            self._apply_playbook_scope(pb, target_scope)
        pb["scope_review_status"] = "skipped" if action == "skip" else "resolved"
        pb["scope_review_resolution"] = action
        pb["scope_review_history"] = history
        pb["last_updated"] = now
        pb["version"] = pb.get("version", 1) + 1
        self._write_playbook_and_index(pb)
        self._audit.log(
            "write", "playbooks",
            detail=f"resolved scope review for {playbook_id}: {action}",
        )
        return {
            "dry_run": False,
            "requires_confirmation": False,
            "updated": change,
        }

    def update_playbook(self, playbook_id: str, updates: dict) -> dict:
        """Update fields on a playbook entry."""
        updates = self._repair_incoming_text(dict(updates))

        def _apply(pb: dict) -> dict:
            for key, value in updates.items():
                if key in _ALLOWED_PLAYBOOK_UPDATE_FIELDS:
                    pb[key] = value
            pb["last_updated"] = _now_iso()
            pb["version"] = pb.get("version", 1) + 1
            return self._ensure_playbook_fields(pb)

        result = self._update_playbook_file_by_id(playbook_id, _apply)
        if result is None:
            return {"error": f"Playbook not found: {playbook_id}"}

        idx_entry = self._playbook_index_entry(result)

        def _upsert(index: list[dict]) -> list[dict]:
            for i, entry in enumerate(index):
                if entry.get("id") == playbook_id:
                    index[i] = idx_entry
                    break
            else:
                index.append(idx_entry)
            return index

        self._update_playbook_index(_upsert)
        self._audit.log("write", "playbooks", detail=f"updated {playbook_id}")
        return result

    def archive_playbook(self, playbook_id: str) -> dict:
        """Mark a playbook as outdated without deleting it."""
        return self.update_playbook(playbook_id, {"status": "outdated"})

    @staticmethod
    def _normalize_playbook_status_filter(status: str | None) -> str:
        value = str(status or "all").strip().lower()
        aliases = {
            "archived": "outdated",
            "archive": "outdated",
            "hidden": "deleted",
            "trash": "deleted",
        }
        return aliases.get(value, value)

    def _playbook_management_entry(self, pb: dict, include_content: bool = False) -> dict:
        """Return a Playbook entry suitable for management views."""
        if include_content:
            return dict(pb)
        scope = self._normalize_playbook_scope(pb)
        scope_type = str(scope.get("type") or "global")
        if scope_type == "shared":
            project_count = len(scope.get("project_ids") or [])
        elif scope_type == "project":
            project_count = 1
        else:
            project_count = 0
        public_scope = dict(scope)
        public_scope.pop("project_folder", None)
        public_scope.pop("project_folders", None)
        return {
            "id": pb.get("id", ""),
            "status": pb.get("status", "active"),
            "scope": public_scope,
            "scope_type": scope_type,
            "project_count": project_count,
            "scope_review_status": pb.get("scope_review_status", ""),
            "scope_review_resolution": pb.get("scope_review_resolution", ""),
            "created_at": pb.get("created_at", ""),
            "last_updated": pb.get("last_updated", ""),
            "last_reviewed": pb.get("last_reviewed", ""),
            "version": pb.get("version", 1),
            "deleted_at": pb.get("deleted_at", ""),
        }

    def list_playbooks_for_management(
        self,
        status: str = "all",
        project_folder: str | None = None,
        scope_type: str = "all",
        include_content: bool = False,
        limit: int | None = None,
    ) -> dict:
        """List Playbooks for management UI/API surfaces, including hidden items."""
        status_filter = self._normalize_playbook_status_filter(status)
        scope_filter = str(scope_type or "all").strip().lower()
        valid_statuses = {"all", "active", "outdated", "staging", "deleted"}
        valid_scopes = {"all", "global", "project", "shared"}
        if status_filter not in valid_statuses:
            return {"error": f"Invalid status {status!r}; must be one of {sorted(valid_statuses)}"}
        if scope_filter not in valid_scopes:
            return {"error": f"Invalid scope_type {scope_type!r}; must be one of {sorted(valid_scopes)}"}
        if limit is not None and limit < 0:
            return {"error": "limit_must_be_positive"}

        items: list[dict] = []
        for pb in self._export_playbooks():
            pb_status = self._normalize_playbook_status_filter(pb.get("status", "active"))
            if status_filter != "all" and pb_status != status_filter:
                continue
            scope = self._normalize_playbook_scope(pb)
            if scope_filter != "all" and scope.get("type") != scope_filter:
                continue
            if project_folder and not self._playbook_visible_for_project(pb, project_folder):
                continue
            items.append(self._playbook_management_entry(pb, include_content=include_content))

        items.sort(
            key=lambda item: (
                item.get("last_updated")
                or item.get("last_reviewed")
                or item.get("created_at")
                or ""
            ),
            reverse=True,
        )
        if limit is not None:
            items = items[:limit]
        self._audit.log("read", "playbooks", detail=f"management list returned {len(items)} items")
        return {
            "total": len(items),
            "status": status_filter,
            "scope_type": scope_filter,
            "items": items,
        }

    def delete_playbook(
        self,
        playbook_id: str,
        reason: str = "",
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        """Soft-delete a Playbook so it is hidden but recoverable."""
        pb = self._read_playbook_by_id(playbook_id)
        if pb is None:
            return {"error": f"Playbook not found: {playbook_id}"}

        current_status = self._normalize_playbook_status_filter(pb.get("status", "active"))
        if current_status == "deleted":
            return {"error": "playbook_already_deleted", "playbook_id": playbook_id}
        change = {
            "id": playbook_id,
            "from_status": current_status,
            "to_status": "deleted",
            "soft_delete": True,
        }
        effective_dry_run = bool(dry_run or not confirm)
        if effective_dry_run:
            return {
                "dry_run": True,
                "requires_confirmation": not confirm,
                "would_delete": change,
            }

        now = _now_iso()
        history = list(pb.get("deletion_history") or [])
        history.append({
            "timestamp": now,
            "action": "delete",
            "from_status": current_status,
            "to_status": "deleted",
            "reason": reason,
        })
        pb["status"] = "deleted"
        pb["deleted_at"] = now
        pb["deletion_reason"] = reason
        pb["deletion_history"] = history
        pb["last_updated"] = now
        pb["version"] = pb.get("version", 1) + 1
        self._write_playbook_and_index(pb)
        self._audit.log("write", "playbooks", detail=f"soft-deleted {playbook_id}")
        return {
            "dry_run": False,
            "requires_confirmation": False,
            "deleted": change,
        }

    def restore_playbook(
        self,
        playbook_id: str,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        """Restore a deleted/outdated Playbook to active status."""
        pb = self._read_playbook_by_id(playbook_id)
        if pb is None:
            return {"error": f"Playbook not found: {playbook_id}"}

        current_status = self._normalize_playbook_status_filter(pb.get("status", "active"))
        if current_status == "active":
            return {"error": "playbook_already_active", "playbook_id": playbook_id}
        change = {
            "id": playbook_id,
            "from_status": current_status,
            "to_status": "active",
        }
        effective_dry_run = bool(dry_run or not confirm)
        if effective_dry_run:
            return {
                "dry_run": True,
                "requires_confirmation": not confirm,
                "would_restore": change,
            }

        now = _now_iso()
        history = list(pb.get("deletion_history") or [])
        history.append({
            "timestamp": now,
            "action": "restore",
            "from_status": current_status,
            "to_status": "active",
        })
        pb["status"] = "active"
        pb["restored_at"] = now
        pb["deletion_history"] = history
        pb["last_updated"] = now
        pb["version"] = pb.get("version", 1) + 1
        self._write_playbook_and_index(pb)
        self._audit.log("write", "playbooks", detail=f"restored {playbook_id}")
        return {
            "dry_run": False,
            "requires_confirmation": False,
            "restored": change,
        }

    def merge_playbooks(self, target_id: str, source: dict) -> dict:
        """Merge steps and pitfalls from *source* dict into existing playbook *target_id*.

        Steps are de-duplicated by action text similarity. Pitfalls and triggers
        are union-merged. The target playbook is updated in-place.
        """
        target = self._read_playbook_by_id(target_id)
        if target is None:
            return {"error": f"Playbook not found: {target_id}"}

        # Merge steps (de-dup by similarity, handle both string and dict formats)
        def _step_action(s: Any) -> str:
            return s if isinstance(s, str) else s.get("action", "")

        def _step_order(s: Any) -> int:
            return s.get("order", 0) if isinstance(s, dict) else 0

        existing_actions = {_step_action(s): True for s in target.get("steps", [])}
        next_order = max((_step_order(s) for s in target.get("steps", [])), default=0)
        merged_steps = list(target.get("steps", []))
        for s in source.get("steps", []):
            action = _step_action(s)
            # Skip if already exists (exact or highly similar)
            is_dup = False
            for existing_action in existing_actions:
                if self._bigram_similarity(action, existing_action) >= 0.6:
                    is_dup = True
                    break
            if not is_dup and action:
                next_order += 1
                merged_steps.append({"order": next_order, "action": action})
                existing_actions[action] = True

        # Merge pitfalls (union)
        existing_pitfalls = set(target.get("pitfalls", []))
        merged_pitfalls = list(target.get("pitfalls", []))
        for p in source.get("pitfalls", []):
            if p not in existing_pitfalls:
                merged_pitfalls.append(p)
                existing_pitfalls.add(p)

        # Merge triggers (union)
        existing_triggers = set(target.get("triggers", []))
        merged_triggers = list(target.get("triggers", []))
        for t in source.get("triggers", []):
            if t not in existing_triggers:
                merged_triggers.append(t)
                existing_triggers.add(t)

        existing_tools = self._normalize_playbook_required_tools(
            target.get("required_tools"),
            target.get("tool_refs"),
        )
        merged_tools = list(existing_tools)
        existing_tool_names = {tool["name"].lower() for tool in merged_tools}
        for tool in self._normalize_playbook_required_tools(
            source.get("required_tools"),
            source.get("tool_refs"),
        ):
            key = tool["name"].lower()
            if key in existing_tool_names:
                continue
            existing_tool_names.add(key)
            merged_tools.append(tool)

        updates = {
            "steps": merged_steps,
            "pitfalls": merged_pitfalls,
            "triggers": merged_triggers,
        }
        if merged_tools:
            updates["required_tools"] = merged_tools
        result = self.update_playbook(target_id, updates)
        result["merged"] = True
        return result

    @staticmethod
    def _version_tuple(value: Any) -> tuple[int, ...]:
        parts = re.findall(r"\d+", str(value or ""))
        return tuple(int(part) for part in parts[:4])

    @classmethod
    def _version_satisfies(cls, actual: Any, minimum: Any) -> bool | None:
        actual_parts = cls._version_tuple(actual)
        min_parts = cls._version_tuple(minimum)
        if not actual_parts or not min_parts:
            return None
        width = max(len(actual_parts), len(min_parts))
        actual_padded = actual_parts + (0,) * (width - len(actual_parts))
        min_padded = min_parts + (0,) * (width - len(min_parts))
        return actual_padded >= min_padded

    def _resolved_tool_entry(
        self,
        requirement: dict,
        *,
        candidate: dict | None = None,
        status: str,
        candidate_count: int | None = None,
    ) -> dict:
        item = {
            "name": requirement.get("name", ""),
            "status": status,
            "optional": bool(requirement.get("optional")),
        }
        if candidate is not None:
            item["tool_id"] = candidate.get("id", "")
            if candidate.get("path"):
                item["path"] = candidate.get("path", "")
            if candidate.get("version"):
                item["version"] = candidate.get("version", "")
            if requirement.get("min_version"):
                satisfied = self._version_satisfies(
                    candidate.get("version", ""),
                    requirement.get("min_version", ""),
                )
                if satisfied is None:
                    item["version_satisfied"] = False
                    item["version_status"] = "unknown"
                else:
                    item["version_satisfied"] = satisfied
        if candidate_count is not None:
            item["candidate_count"] = candidate_count
        return item

    def _resolve_playbook_required_tools(self, required_tools: Any) -> dict:
        """Resolve Playbook tool dependencies against the local tools registry."""
        requirements = self._normalize_playbook_required_tools(required_tools)
        resolved_tools: list[dict] = []
        missing_tools: list[str] = []

        for requirement in requirements:
            name = requirement["name"]
            optional = bool(requirement.get("optional"))
            query = requirement.get("query") or name
            candidates = self.find_tool(query)
            exact = [
                candidate for candidate in candidates
                if str(candidate.get("name", "")).lower() == name.lower()
            ]
            if len(exact) == 1:
                resolved = self._resolved_tool_entry(
                    requirement,
                    candidate=exact[0],
                    status="resolved",
                )
            elif len(exact) > 1:
                resolved = self._resolved_tool_entry(
                    requirement,
                    status="ambiguous",
                    candidate_count=len(exact),
                )
            elif candidates:
                resolved = self._resolved_tool_entry(
                    requirement,
                    status="ambiguous",
                    candidate_count=len(candidates),
                )
            elif requirement.get("purpose"):
                purpose_candidates = self.find_tool(requirement["purpose"])
                if len(purpose_candidates) == 1:
                    resolved = self._resolved_tool_entry(
                        requirement,
                        candidate=purpose_candidates[0],
                        status="resolved_by_purpose",
                    )
                else:
                    resolved = self._resolved_tool_entry(requirement, status="missing")
            else:
                resolved = self._resolved_tool_entry(requirement, status="missing")

            resolved_tools.append(resolved)
            ready_status = resolved.get("status") in {"resolved", "resolved_by_purpose"}
            version_ready = resolved.get("version_satisfied", True) is not False
            if not optional and (not ready_status or not version_ready):
                missing_tools.append(name)

        return {
            "resolved_tools": resolved_tools,
            "tools_ready": not missing_tools,
            "missing_tools": missing_tools,
        }

    def prepare_playbook_execution(
        self,
        playbook_id: str,
        params: dict[str, str] | None = None,
        project_folder: str | None = None,
        confirm_cross_project: bool = False,
    ) -> dict:
        """Prepare a playbook for guided execution with parameter substitution.

        Returns a copy of the playbook with ``${variable}`` placeholders
        replaced by values from *params*, plus per-step status tracking fields.
        Does NOT auto-execute — the AI tool should walk through steps one by one.

        Args:
            playbook_id: ID of the playbook to prepare.
            params: ``{variable_name: value}`` for ``${variable}`` substitution.

        Returns:
            ``{playbook_id, title, execution_plan: [{order, action, status}], parameters_used}``
        """
        pb = self.get_playbook(
            playbook_id,
            _update_access=True,
            project_folder=project_folder,
            confirm_cross_project=confirm_cross_project,
        )
        if pb.get("error"):
            return pb

        params = params or {}

        # Substitute parameters in steps (handle both string and dict formats)
        execution_plan = []
        for i, step in enumerate(pb.get("steps", []), 1):
            if isinstance(step, str):
                action, detail = step, ""
            else:
                action = step.get("action", "")
                detail = step.get("detail", "")
            for var_name, var_value in params.items():
                action = action.replace(f"${{{var_name}}}", var_value)
                detail = detail.replace(f"${{{var_name}}}", var_value)
            execution_plan.append({
                "order": step.get("order", i) if isinstance(step, dict) else i,
                "action": action,
                "detail": detail,
                "status": "pending",
            })

        result = {
            "playbook_id": playbook_id,
            "title": pb.get("title", ""),
            "execution_plan": execution_plan,
            "parameters_used": params,
            "pitfalls": pb.get("pitfalls", []),
            "preconditions": pb.get("preconditions", []),
            "scope": pb.get("scope", {"type": "global"}),
        }
        if confirm_cross_project and project_folder and not self._playbook_visible_for_project(pb, project_folder):
            result["cross_project_confirmed"] = True
            result["requested_project_folder"] = project_folder
        self.save_execution_plan(result)
        result.update(self._resolve_playbook_required_tools(pb.get("required_tools")))
        return result

    # ------------------------------------------------------------------
    # Playbook execution tracking
    # ------------------------------------------------------------------

    def _executions_dir(self) -> Path:
        d = self.root / "playbooks" / "executions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _execution_path(self, playbook_id: str) -> Path:
        return self._executions_dir() / f"{playbook_id}.json"

    @staticmethod
    def _execution_outcome(steps: list[dict]) -> dict:
        """Summarize step states without treating skipped work as success."""
        total = len(steps)
        completed = sum(1 for s in steps if s.get("status") == "completed")
        skipped = sum(1 for s in steps if s.get("status") == "skipped")
        failed = sum(1 for s in steps if s.get("status") == "failed")
        pending = sum(1 for s in steps if s.get("status", "pending") == "pending")
        if failed:
            status = "failed"
        elif total == 0 or (completed == 0 and skipped == 0 and pending == total):
            status = "pending"
        elif completed == total:
            status = "succeeded"
        else:
            status = "partial"
        return {
            "status": status,
            "completed": completed,
            "skipped": skipped,
            "failed": failed,
            "pending": pending,
            "total": total,
        }

    def _encrypt_execution_plan(self, plan: dict) -> dict:
        """Encrypt sensitive fields in an execution plan for at-rest storage."""
        if not self._corpus_key:
            return plan
        result = dict(plan)
        # Encrypt title (derived from playbook)
        if "title" in result and isinstance(result["title"], str):
            result["title"] = self._crypto.corpus_encrypt(result["title"], self._corpus_key)
        # Encrypt step action/detail and list fields
        for list_field in ("execution_plan", "pitfalls", "preconditions"):
            if list_field not in result or not isinstance(result[list_field], list):
                continue
            encrypted_items = []
            for item in result[list_field]:
                if isinstance(item, dict):
                    d = dict(item)
                    for k in ("action", "detail", "notes"):
                        if k in d and isinstance(d[k], str) and d[k]:
                            d[k] = self._crypto.corpus_encrypt(d[k], self._corpus_key)
                    encrypted_items.append(d)
                elif isinstance(item, str):
                    encrypted_items.append(self._crypto.corpus_encrypt(item, self._corpus_key))
                else:
                    encrypted_items.append(item)
            result[list_field] = encrypted_items
        return result

    def _decrypt_execution_plan(self, plan: dict) -> dict:
        """Decrypt sensitive fields in an execution plan for in-memory use."""
        if not self._corpus_key or not isinstance(plan, dict):
            return plan
        result = dict(plan)
        if "title" in result and isinstance(result["title"], str):
            result["title"] = self._crypto.corpus_decrypt(result["title"], self._corpus_key)
        for list_field in ("execution_plan", "pitfalls", "preconditions"):
            if list_field not in result or not isinstance(result[list_field], list):
                continue
            decrypted_items = []
            for item in result[list_field]:
                if isinstance(item, dict):
                    d = dict(item)
                    for k in ("action", "detail", "notes"):
                        if k in d and isinstance(d[k], str) and d[k]:
                            d[k] = self._crypto.corpus_decrypt(d[k], self._corpus_key)
                    decrypted_items.append(d)
                elif isinstance(item, str):
                    decrypted_items.append(self._crypto.corpus_decrypt(item, self._corpus_key))
                else:
                    decrypted_items.append(item)
            result[list_field] = decrypted_items
        return result

    def save_execution_plan(self, plan: dict) -> dict:
        """Persist an execution plan returned by prepare_playbook_execution."""
        pid = plan.get("playbook_id", "")
        if not pid:
            return {"error": "missing playbook_id"}
        plan["started_at"] = _now_iso()
        plan["updated_at"] = _now_iso()
        _write_json(self._execution_path(pid), self._encrypt_execution_plan(plan))
        return {"status": "saved", "playbook_id": pid}

    def update_execution_step(
        self,
        playbook_id: str,
        step_order: int,
        status: str,
        notes: str = "",
    ) -> dict:
        """Update the status of a step in a saved execution plan.

        Args:
            playbook_id: ID of the playbook being executed.
            step_order: The ``order`` number of the step to update.
            status: One of ``"completed"``, ``"skipped"``, ``"failed"``.
            notes: Optional note (e.g. error message for failed steps).

        Returns:
            ``{status, step_order, playbook_id, completed, total}``
        """
        valid = {"completed", "skipped", "failed"}
        if status not in valid:
            return {"error": f"status must be one of {valid}"}

        path = self._execution_path(playbook_id)
        plan = _read_json(path)
        if not plan:
            return {"error": f"no execution plan found for {playbook_id}"}
        # Decrypt the at-rest plan before mutating it, then re-encrypt on
        # write-back. Operating on the raw (encrypted) plan and assigning
        # plaintext ``notes`` directly would leak the note in cleartext, since
        # the surrounding ciphertext fields are never re-encrypted on this path
        # (Codex a5 round-2 P1-4).
        plan = self._decrypt_execution_plan(plan)

        updated = False
        for step in plan.get("execution_plan", []):
            if step.get("order") == step_order:
                step["status"] = status
                if notes:
                    step["notes"] = notes
                step["updated_at"] = _now_iso()
                updated = True
                break

        if not updated:
            return {"error": f"step {step_order} not found in execution plan"}

        plan["updated_at"] = _now_iso()

        steps = plan.get("execution_plan", [])
        outcome = self._execution_outcome(steps)
        completed = outcome["completed"] + outcome["skipped"]
        total = outcome["total"]
        if outcome["status"] == "succeeded":
            plan["completed_at"] = plan.get("completed_at") or _now_iso()
        else:
            plan.pop("completed_at", None)

        _write_json(path, self._encrypt_execution_plan(plan))
        return {
            "status": "updated",
            "step_order": step_order,
            "step_status": status,
            "playbook_id": playbook_id,
            "completed": completed,
            "total": total,
            "outcome": outcome,
        }

    def get_execution_status(self, playbook_id: str) -> dict:
        """Return the current execution state for a playbook."""
        plan = _read_json(self._execution_path(playbook_id))
        if not plan:
            return {"error": f"no execution plan found for {playbook_id}"}
        plan = self._decrypt_execution_plan(plan)
        steps = plan.get("execution_plan", [])
        return {
            "playbook_id": playbook_id,
            "title": plan.get("title", ""),
            "started_at": plan.get("started_at"),
            "completed_at": plan.get("completed_at"),
            "steps": steps,
            "completed": sum(1 for s in steps if s.get("status") in ("completed", "skipped")),
            "total": len(steps),
            "outcome": self._execution_outcome(steps),
        }

    def _export_playbooks(self) -> list[dict]:
        """Export all playbooks as a list for backup."""
        index = self._read_playbook_index()
        result = []
        for entry in index:
            pb = self._read_playbook_by_id(entry.get("id", ""))
            if pb:
                result.append(pb)
        return result

