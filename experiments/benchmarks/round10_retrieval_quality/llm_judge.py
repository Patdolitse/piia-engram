"""LLM judge for Round 10 retrieval quality evaluation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# DeepSeek API (OpenAI-compatible)
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[misc,assignment]

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc,assignment]


ENV_PATH = Path(__file__).resolve().parent.parent / "round3" / ".env"


class DeepSeekJudge:
    """Lightweight DeepSeek V3 judge for retrieval quality evaluation."""

    def __init__(self, raw_log_path: Path | None = None, runs_per_scenario: int = 3):
        if load_dotenv:
            load_dotenv(ENV_PATH)
        if OpenAI is None:
            raise RuntimeError("openai package not installed. Run: pip install openai")

        self.client = OpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.runs = runs_per_scenario
        self.raw_log = raw_log_path
        self._call_count = 0

    def ask(self, system: str, user: str) -> str:
        """Single LLM call, returns content string."""
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=1024,
        )
        content = resp.choices[0].message.content or ""
        self._call_count += 1
        if self.raw_log:
            with open(self.raw_log, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "call": self._call_count,
                    "system": system[:200],
                    "user": user[:200],
                    "response": content[:500],
                }, ensure_ascii=False) + "\n")
        return content

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
        # Return the original value matching the winner string
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
