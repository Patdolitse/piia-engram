from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval_agent_context_pack.py"
spec = importlib.util.spec_from_file_location("eval_agent_context_pack", SCRIPT)
eval_agent_context_pack = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(eval_agent_context_pack)


def test_eval_agent_context_pack_script_json_passes() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(proc.stdout)

    assert payload["schema"] == "agent_context_pack_eval.v1"
    assert payload["overall_passed"] is True
    assert payload["cases"]


def test_agent_context_pack_eval_schema_fields_are_stable() -> None:
    payload = eval_agent_context_pack.run_eval()

    assert set(payload) == {"schema", "overall_passed", "cases"}
    assert payload["schema"] == "agent_context_pack_eval.v1"
    first = payload["cases"][0]
    assert set(first) == {"name", "passed", "checks"}


def test_evaluate_pack_detects_forbidden_substring() -> None:
    pack = {
        "schema": "agent_context_pack.v1",
        "role": "reviewer",
        "context": {"trusted": [{"summary": "contains secret leak"}]},
    }
    expected = {
        "required_substrings": ["contains secret"],
        "forbidden_substrings": ["secret leak"],
    }

    result = eval_agent_context_pack.evaluate_pack("leak_case", pack, expected)

    assert result["passed"] is False
    assert result["checks"]["no_forbidden_substrings"] is False


def test_run_eval_uses_isolated_temp_store(tmp_path: Path) -> None:
    before = sorted(tmp_path.rglob("*"))
    payload = eval_agent_context_pack.run_eval(root=tmp_path)
    after = sorted(tmp_path.rglob("*"))

    assert payload["overall_passed"] is True
    assert before == []
    assert after == []
