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


def test_agent_context_pack_eval_uses_isolated_store_and_not_live_engram_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    live_store = tmp_path / "live-engram-store"
    live_store.mkdir()
    sentinel = live_store / "sentinel.txt"
    sentinel.write_text("LIVE_AGENT_CONTEXT_STORE_SENTINEL", encoding="utf-8")
    before_files = sorted(path.relative_to(live_store).as_posix() for path in live_store.rglob("*"))
    monkeypatch.setenv("ENGRAM_DIR", str(live_store))

    payload = eval_agent_context_pack.run_eval()

    after_files = sorted(path.relative_to(live_store).as_posix() for path in live_store.rglob("*"))
    payload_blob = json.dumps(payload, ensure_ascii=False)

    assert payload["overall_passed"] is True
    assert before_files == after_files == ["sentinel.txt"]
    assert sentinel.read_text(encoding="utf-8") == "LIVE_AGENT_CONTEXT_STORE_SENTINEL"
    assert "LIVE_AGENT_CONTEXT_STORE_SENTINEL" not in payload_blob
