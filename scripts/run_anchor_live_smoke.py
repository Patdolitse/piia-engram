"""Run Anchor LIVE_SMOKE and append an authoritative structured record.

The JSONL record is the source of truth. Markdown output is a compatibility
view for humans and may be regenerated or omitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import portalocker


ROOT = Path(__file__).resolve().parents[1]
RECORD_SCHEMA = "anchor_live_smoke_run_record.v1"
DEFAULT_HISTORY_DIR = ROOT / ".engram-local-evidence" / "anchor-live-smoke-history"
DEFAULT_RECORD_FILE = "anchor-live-smoke-runs.jsonl"
DEFAULT_MARKDOWN_FILE = "summary.md"
DEFAULT_TIMEOUT_SECONDS = 120

SAFE_ERROR_MESSAGES = {
    "launch_failure": "process launch failed",
    "timeout": "process timed out",
    "nonzero_subprocess": "subprocess returned non-zero",
    "parse_failure": "subprocess output could not be parsed",
    "diagnostic_write_failure": "diagnostic artifact could not be written",
    "markdown_write_failure": "derived markdown view could not be written",
    "record_write_failure": "structured record could not be written",
}
SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

PRIVATE_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:[\\/][^\s\"'<>|]+"),
    re.compile(r"\\\\[^\\/\s\"'<>|]+[\\/][^\\/\s\"'<>|]+"),
    re.compile(r"(?i)(^|[\s\"'=:])/(Users|home|tmp|var/tmp)/[^\s\"'<>]*"),
    re.compile(r"(?i)\bauthorization\s*[:=]\s*['\"]?[A-Za-z][A-Za-z0-9._~+/=-]*(?:\s+[A-Za-z0-9._~+/=-]+)?['\"]?"),
    re.compile(r"(?i)(?<![A-Za-z0-9_])bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?ix)\b("
        r"api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
        r"password|passwd|pwd|token|secret|client[_-]?secret|private[_-]?key"
        r")\b\s*[:=]\s*['\"]?[^\s\"'<>|]+['\"]?"
    ),
)


def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def redact_text(text: str) -> str:
    redacted = text
    for pattern in PRIVATE_PATTERNS:
        redacted = pattern.sub("<redacted>", redacted)
    return redacted


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def safe_run_id(value: str) -> str:
    text = str(value or "")
    if SAFE_RUN_ID.fullmatch(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"run-{digest}"


def _strict_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _load_anchor_json(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        loaded = json.loads(stdout)
    except Exception:
        return None, "parse_failure"
    if not isinstance(loaded, dict):
        return None, "parse_failure"
    required = ("checked", "valid", "invalid", "unknown")
    if not all(key in loaded for key in required):
        return None, "parse_failure"
    for key in required:
        if _strict_non_negative_int(loaded.get(key)) is None:
            return None, "parse_failure"
    checked = int(loaded["checked"])
    status_total = int(loaded["valid"]) + int(loaded["invalid"]) + int(loaded["unknown"])
    if status_total > checked:
        return None, "parse_failure"
    for key in ("demoted_to_staging", "demoted", "superseded"):
        if key in loaded and _strict_non_negative_int(loaded.get(key)) is None:
            return None, "parse_failure"
    return loaded, None


def _artifact_ref(run_id: str, suffix: str) -> dict[str, str]:
    return {"kind": "local_redacted_artifact", "id": f"{run_id}.{suffix}.txt"}


def _write_diagnostic_artifacts(
    diagnostics_dir: Path,
    *,
    run_id: str,
    stdout: str,
    stderr: str,
) -> list[dict[str, str]]:
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    refs: list[dict[str, str]] = []
    for suffix, body in (("stdout", stdout), ("stderr", stderr)):
        if not body:
            continue
        ref = _artifact_ref(run_id, suffix)
        (diagnostics_dir / ref["id"]).write_text(redact_text(body), encoding="utf-8")
        refs.append(ref)
    return refs


def _base_record(*, run_id: str, timestamp: str) -> dict[str, Any]:
    return {
        "schema": RECORD_SCHEMA,
        "run_id": run_id,
        "timestamp": timestamp,
        "runner_status": "failed",
        "checked": 0,
        "valid": 0,
        "invalid": 0,
        "unknown": 0,
        "superseded": 0,
        "demoted_to_staging": 0,
        "subprocess_exit": None,
        "error_code": None,
        "evidence_ref": [],
    }


def _record_from_anchor_payload(
    *,
    run_id: str,
    timestamp: str,
    payload: dict[str, Any],
    subprocess_exit: int,
) -> dict[str, Any]:
    invalid = _safe_int(payload.get("invalid"))
    demoted = _safe_int(payload.get("demoted_to_staging", payload.get("demoted", 0)))
    status = "downgrade" if invalid > 0 or demoted > 0 else "stable"
    record = _base_record(run_id=run_id, timestamp=timestamp)
    record.update(
        {
            "runner_status": status,
            "checked": _safe_int(payload.get("checked")),
            "valid": _safe_int(payload.get("valid")),
            "invalid": invalid,
            "unknown": _safe_int(payload.get("unknown")),
            "superseded": _safe_int(payload.get("superseded")),
            "demoted_to_staging": demoted,
            "subprocess_exit": subprocess_exit,
            "error_code": None,
        }
    )
    return record


def append_jsonl_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    lock_path = path.parent / f".{path.name}.lock"
    with portalocker.Lock(lock_path, "a", timeout=5):
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(str(path), flags, 0o600)
        try:
            data = payload.encode("utf-8")
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                if written <= 0:
                    raise OSError("short write")
                offset += written
        finally:
            os.close(fd)


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except Exception:
            continue
        if isinstance(loaded, dict) and loaded.get("schema") == RECORD_SCHEMA:
            records.append(loaded)
    return records


def render_markdown_summary(records: list[dict[str, Any]]) -> str:
    lines = [
        "# Anchor live smoke log",
        "",
        "Local automatic `engram anchors check` evidence. The JSONL run record is authoritative.",
        "",
        "| time | checked | valid | invalid | unknown | human_confirm | note |",
        "|---|---|---|---|---|---|---|",
    ]
    for record in records:
        status = str(record.get("runner_status") or "failed")
        if status in {"stable", "downgrade"}:
            note = "downgrade" if status == "downgrade" else "stable"
            lines.append(
                "| {time} | {checked} | {valid} | {invalid} | {unknown} | 0 | {note} |".format(
                    time=str(record.get("timestamp", ""))[:16].replace("T", " "),
                    checked=_safe_int(record.get("checked")),
                    valid=_safe_int(record.get("valid")),
                    invalid=_safe_int(record.get("invalid")),
                    unknown=_safe_int(record.get("unknown")),
                    note=note,
                )
            )
        else:
            code = str(record.get("error_code") or "failed")
            lines.append(
                "| {time} | {status} | | | | 0 | diagnostic={code} |".format(
                    time=str(record.get("timestamp", ""))[:16].replace("T", " "),
                    status="parse-failed" if status == "parse_failed" else "failed",
                    code=code,
                )
            )
    lines.append("")
    return "\n".join(lines)


def _append_legacy_markdown_row(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(render_markdown_summary([]), encoding="utf-8")
    time_text = str(record.get("timestamp", ""))[:16].replace("T", " ")
    status = str(record.get("runner_status") or "failed")
    if status in {"stable", "downgrade"}:
        note = "downgrade" if status == "downgrade" else "stable"
        row = (
            f"| {time_text} | {_safe_int(record.get('checked'))} | {_safe_int(record.get('valid'))} | "
            f"{_safe_int(record.get('invalid'))} | {_safe_int(record.get('unknown'))} | 0 | {note} |"
        )
    else:
        cell = "parse-failed" if status == "parse_failed" else "failed"
        row = f"| {time_text} | {cell} | | | | 0 | diagnostic={record.get('error_code') or 'failed'} |"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(row + "\n")


def _default_command(repo_root: str) -> list[str]:
    return [
        sys.executable,
        "-c",
        "from piia_engram.setup_wizard import main; raise SystemExit(main())",
        "anchors",
        "check",
        "--root",
        repo_root,
        "--json",
    ]


def run_once(args: argparse.Namespace) -> tuple[int, dict[str, Any] | None]:
    run_id = safe_run_id(args.run_id) if args.run_id else uuid.uuid4().hex
    timestamp = args.timestamp or utc_now_text()
    record = _base_record(run_id=run_id, timestamp=timestamp)
    repo_root = Path(args.repo_root)
    command = list(args.command) if args.command else _default_command(args.repo_root)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if args.engram_dir:
        env["ENGRAM_DIR"] = args.engram_dir
    pythonpath_parts = [str(repo_root / "src")]
    if env.get("PYTHONPATH"):
        pythonpath_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    stdout = ""
    stderr = ""
    try:
        result = subprocess.run(
            command,
            cwd=args.cwd or str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=args.timeout,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        record["subprocess_exit"] = result.returncode
    except FileNotFoundError:
        record["error_code"] = "launch_failure"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        record["error_code"] = "timeout"
    except Exception:
        record["error_code"] = "launch_failure"
    else:
        if result.returncode != 0:
            record["error_code"] = "nonzero_subprocess"
        else:
            payload, error_code = _load_anchor_json(stdout)
            if payload is None:
                record["runner_status"] = "parse_failed"
                record["error_code"] = error_code
            else:
                record = _record_from_anchor_payload(
                    run_id=run_id,
                    timestamp=timestamp,
                    payload=payload,
                    subprocess_exit=result.returncode,
                )

    if record.get("error_code"):
        try:
            record["evidence_ref"] = _write_diagnostic_artifacts(
                Path(args.diagnostics_dir),
                run_id=run_id,
                stdout=stdout,
                stderr=stderr,
            )
        except Exception:
            record["evidence_ref"] = []
            print(SAFE_ERROR_MESSAGES["diagnostic_write_failure"], file=sys.stderr)

    record_file = Path(args.record_file)
    try:
        append_jsonl_record(record_file, record)
    except Exception:
        print(SAFE_ERROR_MESSAGES["record_write_failure"], file=sys.stderr)
        return 1, None

    try:
        if args.markdown_log:
            _append_legacy_markdown_row(Path(args.markdown_log), record)
        elif not args.no_markdown:
            latest_records = _read_records(record_file)
            summary = render_markdown_summary(latest_records)
            summary_path = Path(args.history_dir) / DEFAULT_MARKDOWN_FILE
            tmp_path = summary_path.with_suffix(summary_path.suffix + ".tmp")
            tmp_path.write_text(summary, encoding="utf-8")
            tmp_path.replace(summary_path)
    except Exception:
        print(SAFE_ERROR_MESSAGES["markdown_write_failure"], file=sys.stderr)
        return 1, record

    status = str(record.get("runner_status") or "failed")
    if status in {"stable", "downgrade"}:
        return 0, record
    return 1, record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT), help="Repository root checked by `engram anchors check`.")
    parser.add_argument("--engram-dir", default="", help="Isolated Engram store for the live smoke run.")
    parser.add_argument("--history-dir", default=str(DEFAULT_HISTORY_DIR), help="Local evidence output directory.")
    parser.add_argument("--record-file", default="", help="Authoritative JSONL run record path.")
    parser.add_argument("--diagnostics-dir", default="", help="Private redacted diagnostic artifact directory.")
    parser.add_argument("--markdown-log", default="", help="Optional legacy Markdown log to append without rewriting.")
    parser.add_argument("--no-markdown", action="store_true", help="Do not write the derived Markdown summary.")
    parser.add_argument("--cwd", default="", help="Subprocess working directory.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="Subprocess timeout in seconds.")
    parser.add_argument("--run-id", default="", help="Deterministic run id for tests.")
    parser.add_argument("--timestamp", default="", help="Deterministic UTC timestamp for tests.")
    parser.add_argument("--json", action="store_true", help="Print sanitized run record JSON.")
    parser.add_argument("--command", nargs=argparse.REMAINDER, help="Override command after this flag.")
    args = parser.parse_args()

    history_dir = Path(args.history_dir)
    if not args.record_file:
        args.record_file = str(history_dir / DEFAULT_RECORD_FILE)
    if not args.diagnostics_dir:
        args.diagnostics_dir = str(history_dir / "diagnostics")

    exit_code, record = run_once(args)
    if args.json and record is not None:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    elif record is not None:
        status = str(record.get("runner_status") or "failed")
        if exit_code == 0:
            print(f"anchor live smoke {status}")
        else:
            code = str(record.get("error_code") or "failed")
            print(SAFE_ERROR_MESSAGES.get(code, "anchor live smoke failed"), file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
