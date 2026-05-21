"""DeepSeek LLM judge for round 4 regression benchmark."""

from __future__ import annotations

import json
import os
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


ROUND4_DIR = Path(__file__).resolve().parent
ROUND3_ENV = ROUND4_DIR.parent / "round3" / ".env"


def load_env(path: Path | None = None) -> None:
    """Load the existing round 3 DeepSeek .env without adding dependencies."""
    env_path = path or ROUND3_ENV
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class DeepSeekClient:
    """Small stdlib client for DeepSeek chat completions."""

    def __init__(self) -> None:
        load_env()
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is missing; check experiments/benchmarks/round3/.env")
        self.api_key = api_key
        self.base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

    def complete(self, messages: list[dict[str, str]], max_tokens: int = 500) -> dict[str, Any]:
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

        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        return {
            "content": content,
            "usage": data.get("usage", {}),
            "model": data.get("model", self.model),
            "raw": data,
        }


class LLMJudge:
    """Run three DeepSeek judgments per scenario and store raw evidence."""

    FIELDS_BY_MODE = {
        "onboarding": [
            "role_readable",
            "tech_stack_readable",
            "lesson_present_in_context",
            "lesson_searchable",
            "language_ok",
        ],
        "context": [
            "identity_complete",
            "key_lesson_mentioned",
            "key_decision_mentioned",
            "key_knowledge_mentioned",
        ],
        "extraction": [
            "should_extract",
            "extracted_relevant",
            "false_positive",
        ],
    }

    def __init__(
        self,
        client: Any | None = None,
        raw_log_path: Path | None = None,
        runs_per_scenario: int = 3,
    ) -> None:
        self.client = client or DeepSeekClient()
        self.raw_log_path = raw_log_path or (ROUND4_DIR / "results_raw.jsonl")
        self.runs_per_scenario = runs_per_scenario
        self.total_calls = 0
        self.failed_calls = 0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    @staticmethod
    def build_onboarding_prompt_for_test(
        scenario_id: str,
        profile_reference: dict[str, Any],
        context: str,
        search_results: dict[str, Any],
    ) -> str:
        return _build_onboarding_prompt(scenario_id, profile_reference, context, search_results)

    @staticmethod
    def build_context_prompt_for_test(
        scenario_id: str,
        context: str,
        profile_reference: dict[str, Any],
        key_knowledge_reference: dict[str, Any],
    ) -> str:
        return _build_context_prompt(scenario_id, context, profile_reference, key_knowledge_reference)

    @staticmethod
    def build_similarity_prompt_for_test(scenario_id: str, context_a: str, context_b: str) -> str:
        return _build_similarity_prompt(scenario_id, context_a, context_b)

    @staticmethod
    def build_extraction_prompt_for_test(
        scenario_id: str,
        dialogue: str,
        extraction: dict[str, Any],
    ) -> str:
        return _build_extraction_prompt(scenario_id, dialogue, extraction)

    def judge_onboarding(
        self,
        scenario_id: str,
        profile_reference: dict[str, Any],
        context: str,
        search_results: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = _build_onboarding_prompt(scenario_id, profile_reference, context, search_results)
        return self._majority_vote("onboarding", scenario_id, prompt, max_tokens=500)

    def judge_context_snapshot(
        self,
        scenario_id: str,
        context: str,
        profile_reference: dict[str, Any],
        key_knowledge_reference: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = _build_context_prompt(scenario_id, context, profile_reference, key_knowledge_reference)
        return self._majority_vote("context", scenario_id, prompt, max_tokens=500)

    def judge_similarity(self, scenario_id: str, context_a: str, context_b: str) -> dict[str, Any]:
        prompt = _build_similarity_prompt(scenario_id, context_a, context_b)
        return self._majority_vote("similarity", scenario_id, prompt, max_tokens=350)

    def judge_extraction(
        self,
        scenario_id: str,
        dialogue: str,
        extraction: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = _build_extraction_prompt(scenario_id, dialogue, extraction)
        return self._majority_vote("extraction", scenario_id, prompt, max_tokens=600)

    def _majority_vote(self, mode: str, scenario_id: str, prompt: str, max_tokens: int) -> dict[str, Any]:
        parsed_results = [
            self._call_once(mode, scenario_id, run_index, prompt, max_tokens)
            for run_index in range(1, self.runs_per_scenario + 1)
        ]
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
        parsed: dict[str, Any] = _default_for_mode(mode)
        for attempt in range(1, 3):
            try:
                response = self.client.complete(messages, max_tokens=max_tokens)
                response_payload = response
                content = response.get("content", "")
                usage = response.get("usage", {}) or {}
                parsed = _normalise_for_mode(mode, _parse_json_response(content))
                error = None
                break
            except Exception as exc:  # noqa: BLE001 - raw regression logs must capture judge failures
                error = str(exc)
                parsed = _default_for_mode(mode, reasoning=error)
                time.sleep(0.25 * attempt)

        self.total_calls += 1
        if error is not None:
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


def _build_onboarding_prompt(
    scenario_id: str,
    profile_reference: dict[str, Any],
    context: str,
    search_results: dict[str, Any],
) -> str:
    return (
        "You are an independent regression judge for an AI memory product.\n"
        "Assess whether the generated cold-start context and search output preserve the seeded onboarding data.\n"
        "Use semantic judgment, not exact string matching. Return JSON only.\n\n"
        f"Scenario id: {scenario_id}\n"
        f"Seeded profile reference:\n{json.dumps(profile_reference, ensure_ascii=False, indent=2)}\n\n"
        f"Generated cold-start context:\n{context}\n\n"
        f"Search output:\n{json.dumps(search_results, ensure_ascii=False, indent=2)}\n\n"
        "Return this JSON object exactly with booleans and a short reason:\n"
        '{"role_readable": true, "tech_stack_readable": true, '
        '"lesson_present_in_context": true, "lesson_searchable": true, '
        '"language_ok": true, "reasoning": "..."}'
    )


def _build_context_prompt(
    scenario_id: str,
    context: str,
    profile_reference: dict[str, Any],
    key_knowledge_reference: dict[str, Any],
) -> str:
    return (
        "You are judging one get_user_context() output from a stable local Engram state.\n"
        "Decide whether the output contains complete identity data and recent key knowledge.\n"
        "Key knowledge means the supplied important lesson and the supplied important decision.\n"
        "Use semantic judgment, but every positive key-knowledge judgment must include an exact short quote copied from Context output only.\n"
        "Do not use the Profile reference or Key knowledge reference as evidence. If Context output lacks evidence, set the boolean false and the quote null.\n"
        "Return JSON only.\n\n"
        f"Scenario id: {scenario_id}\n"
        f"Profile reference:\n{json.dumps(profile_reference, ensure_ascii=False, indent=2)}\n\n"
        f"Key knowledge reference:\n{json.dumps(key_knowledge_reference, ensure_ascii=False, indent=2)}\n\n"
        f"Context output:\n{context}\n\n"
        "Return this JSON object exactly:\n"
        '{"identity_complete": true, "key_lesson_mentioned": true, '
        '"key_decision_mentioned": true, "key_knowledge_mentioned": true, '
        '"key_lesson_quote": "...", "key_decision_quote": "...", '
        '"reasoning": "..."}'
    )


def _build_similarity_prompt(scenario_id: str, context_a: str, context_b: str) -> str:
    return (
        "You are judging semantic consistency between two get_user_context() outputs.\n"
        "Return a similarity score from 0.0 to 1.0, where 1.0 means semantically identical.\n"
        "Return JSON only.\n\n"
        f"Pair id: {scenario_id}\n"
        f"Context A:\n{context_a}\n\n"
        f"Context B:\n{context_b}\n\n"
        'Return this JSON object exactly: {"similarity": 0.95, "reasoning": "..."}'
    )


def _build_extraction_prompt(scenario_id: str, dialogue: str, extraction: dict[str, Any]) -> str:
    return (
        "You are judging an automatic memory-extraction result.\n"
        "A durable memory should capture a lesson learned, a technical rule, or a concrete decision.\n"
        "Ordinary coordination, temporary scheduling, and casual status updates should not be persisted.\n"
        "Judge only from the dialogue and the extraction output below. Return JSON only.\n\n"
        f"Scenario id: {scenario_id}\n"
        f"Dialogue:\n{dialogue}\n\n"
        f"Extraction output:\n{json.dumps(extraction, ensure_ascii=False, indent=2)}\n\n"
        "Return this JSON object exactly:\n"
        '{"should_extract": true, "extracted_relevant": true, "false_positive": false, '
        '"semantic_accuracy": 0.95, "reasoning": "..."}'
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
    return data


def _default_for_mode(mode: str, reasoning: str = "UNKNOWN") -> dict[str, Any]:
    if mode == "onboarding":
        return {
            "role_readable": False,
            "tech_stack_readable": False,
            "lesson_present_in_context": False,
            "lesson_searchable": False,
            "language_ok": False,
            "reasoning": reasoning,
        }
    if mode == "context":
        return {
            "identity_complete": False,
            "key_lesson_mentioned": False,
            "key_decision_mentioned": False,
            "key_knowledge_mentioned": False,
            "key_lesson_quote": None,
            "key_decision_quote": None,
            "reasoning": reasoning,
        }
    if mode == "similarity":
        return {"similarity": 0.0, "reasoning": reasoning}
    if mode == "extraction":
        return {
            "should_extract": False,
            "extracted_relevant": False,
            "false_positive": True,
            "semantic_accuracy": 0.0,
            "reasoning": reasoning,
        }
    return {"reasoning": reasoning}


def _normalise_for_mode(mode: str, data: dict[str, Any]) -> dict[str, Any]:
    merged = _default_for_mode(mode, reasoning=str(data.get("reasoning", "")))
    merged.update(data)
    for key in LLMJudge.FIELDS_BY_MODE.get(mode, []):
        merged[key] = _as_bool(merged.get(key))
    if mode == "context":
        merged["key_knowledge_mentioned"] = _as_bool(merged.get("key_knowledge_mentioned")) and _as_bool(
            merged.get("key_lesson_mentioned")
        ) and _as_bool(merged.get("key_decision_mentioned"))
    if mode == "similarity":
        merged["similarity"] = _as_float(merged.get("similarity"))
    if mode == "extraction":
        merged["semantic_accuracy"] = _as_float(merged.get("semantic_accuracy"))
    merged["reasoning"] = str(merged.get("reasoning", ""))
    return merged


def _select_majority(mode: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return _default_for_mode(mode)
    if mode == "similarity":
        values = sorted(_as_float(item.get("similarity")) for item in results)
        median = values[len(values) // 2]
        winner = min(results, key=lambda item: abs(_as_float(item.get("similarity")) - median))
        result = dict(winner)
        result["similarity"] = median
        return result

    fields = LLMJudge.FIELDS_BY_MODE.get(mode, [])
    keys = [
        json.dumps({field: item.get(field) for field in fields}, ensure_ascii=False, sort_keys=True)
        for item in results
    ]
    winner_key, _ = Counter(keys).most_common(1)[0]
    for item, key in zip(results, keys, strict=True):
        if key == winner_key:
            result = dict(item)
            if mode == "extraction":
                result["semantic_accuracy"] = statistics.median(
                    _as_float(candidate.get("semantic_accuracy")) for candidate in results
                )
            return result
    return dict(results[0])


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "y"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _as_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))
