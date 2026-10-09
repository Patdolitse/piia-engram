"""Prompt-free MCP setup: render a read-only plan, then apply its exact text.

Do not construct Engram here: even an empty store initialization writes
identity/knowledge files. Private config text is never part of the report.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
import io
import json
import os
from pathlib import Path
import sys

from . import claude_code_mcp as C
from . import setup_wizard as W

SCHEMA_ID = "piia-engram/setup"
SCHEMA_VERSION = 1
# This is a template, not a disclosure of config arguments/environment values.
CLAUDE_COMMAND = ("claude mcp add --scope user engram -e ENGRAM_DIR=<store> "
                  "-e ENGRAM_TOOLS=<mode> -e PYTHONIOENCODING=<encoding> "
                  "-- <python> -m piia_engram.mcp_server")


class UsageError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's message can echo arbitrary caller input (including secrets).
        raise UsageError("Invalid setup options; use --non-interactive [--apply] "
                         "[--json] [--clients IDS] [--lang zh|en].")


def _path_label(path: Path) -> str:
    """Use ~ and named location tokens rather than environment values."""
    bases = [("~", Path.home())]
    bases.extend((f"${key}", Path(raw).expanduser()) for key in
                 ("CLAUDE_CONFIG_DIR", "APPDATA", "LOCALAPPDATA", "ENGRAM_DIR")
                 if (raw := os.environ.get(key)))
    absolute = Path(os.path.abspath(path))
    for label, base in bases:
        try:
            tail = absolute.relative_to(Path(os.path.abspath(base)))
            return label + ("/" + tail.as_posix() if tail.parts else "")
        except ValueError:
            continue
    return path.as_posix()


def _bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


@dataclass
class ClientPlan:
    tool: dict = field(repr=False)
    row: dict
    before: bytes | None = field(default=None, repr=False)
    text: str | None = field(default=None, repr=False)
    legacy_before: bytes | None = field(default=None, repr=False)
    target: Path | None = field(default=None, repr=False)
    store_target: Path | None = field(default=None, repr=False)
    strict: bool = False


def _report(mode, lang, store, clients=None):
    return {"schema_id": SCHEMA_ID, "schema_version": SCHEMA_VERSION,
            "mode": mode, "language": lang, "result": "ok", "exit_code": 0,
            "store": {"path": _path_label(store),
                      "status": "directory" if store.is_dir() else
                                "invalid" if store.exists() else "missing", "writes": []},
            "clients": clients or [], "next_steps": []}


def _manual(row, reason):
    row.update(action="manual", result="manual", reason=reason, writes=[], commands=[])
    if row["id"] == "claude_code":
        row["manual_command"] = CLAUDE_COMMAND


def _build_plan(store: Path, selected: set[str] | None) -> list[ClientPlan]:
    detected = {tool["id"]: tool for tool in W._detect_tools()}
    plans = []
    server = W._find_mcp_server()
    for tool_id, config in W._tool_configs().items():
        tool = detected.get(tool_id)
        paths = config["config_paths"]
        path = tool["config_path"] if tool else (paths[0] if paths else None)
        chosen = tool_id in selected if selected is not None else tool is not None
        row = {"id": tool_id, "name": config["name"], "detected": tool is not None,
               "selected": chosen, "config_path": _path_label(path) if path else None,
               "action": "none", "result": "excluded" if tool and not chosen else "not_detected",
               "reason": "excluded" if tool and not chosen else "not_detected",
               "writes": [], "commands": [], "manual_command": None}
        plan = ClientPlan(tool or {}, row)
        plans.append(plan)
        if not chosen:
            continue
        if not tool:
            _manual(row, "not_detected")
            continue
        if store.exists() and not store.is_dir():
            _manual(row, "invalid_store")
            continue
        if not server:
            _manual(row, "missing_server")
            continue
        try:
            plan.target = path.resolve()
            plan.store_target = store.resolve()
            # A config alias must never turn into an identity/knowledge write.
            if plan.target.is_relative_to(plan.store_target):
                _manual(row, "unsafe_config_target")
                continue
            plan.strict = W._snippet_strict(store)
            if tool.get("register_via") == "claude_cli" and any(
                p.is_file() and p.stat().st_size > C.MAX_USER_CONFIG_BYTES
                for p in (path, C.legacy_path())
            ):
                _manual(row, "config_requires_manual_step")
                continue
            plan.before = _bytes(path)
            # Legacy writers may print migration messages or manual TOML
            # snippets. Keep those private, including exceptions/CLI stderr.
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                if tool.get("register_via") == "claude_cli":
                    plan.legacy_before = _bytes(C.legacy_path())
                    reg = C.register(
                        lambda env: W._engram_server_entry(sys.executable, server, str(store),
                            existing_env=env, engram_tools=None, store_root=store), preview=True)
                    if reg.registered:
                        row.update(result="unchanged", reason="already_registered")
                    elif reg.status == "planned":
                        row.update(action="register", result="planned", reason="registration",
                                   commands=[CLAUDE_COMMAND])
                    else:
                        _manual(row, "registration_requires_manual_step")
                else:
                    plan.text = W._write_tool_mcp_config(
                        tool, sys.executable, server, str(store), file_safety_root=store,
                        engram_tools=None, preview=True)
                    # Match the existing writer's universal-newline no-op rule.
                    existing = path.read_text(encoding="utf-8") if path.is_file() else None
                    if existing == plan.text:
                        row.update(result="unchanged", reason="already_configured")
                    else:
                        row.update(action="write", result="planned", reason="configuration",
                                   writes=[row["config_path"]])
        except (OSError, ValueError, W._ManualTomlStep):
            _manual(row, "config_requires_manual_step")
    return plans


def _store_writes(store, plans):
    writes = [p for p in plans if p.row["action"] == "write"]
    if not writes:
        return []
    result = [{"kind": "ledger", "path": _path_label(store / 'file_safety_ledger.jsonl*')}]
    if any(p.before is not None for p in writes):
        result.append({"kind": "backups", "path": _path_label(store / 'backups/file_safety/external/*.bak')})
    return result


def _apply(store, plans):
    server = W._find_mcp_server()
    for plan in plans:
        row = plan.row
        if row["action"] not in ("write", "register"):
            continue
        try:
            if (plan.tool["config_path"].resolve() != plan.target
                    or store.resolve() != plan.store_target
                    or W._snippet_strict(store) != plan.strict
                    or _bytes(plan.tool["config_path"]) != plan.before):
                _manual(row, "config_changed")
                continue
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                if row["action"] == "write":
                    W._write_config_text_with_backup(
                        plan.tool["config_path"], plan.text, backup_root=store,
                        authorized_external_write=True)
                    row["result"] = "written"
                else:
                    if _bytes(C.legacy_path()) != plan.legacy_before:
                        _manual(row, "config_changed")
                        continue
                    reg = W._register_claude_code(
                        sys.executable, server, str(store), engram_tools=None, interactive=False)
                    if reg.registered:
                        row["result"] = "registered"
                    else:
                        row.update(result="failed" if reg.status == "failed" else "manual",
                                   reason="registration_requires_manual_step",
                                   manual_command=CLAUDE_COMMAND)
        except (OSError, ValueError):
            row.update(result="failed", reason="apply_failed")


def _finish(report):
    clients = report['clients']
    if report['store']['status'] == 'invalid' or any(
        c['result'] in ('manual', 'failed') for c in clients
    ):
        report.update(result='partial', exit_code=1)
    if any(c['result'] in ('planned', 'written', 'registered') for c in clients):
        report['next_steps'].append('Restart the configured client, then run engram doctor --json.')
    if any(c['result'] in ('manual', 'failed') for c in clients):
        report['next_steps'].append('Complete the manual steps locally, then run setup again.')
    if not any(c['detected'] for c in clients):
        report['next_steps'].append('Install or open a supported client, then run setup again.')


def _emit(report, as_json):
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return
    zh = report['language'] == 'zh'
    print(('Engram 安装计划' if report['mode'] == 'plan' else 'Engram 安装结果') if zh
          else f"Engram setup {report['mode']}")
    print(f"{'存储目录' if zh else 'Store'}: {report['store']['path']} ({report['store']['status']})")
    for client in report['clients']:
        print(f"{client['name']}: detected={client['detected']}, "
              f"action={client['action']}, result={client['result']} ({client['config_path']})")
        for path in client['writes']:
            print(f"  Write: {path}")
        for command in client['commands']:
            print(f"  Command template: {command}")
        if client['manual_command']:
            print(f"  Manual command template: {client['manual_command']}")
    for write in report['store']['writes']:
        print(f"  {write['kind']}: {write['path']}")
    for step in report['next_steps']:
        print(step)


def run_agent_setup(argv: list[str]) -> int:
    parser = Parser(prog='engram setup', allow_abbrev=False)
    parser.add_argument('--non-interactive', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--lang', choices=('zh', 'en'), default='en')
    parser.add_argument('--clients')
    store = Path(os.environ.get('ENGRAM_DIR') or Path.home() / '.engram').expanduser().absolute()
    as_json = any(arg.split('=', 1)[0] == '--json' for arg in argv)
    report = _report('apply' if '--apply' in argv else 'plan', 'en', store)
    try:
        options = parser.parse_args(argv)
        if not options.non_interactive:
            raise UsageError('These setup options require --non-interactive.')
        selected = None
        if options.clients is not None:
            names = [name.strip() for name in options.clients.split(',')]
            if not all(names) or any(name not in W._tool_configs() for name in names):
                raise UsageError('Use comma-separated supported client IDs; see the setup documentation.')
            selected = set(names)
        report = _report('apply' if options.apply else 'plan', options.lang, store)
        plans = _build_plan(store, selected)
        report['clients'] = [p.row for p in plans]
        report['store']['writes'] = _store_writes(store, plans)
        if options.apply:
            _apply(store, plans)
            report['store']['status'] = 'directory' if store.is_dir() else report['store']['status']
        _finish(report)
    except UsageError as exc:
        report.update(result='usage_error', exit_code=2)
        report['next_steps'] = [str(exc)]
    if report['language'] == 'zh':
        translations = {
            'Restart the configured client, then run engram doctor --json.':
                '重启已配置的客户端，然后运行 engram doctor --json。',
            'Complete the manual steps locally, then run setup again.':
                '在本地完成手动步骤，然后重新运行 setup。',
            'Install or open a supported client, then run setup again.':
                '安装或打开支持的客户端，然后重新运行 setup。',
        }
        report['next_steps'] = [translations.get(step, step) for step in report['next_steps']]
    _emit(report, as_json)
    return report['exit_code']
