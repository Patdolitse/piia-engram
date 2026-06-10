"""Local environment tool/program registry (ToolRegistryMixin)."""

from __future__ import annotations

import hashlib

from .storage import (
    _ALLOWED_TOOL_UPDATE_FIELDS,
    _now_iso,
    _read_json,
    _write_json,
)


class ToolRegistryMixin:
    """Register / find / list / update local tools."""

    # ------------------------------------------------------------------
    # Tools Registry — local environment tool/program tracking
    # ------------------------------------------------------------------

    def _read_tools(self) -> list[dict]:
        """Read the tools registry."""
        path = self._environment_dir / "tools.json"
        data = _read_json(path)
        if isinstance(data, list):
            return data
        return []

    def _write_tools(self, tools: list[dict]) -> None:
        _write_json(self._environment_dir / "tools.json", tools)

    def _ensure_tool_fields(self, entry: dict) -> dict:
        """Backfill metadata fields on a tool entry."""
        if not isinstance(entry, dict):
            entry = {}
        now = _now_iso()
        if not entry.get("id"):
            name = str(entry.get("name") or "")
            path = str(entry.get("path") or "")
            seed = f"{name}{path}"
            entry["id"] = hashlib.sha256(seed.encode()).hexdigest()[:12]
        entry.setdefault("category", "other")
        entry.setdefault("status", "active")
        entry.setdefault("created_at", now)
        entry.setdefault("updated_at", now)
        entry.setdefault("registered_by", "")
        entry.setdefault("os_platform", "")
        entry.setdefault("version", "")
        entry.setdefault("install_method", "")
        entry.setdefault("notes", "")
        return entry

    def register_tool(
        self,
        tool: dict,
        registered_by: str = "",
    ) -> dict:
        """Register a local tool/program in the environment registry.

        If a tool with the same name already exists, update it instead.
        """
        new_tool = dict(tool)
        if not new_tool.get("name"):
            return {"error": "Tool must have a name"}

        if registered_by:
            new_tool["registered_by"] = registered_by

        new_tool = self._ensure_tool_fields(new_tool)
        tools = self._read_tools()

        # Check for existing tool with same name (case-insensitive) — update it
        new_name = str(new_tool.get("name", "")).lower()
        for i, existing in enumerate(tools):
            if str(existing.get("name", "")).lower() == new_name:
                # Update existing entry
                for key in ("path", "version", "purpose", "install_method",
                            "os_platform", "category", "notes", "status"):
                    if new_tool.get(key):
                        existing[key] = new_tool[key]
                existing["updated_at"] = _now_iso()
                if registered_by:
                    existing["registered_by"] = registered_by
                tools[i] = existing
                self._write_tools(tools)
                self._audit.log("write", "environment/tools", detail=f"updated {new_name}")
                return {**existing, "_action": "updated"}

        # New tool
        tools.append(new_tool)
        self._write_tools(tools)
        self._audit.log("write", "environment/tools", detail=f"registered {new_name}")
        return {**new_tool, "_action": "registered"}

    def find_tool(self, query: str) -> list[dict]:
        """Search tools by name, category, purpose, or path keyword."""
        tools = self._read_tools()
        if not query or not query.strip():
            return [t for t in tools if t.get("status") == "active"]

        terms = query.lower().split()
        results = []
        for tool in tools:
            if tool.get("status") != "active":
                continue
            searchable = " ".join([
                str(tool.get("name", "")),
                str(tool.get("category", "")),
                str(tool.get("purpose", "")),
                str(tool.get("path", "")),
                str(tool.get("notes", "")),
                str(tool.get("install_method", "")),
            ]).lower()
            if all(term in searchable for term in terms):
                results.append(tool)
        self._audit.log("read", "environment/tools", detail=f"found {len(results)} for '{query}'")
        return results

    def list_tools(self, category: str | None = None) -> list[dict]:
        """List all registered tools, optionally filtered by category."""
        tools = self._read_tools()
        result = []
        for tool in tools:
            if tool.get("status") != "active":
                continue
            if category and tool.get("category") != category:
                continue
            result.append(tool)
        self._audit.log("read", "environment/tools", detail=f"listed {len(result)} tools")
        return result

    def update_tool(self, tool_id: str, updates: dict) -> dict:
        """Update fields on a registered tool."""
        tools = self._read_tools()
        for tool in tools:
            if tool.get("id") == tool_id:
                for key, value in updates.items():
                    if key in _ALLOWED_TOOL_UPDATE_FIELDS:
                        tool[key] = value
                tool["updated_at"] = _now_iso()
                self._write_tools(tools)
                return tool
        return {"error": f"Tool not found: {tool_id}"}

    def remove_tool(self, tool_id: str) -> dict:
        """Mark a tool as removed (soft delete)."""
        return self.update_tool(tool_id, {"status": "removed"})

    def _export_tools(self) -> list[dict]:
        """Export all tools for backup."""
        return self._read_tools()

