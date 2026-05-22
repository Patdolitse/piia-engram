"""LLM judge scaffolding and tool-description loading.

The benchmark prefers a real LLM judge when API credentials are available.
This module keeps that path explicit while allowing the benchmark to fall back
to a local rule-based evaluator without changing the experiment code.
"""

from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
ATOMIC_SOURCE = REPO_ROOT / "src" / "piia_engram" / "mcp_server.py"
COORDINATOR_SOURCE = REPO_ROOT / "experiments" / "coordinator" / "high_level_actions.py"
COORDINATOR_TOOLS = {"remember", "recall", "cleanup", "inherit", "sync"}


class LLMJudgeUnavailable(RuntimeError):
    """Raised when a real LLM judge cannot be used in the current environment."""


def load_atomic_tool_descriptions(source_path: str | Path | None = None) -> list[dict[str, str]]:
    """Parse current MCP atomic tool names and docstrings from mcp_server.py."""
    return _load_decorated_tools(Path(source_path) if source_path else ATOMIC_SOURCE)


def load_coordinator_tool_descriptions(source_path: str | Path | None = None) -> list[dict[str, str]]:
    """Parse the five high-level coordinator action descriptions."""
    source = Path(source_path) if source_path else COORDINATOR_SOURCE
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tools: list[dict[str, str]] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "HighLevelActions":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name in COORDINATOR_TOOLS:
                    tools.append(
                        {
                            "name": item.name,
                            "signature": _format_signature(item, drop_self=True),
                            "description": ast.get_docstring(item) or "",
                        }
                    )

    return sorted(tools, key=lambda tool: tool["name"])


def get_llm_availability() -> dict[str, Any]:
    """Return whether an OpenAI or Anthropic SDK path is usable."""
    openai_key = bool(os.environ.get("OPENAI_API_KEY"))
    anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    openai_sdk = importlib.util.find_spec("openai") is not None
    anthropic_sdk = importlib.util.find_spec("anthropic") is not None

    reasons: list[str] = []
    if not openai_key and not anthropic_key:
        reasons.append("OPENAI_API_KEY and ANTHROPIC_API_KEY are not set")
    if openai_key and not openai_sdk:
        reasons.append("OPENAI_API_KEY is set but the openai SDK is not installed")
    if anthropic_key and not anthropic_sdk:
        reasons.append("ANTHROPIC_API_KEY is set but the anthropic SDK is not installed")

    available = (openai_key and openai_sdk) or (anthropic_key and anthropic_sdk)
    return {
        "available": available,
        "openai_key": openai_key,
        "anthropic_key": anthropic_key,
        "openai_sdk": openai_sdk,
        "anthropic_sdk": anthropic_sdk,
        "reasons": reasons,
    }


def build_tool_selection_prompt(tool_descriptions: list[dict[str, str]], user_input: str) -> str:
    """Build the neutral prompt used by a real LLM judge."""
    tool_lines = [
        f"- {tool['name']}{tool.get('signature', '')}: {tool.get('description', '').strip()}"
        for tool in tool_descriptions
    ]
    return (
        "你有以下工具可用：\n"
        + "\n".join(tool_lines)
        + "\n\n"
        + f'用户说："{user_input}"\n\n'
        + '请选择你要调用的工具，并给出参数。只返回 JSON：{"tool": "...", "params": {...}}'
    )


def choose_with_llm(*_: Any, **__: Any) -> dict[str, Any]:
    """Placeholder entrypoint for real LLM execution.

    The current benchmark run only calls this when get_llm_availability() says a
    supported SDK and key are present. It is intentionally separated from the
    rule-based fallback so reports can state which path was used.
    """
    raise LLMJudgeUnavailable("Real LLM judging is not configured in this environment")


def _load_decorated_tools(source: Path) -> list[dict[str, str]]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tools: list[dict[str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        if not any(_is_tool_decorator(decorator) for decorator in node.decorator_list):
            continue
        tools.append(
            {
                "name": node.name,
                "signature": _format_signature(node),
                "description": ast.get_docstring(node) or "",
            }
        )

    return sorted(tools, key=lambda tool: tool["name"])


def _is_tool_decorator(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "tool"
        and isinstance(target.value, ast.Name)
        and target.value.id == "mcp"
    )


def _format_signature(node: ast.AsyncFunctionDef | ast.FunctionDef, drop_self: bool = False) -> str:
    args = [arg.arg for arg in node.args.args]
    if drop_self and args and args[0] == "self":
        args = args[1:]
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    return f"({', '.join(args)})"

