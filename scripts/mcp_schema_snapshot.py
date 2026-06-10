"""Canonical MCP tool-schema snapshot + drift guard.

The public contract of an MCP server is the set of tools it exposes and the
shape of each tool's call signature (parameter names, whether they are
required, their type annotations, and the return type). A *silent* change to
that contract — a renamed tool, a removed parameter, a previously-optional
parameter made required, a tightened type — breaks already-connected clients
without any local test noticing.

This module derives a canonical, deterministic snapshot of that contract
straight from ``src/piia_engram/mcp_server.py`` plus its ``mcp_tools_*.py``
sibling modules using the standard-library
``ast`` module (no package import, no side effects — the same approach as
``scripts/count_mcp_tools.py``), and provides a drift checker that classifies
the difference between two snapshots as **breaking** vs **additive** vs
**compatible**:

- breaking   — a client that worked before could now fail:
               tool removed/renamed, parameter removed, optional→required,
               a new *required* parameter, annotation changed, return changed.
- additive   — strictly more capability, old clients unaffected:
               new tool, new *optional* parameter, required→optional.
- compatible — neither a break nor new capability (default value changed,
               parameters reordered — MCP calls are by-name, so order is
               irrelevant to callers).

Nothing here publishes a schema anywhere; the snapshot is a local JSON file
that lives in the repo so CI / a test can diff against it.

Usage (from the repo root)::

    python scripts/mcp_schema_snapshot.py                       # human summary
    python scripts/mcp_schema_snapshot.py --json                # snapshot as JSON
    python scripts/mcp_schema_snapshot.py --write PATH          # write snapshot
    python scripts/mcp_schema_snapshot.py --check PATH          # drift guard
    python scripts/mcp_schema_snapshot.py --check PATH --json   # machine diff

Exit codes:
- 0  snapshot derived / no breaking drift
- 1  breaking drift detected (only with --check)
- 2  source file missing / unparseable
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

MCP_SERVER_REL = "src/piia_engram/mcp_server.py"
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Extraction (AST only — never imports the package)
# ---------------------------------------------------------------------------


def _is_mcp_tool_decorator(node: ast.AST) -> bool:
    """Match ``@mcp.tool()`` — a Call whose func is an attribute named ``tool``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "tool"
    )


