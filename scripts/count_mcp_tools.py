"""Deterministically re-derive the MCP tool counts from source.

The public-fact manifest (``docs/public-facts.json``) claims a tool split of
``mcp_tools_total`` / ``mcp_tools_core`` / ``mcp_tools_advanced``. Those numbers
must be reproducible from a single command so the manifest never drifts from the
code. This script is that command: it parses ``src/piia_engram/mcp_server.py``
plus its ``mcp_tools_*.py`` sibling modules with the standard library ``ast``
module (no package import, no side effects):

- total   = number of ``@mcp.tool()``-decorated ``async def`` wrappers
            (equals what ``ENGRAM_TOOLS=all`` registers)
- core    = number of names in the ``TIER1_TOOLS`` frozenset literal
            (loaded by default when ``ENGRAM_TOOLS`` is unset / ``core``)
- advanced = total - core

Run from the repo root:

    python scripts/count_mcp_tools.py            # human line
    python scripts/count_mcp_tools.py --json      # {"total":..,"core":..,"advanced":..}

Exit codes:
- 0  counts derived
- 2  source file missing / TIER1_TOOLS literal not found
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

MCP_SERVER_REL = "src/piia_engram/mcp_server.py"
# Tool implementations are split across mcp_server.py + mcp_tools_*.py siblings.
MCP_TOOLS_GLOB = "mcp_tools_*.py"
TIER1_NAME = "TIER1_TOOLS"


def _tool_source_files(root: Path) -> list[Path]:
    base = root / MCP_SERVER_REL
    return [base, *sorted(base.parent.glob(MCP_TOOLS_GLOB))]


def _is_mcp_tool_decorator(node: ast.AST) -> bool:
    # Matches @mcp.tool(): a Call whose func is an attribute named "tool".
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "tool"
    )


def count_total(tree: ast.AST) -> int:
    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if any(_is_mcp_tool_decorator(d) for d in node.decorator_list):
            total += 1
    return total


def count_core(tree: ast.AST) -> int | None:
    """Count distinct string elements in the TIER1_TOOLS frozenset literal."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if TIER1_NAME not in targets:
            continue
        # Expect: TIER1_TOOLS = frozenset({"a", "b", ...})
        value = node.value
        members: set[str] = set()
        container = None
        if isinstance(value, ast.Call) and value.args:
            container = value.args[0]
        elif isinstance(value, (ast.Set, ast.List, ast.Tuple)):
            container = value
        if isinstance(container, (ast.Set, ast.List, ast.Tuple)):
            for elt in container.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    members.add(elt.value)
        if members:
            return len(members)
    return None


def derive(root: Path) -> dict[str, int]:
    path = root / MCP_SERVER_REL
    if not path.is_file():
        raise SystemExit(f"[error] not found: {path}")
    total = 0
    core = None
    for src in _tool_source_files(root):
        tree = ast.parse(src.read_text(encoding="utf-8"))
        total += count_total(tree)
        if core is None:
            core = count_core(tree)
    if core is None:
        raise SystemExit(f"[error] {TIER1_NAME} frozenset literal not found in {path}")
    return {"total": total, "core": core, "advanced": total - core}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else ""
    )
    ap.add_argument("--root", default=".", help="Repo root (default: cwd)")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args()

    counts = derive(Path(args.root).resolve())
    if args.json:
        print(json.dumps(counts))
    else:
        print(f"mcp_tools_total={counts['total']} "
              f"core={counts['core']} advanced={counts['advanced']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
