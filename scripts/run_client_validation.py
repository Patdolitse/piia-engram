"""Scaffold a client-validation evidence directory.

This script does not run Hermes, OpenClaw, or any other AI client. It creates a
standard evidence directory and optional before/after file snapshots so manual
or automated client runs stop hand-rolling their reports.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from piia_engram.client_validation import (
    build_run_meta,
    build_tool_locations,
    evidence_dir_layout,
    snapshot_files,
)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an Engram client-validation evidence scaffold.")
    parser.add_argument("--run-root", required=True, help="Base directory for validation runs.")
    parser.add_argument("--client-id", required=True, help="Client id, e.g. hermes or openclaw.")
    parser.add_argument("--client-version", default="unknown")
    parser.add_argument("--surface", default="CLI")
    parser.add_argument("--model", default="none")
    parser.add_argument("--engram-mode", default="MCP read-only")
    parser.add_argument("--environment-arm", default="Engram-isolated")
    parser.add_argument("--client-executable", default="")
    parser.add_argument("--client-runtime", default="")
    parser.add_argument("--engram-mcp-executable", default="")
    parser.add_argument("--file-bridge-command", default="")
    parser.add_argument("--copied-client-home", default="")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--known-limitation", action="append", default=[])
    parser.add_argument("--snapshot-file", action="append", default=[], help="File to snapshot for zero-pollution evidence.")
    return parser.parse_args()


def _is_under(child: Path, parent: Path) -> bool:
    child_resolved = child.resolve()
    parent_resolved = parent.resolve()
    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError:
        return False
    return True


def main() -> None:
    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_root = Path(args.run_root)
    run_dir = run_root / args.client_id / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    for item in evidence_dir_layout():
        target = run_dir / item.rstrip("/")
        if item.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)

    workspace = Path(args.workspace) if args.workspace else run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    copied_client_home = Path(args.copied_client_home) if args.copied_client_home else None
    meta = build_run_meta(
        client_id=args.client_id,
        client_version=args.client_version,
        surface=args.surface,
        model=args.model,
        engram_mode=args.engram_mode,
        environment_arm=args.environment_arm,
        workspace_isolated=_is_under(workspace, run_dir),
        home_isolated=_is_under(copied_client_home, run_root) if copied_client_home else False,
        write_tools_allowed=False,
        known_limitations=args.known_limitation,
        run_root=str(run_dir),
        timestamp=timestamp,
    )
    tools = build_tool_locations(
        client_executable=args.client_executable,
        client_runtime=args.client_runtime,
        engram_mcp_executable=args.engram_mcp_executable,
        file_bridge_command=args.file_bridge_command,
        copied_client_home=str(copied_client_home) if copied_client_home else "",
        isolated_workspace=str(workspace),
        run_root=str(run_dir),
    )

    _write_json(run_dir / "run_meta.json", meta)
    _write_json(run_dir / "tool_locations.json", tools)
    (run_dir / "client_version.txt").write_text(args.client_version + "\n", encoding="utf-8")
    (run_dir / "client_config_summary.txt").write_text("待补充：本次客户端配置摘要。\n", encoding="utf-8")
    (run_dir / "timings.json").write_text("{}\n", encoding="utf-8")

    snapshots = snapshot_files(args.snapshot_file)
    _write_json(run_dir / "parsed" / "snapshot_before.json", snapshots)
    _write_json(run_dir / "parsed" / "snapshot_after.json", [])
    _write_json(run_dir / "parsed" / "zero_pollution.json", {
        "status": "pending",
        "clean": None,
        "message": "Client run has not completed; capture after-snapshot before claiming zero pollution.",
    })
    (run_dir / "zero_pollution.txt").write_text(
        "# 零污染校验\n\n"
        "- 状态：待补充\n"
        "- 结论：尚未完成真实客户端运行，不能声称通过。\n"
        "- 下一步：客户端运行结束后重新采集 after snapshot，再生成正式对比。\n",
        encoding="utf-8",
    )

    print(json.dumps({"run_dir": str(run_dir), "created": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