def _annotation_str(node: ast.expr | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive; unparse is robust
        return ""


def _default_repr(node: ast.expr | None) -> Any:
    """A stable, JSON-safe representation of a default value.

    Literals are rendered as themselves; anything more exotic falls back to its
    unparsed source so the snapshot stays deterministic without evaluating code.
    """
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except Exception:
        return _annotation_str(node)


def _extract_params(func: ast.AsyncFunctionDef) -> list[dict[str, Any]]:
    args = func.args
    positional = list(args.posonlyargs) + list(args.args)
    # Align defaults to the tail of the positional args.
    defaults = list(args.defaults)
    pad = [None] * (len(positional) - len(defaults))
    aligned_defaults = pad + defaults

    params: list[dict[str, Any]] = []
    for arg, default in zip(positional, aligned_defaults):
        if arg.arg == "self":
            continue
        params.append(
            {
                "name": arg.arg,
                "annotation": _annotation_str(arg.annotation),
                "required": default is None,
                "default": _default_repr(default),
                "kind": "positional_or_keyword",
            }
        )

    # Keyword-only args (after *) — also part of the call contract.
    kw_defaults = list(args.kw_defaults)
    for arg, default in zip(args.kwonlyargs, kw_defaults):
        params.append(
            {
                "name": arg.arg,
                "annotation": _annotation_str(arg.annotation),
                "required": default is None,
                "default": _default_repr(default),
                "kind": "keyword_only",
            }
        )
    return params


def extract_tools(tree: ast.AST) -> dict[str, dict[str, Any]]:
    """Map tool name → ``{params, returns}`` for every ``@mcp.tool()`` wrapper."""
    tools: dict[str, dict[str, Any]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if not any(_is_mcp_tool_decorator(d) for d in node.decorator_list):
            continue
        tools[node.name] = {
            "params": _extract_params(node),
            "returns": _annotation_str(node.returns),
        }
    return tools


def build_snapshot(root: Path) -> dict[str, Any]:
    path = root / MCP_SERVER_REL
    if not path.is_file():
        raise SystemExit(f"[error] not found: {path}")
    tools: dict[str, Any] = {}
    for src in [path, *sorted(path.parent.glob("mcp_tools_*.py"))]:
        try:
            tree = ast.parse(src.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - defensive
            raise SystemExit(f"[error] could not parse {src}: {exc}") from exc
        tools.update(extract_tools(tree))
    # Canonical ordering: tools sorted by name (params keep source order, which
    # is the documented call order).
    ordered = {name: tools[name] for name in sorted(tools)}
    return {
        "schema_version": SCHEMA_VERSION,
        "source": MCP_SERVER_REL,
        "tool_count": len(ordered),
        "tools": ordered,
    }


# ---------------------------------------------------------------------------
# Drift classification
# ---------------------------------------------------------------------------


def _params_by_name(tool: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in tool.get("params", [])}


def diff_tool(name: str, old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, str]]:
    """Classify the per-parameter / return changes for one shared tool."""
    changes: list[dict[str, str]] = []
    old_params = _params_by_name(old)
    new_params = _params_by_name(new)

    for pname in old_params:
        if pname not in new_params:
            changes.append(
                {
                    "kind": "breaking",
                    "tool": name,
                    "code": "param_removed",
                    "detail": f"parameter '{pname}' removed",
                }
            )
    for pname, np in new_params.items():
        op = old_params.get(pname)
        if op is None:
            changes.append(
                {
                    "kind": "breaking" if np["required"] else "additive",
                    "tool": name,
                    "code": "required_param_added" if np["required"] else "optional_param_added",
                    "detail": f"parameter '{pname}' added ({'required' if np['required'] else 'optional'})",
                }
            )
            continue
        if op["required"] != np["required"]:
            if np["required"]:
                changes.append(
                    {
                        "kind": "breaking",
                        "tool": name,
                        "code": "param_made_required",
                        "detail": f"parameter '{pname}' optional → required",
                    }
                )
            else:
                changes.append(
                    {
                        "kind": "additive",
                        "tool": name,
                        "code": "param_made_optional",
                        "detail": f"parameter '{pname}' required → optional",
                    }
                )
        if op.get("annotation", "") != np.get("annotation", ""):
            changes.append(
                {
                    "kind": "breaking",
                    "tool": name,
                    "code": "annotation_changed",
                    "detail": (
                        f"parameter '{pname}' annotation "
                        f"'{op.get('annotation', '')}' → '{np.get('annotation', '')}'"
                    ),
                }
            )
        if op.get("default") != np.get("default") and op["required"] == np["required"]:
            changes.append(
                {
                    "kind": "compatible",
                    "tool": name,
                    "code": "default_changed",
                    "detail": f"parameter '{pname}' default changed",
                }
            )

    old_order = [p["name"] for p in old.get("params", [])]
    new_order = [p["name"] for p in new.get("params", [])]
    shared_old = [p for p in old_order if p in set(new_order)]
    shared_new = [p for p in new_order if p in set(old_order)]
    if shared_old != shared_new:
        changes.append(
            {
                "kind": "compatible",
                "tool": name,
                "code": "params_reordered",
                "detail": "parameter order changed (MCP calls are by-name)",
            }
        )

    if old.get("returns", "") != new.get("returns", ""):
        changes.append(
            {
                "kind": "breaking",
                "tool": name,
                "code": "return_changed",
                "detail": f"return type '{old.get('returns', '')}' → '{new.get('returns', '')}'",
            }
        )
    return changes


def diff_snapshots(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Compare two snapshots and bucket every change as breaking/additive/compatible."""
    old_tools = old.get("tools", {})
    new_tools = new.get("tools", {})
    changes: list[dict[str, str]] = []

    for name in old_tools:
        if name not in new_tools:
            changes.append(
                {
                    "kind": "breaking",
                    "tool": name,
                    "code": "tool_removed",
                    "detail": f"tool '{name}' removed or renamed",
                }
            )
    for name in new_tools:
        if name not in old_tools:
            changes.append(
                {
                    "kind": "additive",
                    "tool": name,
                    "code": "tool_added",
                    "detail": f"tool '{name}' added",
                }
            )
    for name in sorted(set(old_tools) & set(new_tools)):
        changes.extend(diff_tool(name, old_tools[name], new_tools[name]))

    breaking = [c for c in changes if c["kind"] == "breaking"]
    additive = [c for c in changes if c["kind"] == "additive"]
    compatible = [c for c in changes if c["kind"] == "compatible"]
    return {
        "breaking": breaking,
        "additive": additive,
        "compatible": compatible,
        "is_breaking": bool(breaking),
        "changed": bool(changes),
    }


def render_diff(diff: dict[str, Any]) -> str:
    lines: list[str] = []
    verdict = "BREAKING DRIFT" if diff["is_breaking"] else (
        "additive/compatible drift" if diff["changed"] else "no drift"
    )
    lines.append(f"MCP schema drift: {verdict}")
    for bucket in ("breaking", "additive", "compatible"):
        for c in diff[bucket]:
            lines.append(f"  [{bucket}] {c['tool']}: {c['code']} — {c['detail']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--root", default=".", help="Repo root (default: cwd)")
    ap.add_argument("--write", metavar="PATH", help="Write the canonical snapshot to PATH")
    ap.add_argument("--check", metavar="PATH", help="Compare live schema against snapshot PATH")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    live = build_snapshot(root)

    if args.write:
        out = Path(args.write)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(live, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote snapshot: {out} ({live['tool_count']} tools)")
        return 0

    if args.check:
        snap_path = Path(args.check)
        if not snap_path.is_file():
            print(f"[error] snapshot not found: {snap_path}", file=sys.stderr)
            return 2
        old = json.loads(snap_path.read_text(encoding="utf-8"))
        diff = diff_snapshots(old, live)
        if args.json:
            print(json.dumps(diff, ensure_ascii=False, indent=2))
        else:
            print(render_diff(diff))
        return 1 if diff["is_breaking"] else 0

    if args.json:
        print(json.dumps(live, ensure_ascii=False, indent=2))
    else:
        print(f"mcp_tool_schema: {live['tool_count']} tools")
        for name, tool in live["tools"].items():
            req = [p["name"] for p in tool["params"] if p["required"]]
            opt = [p["name"] for p in tool["params"] if not p["required"]]
            print(f"  {name}(required={req}, optional={opt}) -> {tool['returns']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
