"""MCP tool-schema snapshot + drift-guard tests.

Two layers:

1. **Classifier unit tests** — synthetic before/after snapshots prove that the
   drift classifier flags removing/renaming a tool, removing a parameter,
   making a parameter required, adding a required parameter, changing an
   annotation, and changing the return type as *breaking*; and that purely
   additive metadata (a new tool, a new optional parameter, loosening a
   parameter to optional) is *not* breaking.

2. **Live drift guard** — the committed snapshot
   (``tests/snapshots/mcp_tool_schema.json``) must not have drifted from the
   real ``mcp_server.py`` in a *breaking* way. Additive changes pass (the
   snapshot can be regenerated with ``--write``); a breaking change fails the
   build. The generator is also asserted deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import importlib.util

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "mcp_schema_snapshot.py"
_SNAPSHOT = _ROOT / "tests" / "snapshots" / "mcp_tool_schema.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("mcp_schema_snapshot", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mss = _load_module()


def _param(name, annotation="str", required=False, default="", kind="positional_or_keyword"):
    return {"name": name, "annotation": annotation, "required": required, "default": default, "kind": kind}


def _tool(params=None, returns="str"):
    return {"params": params or [], "returns": returns}


def _snap(tools):
    return {"schema_version": 1, "source": "x", "tool_count": len(tools), "tools": tools}


# ---------------------------------------------------------------------------
# Classifier — breaking changes
# ---------------------------------------------------------------------------


class TestBreakingDrift:
    def test_tool_removed_is_breaking(self):
        old = _snap({"a": _tool(), "b": _tool()})
        new = _snap({"a": _tool()})
        diff = mss.diff_snapshots(old, new)
        assert diff["is_breaking"] is True
        assert any(c["code"] == "tool_removed" and c["tool"] == "b" for c in diff["breaking"])

    def test_tool_renamed_is_breaking(self):
        # A rename reads as remove(old)+add(new): the removal is breaking.
        old = _snap({"get_user_context": _tool()})
        new = _snap({"get_user_ctx": _tool()})
        diff = mss.diff_snapshots(old, new)
        assert diff["is_breaking"] is True
        assert any(c["code"] == "tool_removed" for c in diff["breaking"])
        assert any(c["code"] == "tool_added" for c in diff["additive"])

    def test_param_removed_is_breaking(self):
        old = _snap({"a": _tool([_param("x"), _param("y")])})
        new = _snap({"a": _tool([_param("x")])})
        diff = mss.diff_snapshots(old, new)
        assert diff["is_breaking"] is True
        assert any(c["code"] == "param_removed" for c in diff["breaking"])

    def test_optional_made_required_is_breaking(self):
        old = _snap({"a": _tool([_param("x", required=False, default="")])})
        new = _snap({"a": _tool([_param("x", required=True, default=None)])})
        diff = mss.diff_snapshots(old, new)
        assert diff["is_breaking"] is True
        assert any(c["code"] == "param_made_required" for c in diff["breaking"])

    def test_new_required_param_is_breaking(self):
        old = _snap({"a": _tool([_param("x")])})
        new = _snap({"a": _tool([_param("x"), _param("y", required=True, default=None)])})
        diff = mss.diff_snapshots(old, new)
        assert diff["is_breaking"] is True
        assert any(c["code"] == "required_param_added" for c in diff["breaking"])

    def test_annotation_tightened_is_breaking(self):
        old = _snap({"a": _tool([_param("x", annotation="Optional[str]")])})
        new = _snap({"a": _tool([_param("x", annotation="str")])})
        diff = mss.diff_snapshots(old, new)
        assert diff["is_breaking"] is True
        assert any(c["code"] == "annotation_changed" for c in diff["breaking"])

    def test_return_type_changed_is_breaking(self):
        old = _snap({"a": _tool(returns="str")})
        new = _snap({"a": _tool(returns="dict")})
        diff = mss.diff_snapshots(old, new)
        assert diff["is_breaking"] is True
        assert any(c["code"] == "return_changed" for c in diff["breaking"])


# ---------------------------------------------------------------------------
# Classifier — additive / compatible changes (must NOT be breaking)
# ---------------------------------------------------------------------------


class TestAdditiveDrift:
    def test_new_tool_is_additive(self):
        old = _snap({"a": _tool()})
        new = _snap({"a": _tool(), "b": _tool()})
        diff = mss.diff_snapshots(old, new)
        assert diff["is_breaking"] is False
        assert any(c["code"] == "tool_added" and c["tool"] == "b" for c in diff["additive"])

    def test_new_optional_param_is_additive(self):
        old = _snap({"a": _tool([_param("x")])})
        new = _snap({"a": _tool([_param("x"), _param("y", required=False, default="")])})
        diff = mss.diff_snapshots(old, new)
        assert diff["is_breaking"] is False
        assert any(c["code"] == "optional_param_added" for c in diff["additive"])

    def test_required_made_optional_is_additive(self):
        old = _snap({"a": _tool([_param("x", required=True, default=None)])})
        new = _snap({"a": _tool([_param("x", required=False, default="")])})
        diff = mss.diff_snapshots(old, new)
        assert diff["is_breaking"] is False
        assert any(c["code"] == "param_made_optional" for c in diff["additive"])

    def test_default_value_change_is_compatible(self):
        old = _snap({"a": _tool([_param("x", required=False, default="standard")])})
        new = _snap({"a": _tool([_param("x", required=False, default="quick")])})
        diff = mss.diff_snapshots(old, new)
        assert diff["is_breaking"] is False
        assert any(c["code"] == "default_changed" for c in diff["compatible"])

    def test_param_reorder_is_compatible(self):
        old = _snap({"a": _tool([_param("x"), _param("y")])})
        new = _snap({"a": _tool([_param("y"), _param("x")])})
        diff = mss.diff_snapshots(old, new)
        assert diff["is_breaking"] is False
        assert any(c["code"] == "params_reordered" for c in diff["compatible"])

    def test_identical_snapshots_show_no_drift(self):
        snap = _snap({"a": _tool([_param("x")])})
        diff = mss.diff_snapshots(snap, snap)
        assert diff["is_breaking"] is False
        assert diff["changed"] is False


# ---------------------------------------------------------------------------
# Generator determinism + live drift guard
# ---------------------------------------------------------------------------


class TestSnapshotGenerator:
    def test_snapshot_is_deterministic(self):
        a = mss.build_snapshot(_ROOT)
        b = mss.build_snapshot(_ROOT)
        assert a == b
        # Canonical: tools sorted by name.
        names = list(a["tools"].keys())
        assert names == sorted(names)

    def test_snapshot_count_is_self_consistent(self):
        snap = mss.build_snapshot(_ROOT)
        assert snap["tool_count"] == len(snap["tools"])
        assert snap["tool_count"] > 0

    def test_committed_snapshot_exists_and_parses(self):
        assert _SNAPSHOT.is_file(), "run scripts/mcp_schema_snapshot.py --write to regenerate"
        data = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
        assert data["schema_version"] == mss.SCHEMA_VERSION
        assert data["tools"]

    def test_live_schema_has_no_breaking_drift_vs_snapshot(self):
        """The drift guard: live code must not have *broken* the committed
        contract. Additive drift is allowed (regenerate the snapshot); a
        breaking change must fail this test."""
        committed = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
        live = mss.build_snapshot(_ROOT)
        diff = mss.diff_snapshots(committed, live)
        assert not diff["is_breaking"], (
            "Breaking MCP schema drift detected:\n" + mss.render_diff(diff)
        )
