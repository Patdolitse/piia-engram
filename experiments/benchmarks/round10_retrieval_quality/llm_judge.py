"""LLM judge for Round 10 retrieval quality evaluation.

Uses urllib (no SDK dependency) to call DeepSeek V4 API.
- Evaluation / judge calls: deepseek-v4-pro
- Fast test runner calls: deepseek-v4-flash
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ENV_PATH = Path(__file__).resolve().parent.parent / "round3" / ".env"

# DeepSeek V4 model names
MODEL_JUDGE = "deepseek-v4-pro"    # evaluation / judge
MODEL_FAST = "deepseek-v4-flash"   # fast test runner


def _load_env(path: Path | None = None) -> None:
    env_path = path or ENV_PATH
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class DeepSeekJudge:
    """Lightweight DeepSeek V4 judge for retrieval quality evaluation.

    Uses raw urllib — no openai SDK needed.
    """

    def __init__(
        self,
        raw_log_path: Path | None = None,
        runs_per_scenario: int = 3,
        model: str | None = None,
    ):
        _load_env()
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY missing; check round3/.env")
        self._api_key = api_key
        self._base_url = os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        ).rstrip("/")
        self.model = model or MODEL_JUDGE
        self.runs = runs_per_scenario
        self.raw_log = raw_log_path
        self._call_count = 0

    def _complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {body}") from exc
        content = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "")
        )
        return {"content": content, "usage": data.get("usage", {}), "raw": data}

    def ask(self, system: str, user: str) -> str:
        """Single LLM call, returns content string."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        for attempt in range(1, 4):
            try:
                resp = self._complete(messages)
                content = resp["content"]
                self._call_count += 1
                if self.raw_log:
                    with open(self.raw_log, "a", encoding="utf-8") as f:
                        f.write(json.dumps({
                            "call": self._call_count,
                            "model": self.model,
                            "system": system[:200],
                            "user": user[:200],
                            "response": content[:500],
                            "usage": resp.get("usage", {}),
                        }, ensure_ascii=False) + "\n")
                return content
            except Exception as exc:
                if attempt >= 3:
                    raise
                time.sleep(0.5 * attempt)
        return ""  # unreachable

    def majority_vote(self, system: str, user: str, extractor=None) -> Any:
        """Run N times, return majority-voted result."""
        results = []
        for _ in range(self.runs):
            raw = self.ask(system, user)
            val = extractor(raw) if extractor else raw
            results.append(val)
        # Majority vote
        from collections import Counter
        counts = Counter(str(r) for r in results)
        winner = counts.most_common(1)[0][0]
        for r in results:
            if str(r) == winner:
                return r
        return results[0]


# ── Prompt builders ────────────────────────────────────────────────


def build_relevance_rating_prompt(
    lessons: list[dict],
    project_desc: str,
) -> tuple[str, str]:
    """Build prompt for D3-QUALITY: rate lesson relevance to project."""
    system = (
        "你是知识管理专家。用户正在开发一个项目，系统为他召回了一些历史经验教训。"
        "请评估每条经验与当前项目的相关度。\n\n"
        "评分标准：\n"
        "- 2 = 高度相关（直接适用于当前项目）\n"
        "- 1 = 一般相关（有参考价值但不直接适用）\n"
        "- 0 = 不相关\n\n"
        "只输出 JSON 数组，每个元素 {\"index\": N, \"score\": 0|1|2}。"
    )
    items = "\n".join(
        f"{i+1}. [{l.get('domain','')}] {l.get('summary','')}"
        for i, l in enumerate(lessons)
    )
    user = f"项目描述：{project_desc}\n\n召回的经验教训：\n{items}"
    return system, user


def build_context_qa_prompt(
    context: str,
    question: str,
) -> tuple[str, str]:
    """Build prompt for D4: inject context, ask question about user."""
    system = (
        "以下是关于一位用户的背景信息。请根据这些信息回答问题。"
        "如果信息中没有提到，请说明。回答要简洁。\n\n"
        f"{context}"
    )
    return system, question
