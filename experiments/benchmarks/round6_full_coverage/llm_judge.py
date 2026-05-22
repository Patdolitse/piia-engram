"""DeepSeek judge for Round 6 full tool-surface coverage."""

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
ROUND6_DIR = Path(__file__).resolve().parent
ROUND3_ENV = ROUND6_DIR.parent / "round3" / ".env"
MCP_SERVER = REPO_ROOT / "src" / "piia_engram" / "mcp_server.py"


def load_env(path: Path | None = None) -> None:
    env_path = path or ROUND3_ENV
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_live_tools_desc(source_path: Path | None = None) -> list[dict[str, str]]:
    source = source_path or MCP_SERVER
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tools: list[dict[str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        if not any(_is_mcp_tool_decorator(decorator) for decorator in node.decorator_list):
            continue
        tools.append(
            {
                "name": node.name,
                "signature": _format_signature(node),
                "description": ast.get_docstring(node) or "",
            }
        )
    return tools


def build_tool_choice_prompt(user_input: str, tools: list[dict[str, str]]) -> str:
    return (
        "You are an AI agent choosing whether to call one Engram MCP tool.\n"
        "Pick exactly one best tool for the user's request, or return none if no Engram tool is needed.\n"
        "If the request clearly intends a tool but required parameters are missing, still choose the intended tool "
        "and mention the missing parameters in reasoning. Do not invent parameter values.\n"
        "Use the tool names, signatures, and descriptions carefully. Return JSON only.\n\n"
        f"TOOLS:\n{_format_tools(tools)}\n\n"
        "SPECIAL OPTION:\nnone\nUse none only when the user is chatting, asking a general explanation, pausing, "
        "thanking, or otherwise not asking Engram to read/write/maintain knowledge.\n\n"
        f"USER REQUEST:\n{user_input}\n\n"
        'Return exactly: {"tool": "tool_name_or_none", "reasoning": "short reason"}'
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

    def complete(self, messages: list[dict[str, str]], max_tokens: int = 320) -> dict[str, Any]:
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
            with urllib.request.urlopen(req, timeout=75) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {body}") from exc

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
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
        tools: list[dict[str, str]] | None = None,
    ) -> None:
        self.client = client or DeepSeekClient()
        self.raw_log_path = raw_log_path or (ROUND6_DIR / "results_raw.jsonl")
        self.runs_per_scenario = runs_per_scenario
        self.tools = tools or load_live_tools_desc()
        self.total_calls = 0
        self.failed_calls = 0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def judge_tool(self, scenario_id: str, user_input: str) -> dict[str, Any]:
        prompt = build_tool_choice_prompt(user_input, self.tools)
        parsed_results = [
            self._call_once(scenario_id, run_index, prompt)
            for run_index in range(1, self.runs_per_scenario + 1)
        ]
        majority = _select_majority(parsed_results)
        majority["votes"] = [item.get("tool", "UNKNOWN") for item in parsed_results]
        return majority

    def _call_once(self, scenario_id: str, run_index: int, prompt: str) -> dict[str, Any]:
        messages = [{"role": "user", "content": prompt}]
        content = ""
        response_payload: dict[str, Any] = {}
        usage: dict[str, Any] = {}
        error = None
        parsed = {"tool": "UNKNOWN", "reasoning": ""}
        for attempt in range(1, 3):
            try:
                response = self.client.complete(messages)
                response_payload = response
                content = response.get("content", "")
                usage = response.get("usage", {}) or {}
                parsed = _parse_json_response(content)
                error = None
                break
            except Exception as exc:  # noqa: BLE001 - raw log captures judge failures
                error = str(exc)
                parsed = {"tool": "UNKNOWN", "reasoning": error}
                time.sleep(0.25 * attempt)

        self.total_calls += 1
        if parsed.get("tool") == "UNKNOWN":
            self.failed_calls += 1
        self._add_usage(usage)
        self._append_raw(
            {
                "scenario_id": scenario_id,
                "mode": "tool_choice_or_none",
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


def _is_mcp_tool_decorator(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "tool"
        and isinstance(target.value, ast.Name)
        and target.value.id == "mcp"
    )


def _format_signature(node: ast.AsyncFunctionDef | ast.FunctionDef) -> str:
    return f"({', '.join(arg.arg for arg in node.args.args)})"


def _format_tools(tools: list[dict[str, str]]) -> str:
    return "\n\n".join(
        f"{tool['name']}{tool.get('signature', '')}\n{tool.get('description', '')}"
        for tool in tools
    )


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
    data.setdefault("reasoning", "")
    return data


def _select_majority(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"tool": "UNKNOWN", "reasoning": ""}
    winner, _ = Counter(str(item.get("tool", "UNKNOWN")) for item in results).most_common(1)[0]
    for item in results:
        if item.get("tool") == winner:
            return dict(item)
    return dict(results[0])
