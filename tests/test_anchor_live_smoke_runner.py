from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_anchor_live_smoke.py"
COLLECTOR = ROOT / "scripts" / "collect_anchor_live_smoke_evidence.py"
INSTALLER = ROOT / "scripts" / "install_anchor_live_smoke_task.ps1"


def _python_json(payload: dict[str, object]) -> list[str]:
    code = "import json; print(json.dumps(%r))" % payload
    return [sys.executable, "-c", code]


def _python_exit(code: int, stdout: str = "", stderr: str = "") -> list[str]:
    script = (
        "import sys; "
        f"sys.stdout.write({stdout!r}); "
        f"sys.stderr.write({stderr!r}); "
        f"raise SystemExit({code})"
    )
    return [sys.executable, "-c", script]


def _run(tmp_path: Path, command: list[str], *, run_id: str = "run") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--repo-root",
            str(ROOT),
            "--history-dir",
            str(tmp_path),
            "--record-file",
            str(tmp_path / "runs.jsonl"),
            "--diagnostics-dir",
            str(tmp_path / "diagnostics"),
            "--no-markdown",
            "--run-id",
            run_id,
            "--timestamp",
            "2026-07-10T02:00:00Z",
            "--json",
            "--command",
            *command,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema": "anchor_live_smoke_run_record.v1",
        "run_id": "stable",
        "timestamp": "2026-07-10T02:00:00Z",
        "runner_status": "stable",
        "checked": 3,
        "valid": 3,
        "invalid": 0,
        "unknown": 0,
        "superseded": 0,
        "demoted_to_staging": 0,
        "subprocess_exit": 0,
        "error_code": None,
        "evidence_ref": [],
    }
    record.update(overrides)
    return record


def _assert_no_unsafe_fragments(text: str, fragments: list[str]) -> None:
    for fragment in fragments:
        if fragment in text:
            raise AssertionError("unsafe fixture leaked")


def test_success_stable_record_and_exit_zero(tmp_path: Path) -> None:
    result = _run(tmp_path, _python_json({"checked": 3, "valid": 3, "invalid": 0, "unknown": 0}))
    record = json.loads(result.stdout)

    assert result.returncode == 0
    assert record["schema"] == "anchor_live_smoke_run_record.v1"
    assert record["runner_status"] == "stable"
    assert record["checked"] == 3
    assert record["subprocess_exit"] == 0
    assert record["error_code"] is None
    assert _records(tmp_path / "runs.jsonl") == [record]


def test_detected_downgrade_is_distinct_success(tmp_path: Path) -> None:
    result = _run(tmp_path, _python_json({"checked": 3, "valid": 2, "invalid": 1, "unknown": 0}))
    record = json.loads(result.stdout)

    assert result.returncode == 0
    assert record["runner_status"] == "downgrade"
    assert record["invalid"] == 1


def test_launch_failure_records_failure_and_nonzero(tmp_path: Path) -> None:
    result = _run(tmp_path, ["definitely-missing-anchor-smoke-executable"], run_id="launch")
    record = json.loads(result.stdout)

    assert result.returncode == 1
    assert record["runner_status"] == "failed"
    assert record["error_code"] == "launch_failure"
    assert record["subprocess_exit"] is None


def test_timeout_records_failure_and_nonzero(tmp_path: Path) -> None:
    code = "import time; time.sleep(5)"
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--repo-root",
            str(ROOT),
            "--history-dir",
            str(tmp_path),
            "--record-file",
            str(tmp_path / "runs.jsonl"),
            "--diagnostics-dir",
            str(tmp_path / "diagnostics"),
            "--timeout",
            "0.1",
            "--no-markdown",
            "--run-id",
            "timeout",
            "--timestamp",
            "2026-07-10T02:00:00Z",
            "--json",
            "--command",
            sys.executable,
            "-c",
            code,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    record = json.loads(result.stdout)

    assert result.returncode == 1
    assert record["error_code"] == "timeout"


