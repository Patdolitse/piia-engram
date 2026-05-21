"""DeepSeek LLM judge for round 3.

Prompts intentionally include only source-derived tool descriptions and the
user input. Expected labels are never passed into prompt construction.
"""

from __future__ import annotations

import ast
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
ROUND3_DIR = Path(__file__).resolve().parent
ATOMIC_SOURCE = REPO_ROOT / "src" / "engram_core" / "mcp_server.py"
COORDINATOR_SOURCE = REPO_ROOT / "experiments" / "coordinator" / "high_level_actions.py"
COORDINATOR_TOOLS = {"remember", "recall", "cleanup", "inherit", "sync"}


def load_env(path: Path | None = None) -> None:
    env_path = path or (ROUND3_DIR / ".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_atomic_tools_desc(source_path: Path | None = None) -> list[dict[str, str]]:
    return _load_mcp_tools(source_path or ATOMIC_SOURCE)


def load_coordinator_tools_desc(source_path: Path | None = None) -> list[dict[str, str]]:
    source = source_path or COORDINATOR_SOURCE
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
    return tools


def build_atomic_prompt(user_input: str, tools: list[dict[str, str]]) -> str:
    return (
        "你是一个 AI 助手，可以调用以下工具：\n\n"
        f"{_format_tools(tools)}\n\n"
        f"用户说：\"{user_input}\"\n\n"
        "请只返回一个 JSON，格式：\n"
        "{\"tool\": \"工具名\", \"reasoning\": \"为什么选这个\"}\n\n"
        "只能选一个工具。不要解释，不要 markdown，直接返回 JSON。"
    )


def build_coordinator_prompt(user_input: str, tools: list[dict[str, str]]) -> str:
    return (
        "你是一个 AI 助手，可以调用以下工具：\n\n"
        f"{_format_tools(tools)}\n\n"
        f"用户说：\"{user_input}\"\n\n"
        "请只返回 JSON：\n"
        "{\"tool\": \"remember|recall|cleanup|inherit|sync\", "
        "\"kind\": \"lesson|decision|null\", "
        "\"domain\": \"推断的领域，如 python/javascript/database/...\", "
        "\"reasoning\": \"...\"}\n\n"
        "只有当 tool=remember 时才需要传 kind 和 domain。不要解释，直接返回 JSON。"
    )


class DeepSeekClient:
    def __init__(self) -> None:
        load_env()
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is missing; check experiments/benchmarks/round3/.env")
        self.api_key = api_key
        self.base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

    def complete(self, messages: list[dict[str, str]], max_tokens: int = 300) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {body}") from exc

        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        return {
            "content": content,
            "usage": data.get("usage", {}),
            "model": data.get("model", self.model),
            "raw": data,
        }


class LLMJudge:
    def __init__(
        self,
        client: Any | None = None,
        raw_log_path: Path | None = None,
        runs_per_scenario: int = 3,
    ) -> None:
        self.client = client or DeepSeekClient()
        self.raw_log_path = raw_log_path or (ROUND3_DIR / "results_raw.jsonl")
        self.runs_per_scenario = runs_per_scenario
        self.atomic_tools = load_atomic_tools_desc()
        self.coordinator_tools = load_coordinator_tools_desc()
        self.total_calls = 0
        self.failed_calls = 0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def judge_atomic(self, scenario_id: str, user_input: str) -> dict[str, Any]:
        prompt = build_atomic_prompt(user_input, self.atomic_tools)
        return self._majority_vote("atomic", scenario_id, prompt, max_tokens=300)

    def judge_coordinator(self, scenario_id: str, user_input: str) -> dict[str, Any]:
        prompt = build_coordinator_prompt(user_input, self.coordinator_tools)
        return self._majority_vote("coordinator", scenario_id, prompt, max_tokens=350)

    def _majority_vote(self, mode: str, scenario_id: str, prompt: str, max_tokens: int) -> dict[str, Any]:
        parsed_results: list[dict[str, Any]] = []
        for run_index in range(1, self.runs_per_scenario + 1):
            parsed = self._call_once(mode, scenario_id, run_index, prompt, max_tokens)
            parsed_results.append(parsed)
        return _select_majority(mode, parsed_results)

    def _call_once(
        self,
        mode: str,
        scenario_id: str,
        run_index: int,
        prompt: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        messages = [{"role": "user", "content": prompt}]
        error = None
        content = ""
        usage: dict[str, Any] = {}
        response_payload: dict[str, Any] = {}
        parsed: dict[str, Any] = {"tool": "UNKNOWN", "kind": None, "domain": None, "reasoning": ""}
        for attempt in range(1, 3):
            try:
                response = self.client.complete(messages, max_tokens=max_tokens)
                response_payload = response
                content = response.get("content", "")
                usage = response.get("usage", {}) or {}
                parsed = _parse_json_response(content)
                error = None
                break
            except Exception as exc:  # noqa: BLE001 - raw log must capture any judge failure
                error = str(exc)
                parsed = {"tool": "UNKNOWN", "kind": None, "domain": None, "reasoning": error}
                time.sleep(0.2 * attempt)

        self.total_calls += 1
        if parsed.get("tool") == "UNKNOWN":
            self.failed_calls += 1
        self._add_usage(usage)
        self._append_raw(
            {
                "scenario_id": scenario_id,
                "mode": mode,
                "run_index": run_index,
                "messages": messages,
                "response": response_payload,
                "raw_content": content,
                "parsed": parsed,
                "usage": usage,
                "error": error,
            }
        )
        return parsed

    def _add_usage(self, usage: dict[str, Any]) -> None:
        for key in self.usage:
            value = usage.get(key)
            if isinstance(value, int):
                self.usage[key] += value

    def _append_raw(self, item: dict[str, Any]) -> None:
        self.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.raw_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")


def _load_mcp_tools(source: Path) -> list[dict[str, str]]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tools: list[dict[str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        if any(_is_mcp_tool_decorator(decorator) for decorator in node.decorator_list):
            tools.append(
                {
                    "name": node.name,
                    "signature": _format_signature(node),
                    "description": ast.get_docstring(node) or "",
                }
            )
    return tools


def _is_mcp_tool_decorator(decorator: ast.expr) -> bool:
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
    return f"({', '.join(args)})"


def _format_tools(tools: list[dict[str, str]]) -> str:
    parts = []
    for tool in tools:
        description = tool.get("description", "")
        parts.append(f"{tool['name']}{tool.get('signature', '')}\n{description}")
    return "\n\n".join(parts)


def _parse_json_response(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM response JSON is not an object")
    data.setdefault("tool", "UNKNOWN")
    data.setdefault("kind", None)
    data.setdefault("domain", None)
    data.setdefault("reasoning", "")
    if data["kind"] == "null":
        data["kind"] = None
    if data["domain"] == "null":
        data["domain"] = None
    return data


def _select_majority(mode: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    if mode == "atomic":
        keys = [str(item.get("tool", "UNKNOWN")) for item in results]
    else:
        keys = [
            json.dumps(
                {
                    "tool": item.get("tool", "UNKNOWN"),
                    "kind": item.get("kind"),
                    "domain": item.get("domain"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            for item in results
        ]
    winner_key, _ = Counter(keys).most_common(1)[0]
    for item, key in zip(results, keys, strict=True):
        if key == winner_key:
            return item
    return results[0] if results else {"tool": "UNKNOWN", "kind": None, "domain": None, "reasoning": ""}
