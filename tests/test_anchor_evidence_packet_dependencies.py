from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
COLLECTOR = SCRIPTS / "collect_anchor_live_smoke_evidence.py"


def _load_script_attr(script: Path, name: str):
    spec = importlib.util.spec_from_file_location(script.stem, script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


def test_m11_reply_renderer_exports_render_reply() -> None:
    render_reply = _load_script_attr(SCRIPTS / "render_anchor_forum_reply.py", "render_reply")

    assert callable(render_reply)
    text = render_reply({
        "schema": "anchor_live_smoke_evidence.v1",
        "public_safe": True,
        "anchors": {
            "checked": 1,
            "valid": 1,
            "invalid": 0,
            "unknown": 0,
            "superseded": 0,
            "demoted_to_staging": 0,
        },
        "live_smoke": {"runs": 1, "passed": 1, "failed": 0, "failure_classes": {}},
    })

    assert "Owner confirmation required before posting" in text
    assert "not a statistically significant result" in text


def test_m11_collector_supports_synthetic_json_mode() -> None:
    result = subprocess.run(
        [sys.executable, str(COLLECTOR), "--json", "--synthetic"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["schema"] == "anchor_live_smoke_evidence.v1"
    assert payload["public_safe"] is True
    assert payload["mode"] == "synthetic"


def test_m11_live_collector_keeps_knowledge_reads_read_only() -> None:
    text = COLLECTOR.read_text(encoding="utf-8")

    assert "_update_access=False" in text
    assert "diagnose_wrap_up_session.py" in text
    assert "--json" in text