def test_nonzero_subprocess_records_failure_and_nonzero(tmp_path: Path) -> None:
    result = _run(tmp_path, _python_exit(7, stdout='{"checked":1}\n'), run_id="nonzero")
    record = json.loads(result.stdout)

    assert result.returncode == 1
    assert record["error_code"] == "nonzero_subprocess"
    assert record["subprocess_exit"] == 7


def test_parse_failure_records_failure_and_nonzero(tmp_path: Path) -> None:
    result = _run(tmp_path, _python_exit(0, stdout="not-json"), run_id="parse")
    record = json.loads(result.stdout)

    assert result.returncode == 1
    assert record["runner_status"] == "parse_failed"
    assert record["error_code"] == "parse_failure"


def test_malformed_anchor_json_is_parse_failure(tmp_path: Path) -> None:
    malformed_cases = [
        {"checked": "3", "valid": 3, "invalid": 0, "unknown": 0},
        {"checked": True, "valid": 1, "invalid": 0, "unknown": 0},
        {"checked": 2, "valid": 3, "invalid": 0, "unknown": 0},
        {"checked": 1, "valid": 1, "invalid": -1, "unknown": 0},
    ]

    for index, payload in enumerate(malformed_cases):
        result = _run(tmp_path, _python_json(payload), run_id=f"malformed-{index}")
        record = json.loads(result.stdout)
        assert result.returncode == 1
        assert record["runner_status"] == "parse_failed"
        assert record["error_code"] == "parse_failure"


def test_write_failure_is_nonzero_and_does_not_claim_success(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--repo-root",
            str(ROOT),
            "--record-file",
            str(blocker / "runs.jsonl"),
            "--diagnostics-dir",
            str(tmp_path / "diagnostics"),
            "--no-markdown",
            "--command",
            *_python_json({"checked": 1, "valid": 1, "invalid": 0, "unknown": 0}),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "structured record could not be written" in result.stderr
    assert "anchor live smoke stable" not in result.stdout


def test_markdown_write_failure_returns_nonzero_without_traceback(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--repo-root",
            str(ROOT),
            "--history-dir",
            str(tmp_path),
            "--record-file",
            str(tmp_path / "runs.jsonl"),
            "--diagnostics-dir",
            str(tmp_path / "diagnostics"),
            "--markdown-log",
            str(blocker / "SMOKE_LOG.md"),
            "--command",
            *_python_json({"checked": 1, "valid": 1, "invalid": 0, "unknown": 0}),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    body = result.stdout + result.stderr

    assert result.returncode == 1
    assert "derived markdown view could not be written" in result.stderr
    assert "Traceback" not in body


def test_failure_artifacts_are_redacted_and_referenced_opaquely(tmp_path: Path) -> None:
    unsafe = "Authorization: Bearer abc.def token=secret E:\\Private\\debug.log"
    result = _run(tmp_path, _python_exit(0, stdout=unsafe), run_id="unsafe")
    record = json.loads(result.stdout)
    body = result.stdout + result.stderr + (tmp_path / "runs.jsonl").read_text(encoding="utf-8")
    leaked_fragments = ["Bearer abc.def", "token=secret", "E:\\Private"]

    assert result.returncode == 1
    _assert_no_unsafe_fragments(body, leaked_fragments)
    assert record["evidence_ref"] == [{"id": "unsafe.stdout.txt", "kind": "local_redacted_artifact"}]
    artifact = tmp_path / "diagnostics" / "unsafe.stdout.txt"
    artifact_text = artifact.read_text(encoding="utf-8")
    assert "<redacted>" in artifact_text
    _assert_no_unsafe_fragments(artifact_text, leaked_fragments)


def test_redaction_covers_equals_quotes_and_bare_bearer_without_echo(tmp_path: Path) -> None:
    unsafe_parts = [
        "Authorization=Digest abc123",
        "Bearer xyz.token",
        "api_key='quoted-secret'",
        'client_secret="double-quoted"',
    ]
    result = _run(tmp_path, _python_exit(0, stdout=" ".join(unsafe_parts)), run_id="redact")
    record = json.loads(result.stdout)
    body = result.stdout + result.stderr + (tmp_path / "runs.jsonl").read_text(encoding="utf-8")
    artifact_text = (tmp_path / "diagnostics" / record["evidence_ref"][0]["id"]).read_text(encoding="utf-8")

    assert result.returncode == 1
    assert "<redacted>" in artifact_text
    _assert_no_unsafe_fragments(body, unsafe_parts)
    _assert_no_unsafe_fragments(artifact_text, unsafe_parts)


def test_diagnostic_write_failure_returns_nonzero_without_traceback(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--repo-root",
            str(ROOT),
            "--history-dir",
            str(tmp_path),
            "--record-file",
            str(tmp_path / "runs.jsonl"),
            "--diagnostics-dir",
            str(blocker / "diagnostics"),
            "--no-markdown",
            "--json",
            "--command",
            *_python_exit(0, stdout="not-json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    body = result.stdout + result.stderr

    assert result.returncode == 1
    assert "diagnostic artifact could not be written" in result.stderr
    assert "Traceback" not in body


def test_unsafe_run_id_is_converted_to_opaque_artifact_ref(tmp_path: Path) -> None:
    result = _run(tmp_path, _python_exit(0, stdout="not-json"), run_id="../escape")
    record = json.loads(result.stdout)
    ref_id = record["evidence_ref"][0]["id"]

    assert result.returncode == 1
    assert "/" not in ref_id
    assert "\\" not in ref_id
    assert ".." not in ref_id
    assert ref_id.startswith("run-")
    assert (tmp_path / "diagnostics" / ref_id).exists()


def test_jsonl_append_is_locked_and_line_delimited_under_threads(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_anchor_live_smoke", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    target = tmp_path / "runs.jsonl"

    def write_one(index: int) -> None:
        module.append_jsonl_record(
            target,
            {
                "schema": module.RECORD_SCHEMA,
                "run_id": f"run-{index}",
                "timestamp": "2026-07-10T02:00:00Z",
                "runner_status": "stable",
                "checked": index,
                "valid": index,
                "invalid": 0,
                "unknown": 0,
                "superseded": 0,
                "demoted_to_staging": 0,
                "subprocess_exit": 0,
                "error_code": None,
                "evidence_ref": [],
            },
        )

    threads = [threading.Thread(target=write_one, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = target.read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in lines]
    assert len(lines) == 20
    assert {item["run_id"] for item in parsed} == {f"run-{i}" for i in range(20)}


def test_legacy_markdown_log_is_append_only_and_preserves_old_parse_failed_row(tmp_path: Path) -> None:
    log = tmp_path / "SMOKE_LOG.md"
    old = (
        "# Anchor live smoke log\n\n"
        "| time | checked | valid | invalid | unknown | human_confirm | note |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 2026-07-10 10:00 | parse-failed | | | | 0 | see raw output |\n"
    )
    log.write_text(old, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--repo-root",
            str(ROOT),
            "--history-dir",
            str(tmp_path),
            "--record-file",
            str(tmp_path / "runs.jsonl"),
            "--diagnostics-dir",
            str(tmp_path / "diagnostics"),
            "--markdown-log",
            str(log),
            "--run-id",
            "append",
            "--timestamp",
            "2026-07-10T11:00:00Z",
            "--command",
            *_python_json({"checked": 1, "valid": 1, "invalid": 0, "unknown": 0}),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    updated = log.read_text(encoding="utf-8")
    assert updated.startswith(old)
    assert "| 2026-07-10 10:00 | parse-failed | | | | 0 | see raw output |" in updated
    assert "| 2026-07-10 11:00 | 1 | 1 | 0 | 0 | 0 | stable |" in updated


def test_collector_excludes_failed_run_from_anchor_sample_count(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    records = [
        _run_record(),
        _run_record(
            run_id="parse",
            timestamp="2026-07-10T02:01:00Z",
            runner_status="parse_failed",
            checked=99,
            valid=99,
            subprocess_exit=0,
            error_code="parse_failure",
            evidence_ref=[{"kind": "local_redacted_artifact", "id": "parse.stdout.txt"}],
        ),
    ]
    runs.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    zero_anchor = tmp_path / "zero-anchor.json"
    zero_anchor.write_text(
        json.dumps(
            {
                "anchors": {
                    "checked": 0,
                    "valid": 0,
                    "invalid": 0,
                    "unknown": 0,
                    "superseded": 0,
                    "demoted_to_staging": 0,
                }
            }
        ),
        encoding="utf-8",
    )
    zero_live = tmp_path / "zero-live.json"
    zero_live.write_text(
        json.dumps(
            {
                "live_smoke": {
                    "runs": 0,
                    "passed": 0,
                    "failed": 0,
                    "failure_classes": {},
                    "status_counts": {},
                }
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(COLLECTOR),
            "--json",
            "--synthetic",
            "--anchor-json",
            str(zero_anchor),
            "--live-smoke-json",
            str(zero_live),
            "--live-smoke-run-jsonl",
            str(runs),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    payload = json.loads(result.stdout)

    assert payload["anchors"]["checked"] == 3
    assert payload["anchors"]["valid"] == 3
    assert payload["live_smoke"]["runs"] == 2
    assert payload["live_smoke"]["passed"] == 1
    assert payload["live_smoke"]["failed"] == 1
    assert payload["live_smoke"]["status_counts"] == {"stable": 1, "parse_failed": 1}
    assert payload["live_smoke"]["failure_classes"] == {"parse_failure": 1}


def test_collector_rejects_semantically_corrupt_stable_record(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    runs.write_text(
        json.dumps(
            _run_record(
                run_id="bad-stable",
                checked="15",
                valid="15",
                error_code="parse_failure",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(COLLECTOR), "--json", "--synthetic", "--live-smoke-run-jsonl", str(runs)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["anchors"]["checked"] == 5
    assert payload["anchors"]["valid"] == 3
    assert payload["live_smoke"]["runs"] == 4
    assert payload["live_smoke"]["passed"] == 3
    assert payload["live_smoke"]["failed"] == 1
    assert payload["live_smoke"]["status_counts"] == {"stable": 3, "parse_failed": 1}
    assert payload["live_smoke"]["failure_classes"] == {"invalid_run_record": 1}


def test_collector_rejects_missing_required_stable_record(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    runs.write_text(
        json.dumps(
            {
                "schema": "anchor_live_smoke_run_record.v1",
                "runner_status": "stable",
                "subprocess_exit": 0,
                "error_code": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(COLLECTOR), "--json", "--synthetic", "--live-smoke-run-jsonl", str(runs)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["live_smoke"]["passed"] == 3
    assert payload["live_smoke"]["failed"] == 1
    assert payload["live_smoke"]["status_counts"] == {"stable": 3, "parse_failed": 1}
    assert payload["live_smoke"]["failure_classes"] == {"invalid_run_record": 1}


def test_collector_rejects_bool_and_float_subprocess_exit(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    runs.write_text(
        json.dumps(_run_record(run_id="bool-exit", subprocess_exit=False))
        + "\n"
        + json.dumps(_run_record(run_id="float-exit", subprocess_exit=0.0))
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(COLLECTOR), "--json", "--synthetic", "--live-smoke-run-jsonl", str(runs)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["live_smoke"]["passed"] == 3
    assert payload["live_smoke"]["failed"] == 2
    assert payload["live_smoke"]["status_counts"] == {"stable": 3, "failed": 2}


def test_collector_rejects_parse_failed_without_matching_error_code(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    runs.write_text(
        json.dumps(_run_record(run_id="parse-missing", runner_status="parse_failed", error_code=None))
        + "\n"
        + json.dumps(_run_record(run_id="parse-wrong", runner_status="parse_failed", error_code="timeout"))
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(COLLECTOR), "--json", "--synthetic", "--live-smoke-run-jsonl", str(runs)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["live_smoke"]["passed"] == 3
    assert payload["live_smoke"]["failed"] == 2
    assert payload["live_smoke"]["status_counts"] == {"stable": 3, "failed": 2}
    assert payload["live_smoke"]["failure_classes"] == {"invalid_run_record": 2}


def test_collector_validates_safe_evidence_refs(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    runs.write_text(
        json.dumps(
            _run_record(
                run_id="safe-parse",
                runner_status="parse_failed",
                error_code="parse_failure",
                evidence_ref=[{"kind": "local_redacted_artifact", "id": "safe.stdout.txt"}],
            )
        )
        + "\n"
        + json.dumps(
            _run_record(
                run_id="unsafe-ref",
                runner_status="parse_failed",
                error_code="parse_failure",
                evidence_ref=[{"kind": "local_redacted_artifact", "id": "../escape.txt"}],
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(COLLECTOR), "--json", "--synthetic", "--live-smoke-run-jsonl", str(runs)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["live_smoke"]["failed"] == 2
    assert payload["live_smoke"]["status_counts"] == {"stable": 3, "parse_failed": 2}
    assert payload["live_smoke"]["failure_classes"] == {
        "parse_failure": 1,
        "invalid_run_record": 1,
    }


def test_demotion_only_downgrade_is_preserved_and_collected(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        _python_json(
            {
                "checked": 3,
                "valid": 3,
                "invalid": 0,
                "unknown": 0,
                "superseded": 0,
                "demoted_to_staging": 1,
            }
        ),
        run_id="demotion",
    )
    record = json.loads(result.stdout)
    runs = tmp_path / "runs.jsonl"

    assert result.returncode == 0
    assert record["runner_status"] == "downgrade"
    assert record["demoted_to_staging"] == 1

    collector = subprocess.run(
        [sys.executable, str(COLLECTOR), "--json", "--synthetic", "--live-smoke-run-jsonl", str(runs)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    payload = json.loads(collector.stdout)

    assert collector.returncode == 0
    assert payload["anchors"]["checked"] == 8
    assert payload["anchors"]["valid"] == 6
    assert payload["anchors"]["invalid"] == 1
    assert payload["anchors"]["demoted_to_staging"] == 2
    assert payload["live_smoke"]["passed"] == 4
    assert payload["live_smoke"]["status_counts"] == {"stable": 3, "downgrade": 1}


def test_collector_counts_malformed_jsonl_as_failed_run_with_consistent_totals(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    runs.write_text(
        "not-json\n"
        + json.dumps({"schema": "anchor_live_smoke_run_record.v1", "runner_status": "surprise"})
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(COLLECTOR), "--json", "--synthetic", "--live-smoke-run-jsonl", str(runs)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    live = payload["live_smoke"]

    assert result.returncode == 0
    assert live["runs"] == 5
    assert live["passed"] == 3
    assert live["failed"] == 2
    assert live["status_counts"] == {"stable": 3, "parse_failed": 2}
    assert live["passed"] + live["failed"] == live["runs"]
    assert live["status_counts"]["stable"] == live["passed"]
    assert live["status_counts"]["parse_failed"] == live["failed"]


def test_installer_copies_runner_and_rejects_codex_runtime_paths() -> None:
    text = INSTALLER.read_text(encoding="utf-8")

    assert "Copy-Item -LiteralPath $RunnerScript -Destination $installedRunner -Force" in text
    assert "& $PythonExe $installedRunner --help" in text
    assert "$installedRunner" in text
    assert "manifest.json" in text
    assert "New-ScheduledTaskAction -Execute $env:ComSpec" in text
    assert "PythonExe must be a durable Python install" in text
    assert "\\codex-runtimes\\" in text
    assert "while offset < len(data)" in RUNNER.read_text(encoding="utf-8")
