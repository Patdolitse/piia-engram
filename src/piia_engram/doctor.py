"""engram doctor — config integrity report and functional checks.

Split out of setup_wizard.py. Helpers that tests monkeypatch on
``piia_engram.setup_wizard`` are accessed late-bound via ``W.<name>`` so
existing patches keep intercepting.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from . import setup_wizard as W
from .i18n import t as _t


def _detect_installed_tools() -> list[dict]:
    """扫描系统中实际安装的 AI 工具。

    不仅检查配置文件是否存在，还检查工具本身是否安装（配置目录存在）。
    返回 [{tool_id, name, config_path, format, verified, status, config, servers}]。
    - status: "configured" (有 engram 条目), "installed" (工具在但没配 engram)
    - verified: True = evidence-tracked setup path, False = expected/community setup path
    """
    results = []
    for tool_id, cfg in W._tool_configs().items():
        fmt = cfg.get("format", "json")
        server_key = cfg.get("server_key", "mcpServers")
        verified = cfg.get("verified", False)
        for config_path in cfg["config_paths"]:
            # 检查工具是否安装（配置目录存在 = 工具装了）
            tool_dir = config_path.parent
            if not tool_dir.exists():
                continue

            config = W._read_mcp_config(config_path, fmt=fmt)

            # 按工具的 server_key 取 MCP servers 段
            servers = config.get(server_key, {})
            # TOML 回退：也检查下划线变体
            if not servers and server_key == "mcpServers":
                servers = config.get("mcp_servers", {})
            has_engram = "engram" in servers

            results.append({
                "tool_id": tool_id,
                "name": cfg["name"],
                "config_path": config_path,
                "format": fmt,
                "server_key": server_key,
                "verified": verified,
                "status": "configured" if has_engram else "installed",
                "config": config,
                "servers": servers,
            })
            break  # 每个工具只取第一个匹配的路径
    return results


def _file_sha256_12(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except (OSError, PermissionError):
        return ""


def _servers_from_config(config: dict, server_key: str) -> dict:
    servers = config.get(server_key, {})
    if isinstance(servers, dict) and servers:
        return servers
    if server_key == "mcpServers":
        fallback = config.get("mcp_servers", {})
    elif server_key == "mcp_servers":
        fallback = config.get("mcpServers", {})
    else:
        fallback = {}
    return fallback if isinstance(fallback, dict) else {}


def _shared_instruction_candidates(home: Path) -> list[Path]:
    data_dir = Path(os.environ.get("ENGRAM_DIR", "") or home / ".engram")
    candidates = [
        data_dir / "shared_instructions.md",
        home / ".piia" / "shared_instructions.md",
    ]
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def _claude_hook_rows(home: Path) -> list[dict]:
    settings_path = home / ".claude" / "settings.json"
    settings_exists = settings_path.is_file()
    settings: dict = {}
    if settings_exists:
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            settings = {}

    hook_specs = (
        (
            "Stop",
            "Stop",
            ("auto_save_on_stop", "piia_engram.hooks.auto_save_on_stop"),
            "any",
        ),
        (
            "PreCompact",
            "PreCompact",
            ("CLAUDE_INVOKED_BY=engram_precompact",
             "piia_engram.hooks.auto_save_on_stop"),
            "all",
        ),
        (
            "SessionStart",
            "SessionStart (resume brief inject)",
            ("auto_inject_resume_brief",
             "piia_engram.hooks.auto_inject_resume_brief"),
            "any",
        ),
        (
            "PostCompact",
            "PostCompact (summary absorb)",
            ("auto_absorb_compact",
             "piia_engram.hooks.auto_absorb_compact"),
            "any",
        ),
    )

    rows: list[dict] = []
    hooks = settings.get("hooks", {}) if isinstance(settings, dict) else {}
    for event, label, markers, match_mode in hook_specs:
        registered = False
        matcher = all if match_mode == "all" else any
        for event_group in hooks.get(event, []) if isinstance(hooks, dict) else []:
            for hook in event_group.get("hooks", []):
                cmd = str(hook.get("command", ""))
                if matcher(marker in cmd for marker in markers):
                    registered = True
                    break
            if registered:
                break
        rows.append({
            "event": event,
            "label": label,
            "settings_path": str(settings_path),
            "settings_exists": settings_exists,
            "registered": registered,
            "sha256_12": _file_sha256_12(settings_path) if settings_exists else "",
        })
    return rows


def _build_config_integrity_report(cwd: Path | None = None) -> dict:
    """Build a metadata-only portability/integrity report for local AI config.

    The report intentionally includes hashes, counts, booleans, and paths, but
    never returns instruction file bodies or project rule lines.
    """
    mcp_configs: list[dict] = []
    for tool_id, cfg in W._tool_configs().items():
        fmt = cfg.get("format", "json")
        server_key = cfg.get("server_key", "mcpServers")
        for raw_path in cfg.get("config_paths", []):
            path = Path(raw_path)
            exists = path.is_file()
            config = W._read_mcp_config(path, fmt=fmt) if exists else {}
            servers = _servers_from_config(config, server_key)
            mcp_configs.append({
                "tool_id": tool_id,
                "name": cfg.get("name", tool_id),
                "path": str(path),
                "format": fmt,
                "server_key": server_key,
                "verified": bool(cfg.get("verified", False)),
                "parent_exists": path.parent.exists(),
                "exists": exists,
                "configured": "engram" in servers,
                "legacy_servers": [
                    name for name in W.LEGACY_SERVER_NAMES if name in servers
                ],
                "sha256_12": _file_sha256_12(path) if exists else "",
            })

    home = Path.home()
    instruction_files: list[dict] = []
    for tool_id, info in W._INSTRUCTION_SNIPPETS.items():
        path = Path(info["path_fn"](home))
        exists = path.is_file()
        content = ""
        if exists:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError):
                content = ""
        if tool_id == "cursor":
            has_marker = bool(content.strip()) and (
                "engram" in content.lower() or W._SNIPPET_FRESHNESS_TOKEN in content
            )
        else:
            has_marker = W._INSTRUCTION_MARKER in content or (
                "<!-- piia-engram:auto-injected -->" in content
            )
        instruction_files.append({
            "tool_id": tool_id,
            "path": str(path),
            "exists": exists,
            "has_marker": has_marker,
            "has_resume_brief": W._SNIPPET_FRESHNESS_TOKEN in content,
            "line_count": len(content.splitlines()) if content else 0,
            "sha256_12": _file_sha256_12(path) if exists else "",
        })

    shared_instruction_files: list[dict] = []
    for path in _shared_instruction_candidates(home):
        exists = path.is_file()
        line_count = 0
        if exists:
            try:
                line_count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            except (OSError, PermissionError):
                line_count = 0
        shared_instruction_files.append({
            "path": str(path),
            "exists": exists,
            "line_count": line_count,
            "sha256_12": _file_sha256_12(path) if exists else "",
        })

    claude_hooks = _claude_hook_rows(home)

    project_rules: list[dict] = []
    for rule in W._scan_rule_files(cwd=cwd):
        path = Path(rule["path"])
        lines = list(rule.get("lines", []))
        content_line_count = len([
            line for line in lines
            if line.strip() and not line.strip().startswith("#")
        ])
        project_rules.append({
            "path": str(path),
            "scope": rule.get("scope", ""),
            "line_count": len(lines),
            "content_line_count": content_line_count,
            "sha256_12": _file_sha256_12(path),
        })

    summary = {
        "mcp_config_paths": len(mcp_configs),
        "mcp_configs_found": sum(1 for row in mcp_configs if row["exists"]),
        "mcp_configs_configured": sum(1 for row in mcp_configs if row["configured"]),
        "instruction_files": len(instruction_files),
        "instruction_files_found": sum(1 for row in instruction_files if row["exists"]),
        "instruction_files_fresh": sum(
            1 for row in instruction_files
            if row["exists"] and row["has_resume_brief"]
        ),
        "project_rule_files": sum(1 for row in project_rules if row["scope"] == "project"),
        "legacy_server_configs": sum(1 for row in mcp_configs if row["legacy_servers"]),
        "shared_instruction_files_found": sum(
            1 for row in shared_instruction_files if row["exists"]
        ),
        "claude_hooks_registered": sum(1 for row in claude_hooks if row["registered"]),
        "claude_hooks_total": len(claude_hooks),
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "mcp_configs": mcp_configs,
        "instruction_files": instruction_files,
        "shared_instruction_files": shared_instruction_files,
        "claude_hooks": claude_hooks,
        "project_rules": project_rules,
        "live_store_modified": False,
    }


def _print_config_integrity_report(report: dict) -> None:
    W._safe_print("\n  -- Config Integrity --\n")
    summary = report.get("summary", {})
    mcp_total = int(summary.get("mcp_config_paths", 0) or 0)
    mcp_found = int(summary.get("mcp_configs_found", 0) or 0)
    mcp_configured = int(summary.get("mcp_configs_configured", 0) or 0)
    instruction_total = int(summary.get("instruction_files", 0) or 0)
    instruction_found = int(summary.get("instruction_files_found", 0) or 0)
    instruction_fresh = int(summary.get("instruction_files_fresh", 0) or 0)
    project_rules = int(summary.get("project_rule_files", 0) or 0)
    legacy_configs = int(summary.get("legacy_server_configs", 0) or 0)
    shared_found = int(summary.get("shared_instruction_files_found", 0) or 0)
    hooks_registered = int(summary.get("claude_hooks_registered", 0) or 0)
    hooks_total = int(summary.get("claude_hooks_total", 0) or 0)

    mcp_status = "[ok]" if legacy_configs == 0 else "[--]"
    print(
        f"    {mcp_status} MCP configs: "
        f"{mcp_found}/{mcp_total} files found, {mcp_configured} configured"
    )
    snippet_status = "[ok]" if instruction_found == instruction_fresh else "[--]"
    print(
        f"    {snippet_status} Instruction files: "
        f"{instruction_found}/{instruction_total} found, {instruction_fresh} fresh"
    )
    print(f"    [ok] Project rule files: {project_rules} found")
    print(f"    [ok] Shared instructions: {shared_found} found")
    hook_status = "[ok]" if hooks_registered == hooks_total else "[--]"
    print(f"    {hook_status} Claude hooks: {hooks_registered}/{hooks_total} registered")
    print("    [ok] Report is metadata-only (hashes + counts; no rule bodies)")


def _entry_args(entry: dict) -> list[str]:
    """Return MCP entry args as strings."""
    args = entry.get("args", [])
    if args is None:
        return []
    if not isinstance(args, list):
        return []
    return [str(arg) for arg in args]


def _classify_engram_entry(entry: dict) -> dict:
    """Classify an MCP ``engram`` entry and build a safe ``--help`` probe."""
    command = str(entry.get("command") or "").strip()
    raw_args = entry.get("args", [])
    args = _entry_args(entry)

    if not command:
        return {
            "severity": "error",
            "style": "invalid",
            "message": "MCP entry is missing command",
            "probe_argv": None,
        }
    if raw_args is not None and not isinstance(raw_args, list):
        return {
            "severity": "error",
            "style": "invalid",
            "message": "MCP entry args must be a list",
            "probe_argv": None,
        }

    if command == "uvx":
        if args[:3] == ["--from", "piia-engram", "piia-engram-mcp"]:
            return {
                "severity": "ok",
                "style": "recommended-uvx",
                "message": "Entry point style: recommended uvx zero-install",
                "probe_argv": [command, *args, "--help"],
            }
        return {
            "severity": "warn",
            "style": "uvx-other",
            "message": (
                "uvx entry should use: "
                "--from piia-engram piia-engram-mcp"
            ),
            "probe_argv": None,
        }

    if command == "piia-engram-mcp":
        return {
            "severity": "ok",
            "style": "recommended-console-script",
            "message": "Entry point style: recommended installed console script",
            "probe_argv": [command, *args, "--help"],
        }

    for index, arg in enumerate(args):
        if arg == "-m" and index + 1 < len(args):
            module_name = args[index + 1]
            if module_name == "piia_engram.mcp_server":
                return {
                    "severity": "ok",
                    "style": "compatible-python-module",
                    "message": "Entry point style: compatible python module",
                    "probe_argv": [command, *args, "--help"],
                }
            if "engram_core" in module_name:
                return {
                    "severity": "warn",
                    "style": "legacy-module",
                    "message": (
                        f"Uses old module name '{module_name}', use "
                        f"'{module_name.replace('engram_core', 'piia_engram')}'"
                    ),
                    "probe_argv": None,
                }

    if any(str(arg).endswith("mcp_server.py") for arg in args):
        return {
            "severity": "warn",
            "style": "legacy-script-path",
            "message": (
                "Uses direct mcp_server.py path; use "
                '["-m", "piia_engram.mcp_server"] or piia-engram-mcp'
            ),
            "probe_argv": None,
        }

    return {
        "severity": "warn",
        "style": "unknown",
        "message": (
            "Unknown MCP entry style; expected piia-engram-mcp, uvx, "
            "or python -m piia_engram.mcp_server"
        ),
        "probe_argv": None,
    }


def _probe_mcp_entry(entry: dict, *, timeout: int = 5) -> str | None:
    """Run a bounded ``--help`` probe for safe MCP entry shapes."""
    classification = _classify_engram_entry(entry)
    probe_argv = classification.get("probe_argv")
    if not probe_argv:
        return None
    try:
        result = subprocess.run(
            probe_argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"MCP launch probe timed out after {timeout}s"
    except Exception as exc:
        return f"MCP launch probe failed: {exc}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        suffix = f": {detail[0][:160]}" if detail else ""
        return f"MCP launch probe exited with code {result.returncode}{suffix}"
    return None


def _validate_engram_entry(servers: dict, config_path: Path) -> list[str]:
    """验证 engram MCP 条目的所有路径是否有效。

    Returns:
        问题描述列表（空 = 健康）。
    """
    issues = []
    engram = servers.get("engram", {})
    if not engram:
        return issues

    # Entry-point style + bounded launchability probe. The probe only runs
    # for shapes where `_classify_engram_entry` can build a safe `--help`.
    classification = _classify_engram_entry(engram)
    if classification["severity"] in ("warn", "error"):
        issues.append(classification["message"])
    if classification.get("probe_argv"):
        probe_issue = W._probe_mcp_entry(engram)
        if probe_issue:
            issues.append(probe_issue)

    # 1. Python 可执行路径
    python_exe = engram.get("command", "")
    non_path_commands = {"npx", "node", "uvx", "piia-engram-mcp"}
    if python_exe and python_exe not in non_path_commands:
        exe_path = Path(python_exe.replace("\\\\", "\\"))
        if not exe_path.is_file():
            issues.append(f"Python 路径不存在: {python_exe}")

    # 2. 服务端脚本/模块路径
    args = engram.get("args") or []
    uses_module_invocation = "-m" in args
    for arg in args:
        if arg.startswith("-"):
            # -m module.name — 不是文件路径，跳过
            continue
        p = Path(arg.replace("\\\\", "\\"))
        # 只验证看起来像路径的参数（含 / 或 \ 或 .py）
        if ("/" in arg or "\\" in arg or arg.endswith(".py")):
            if not p.is_file():
                issues.append(f"脚本路径不存在: {arg}")
            if not uses_module_invocation and arg.endswith(".py"):
                # Direct .py invocation causes ImportError on relative imports.
                issues.append(
                    f"使用直接 .py 路径调用 '{arg}'，会导致 ImportError。"
                    f"应改为 args: [\"-m\", \"piia_engram.mcp_server\"]"
                )

    # 3. 检查 -m 模块调用是否指向旧模块名
    for i, arg in enumerate(args):
        if arg == "-m" and i + 1 < len(args):
            module_name = args[i + 1]
            if "engram_core" in module_name:
                issues.append(
                    f"使用旧模块名 '{module_name}'，应改为 "
                    f"'{module_name.replace('engram_core', 'piia_engram')}'"
                )

    # 4. 环境变量中的路径
    env = engram.get("env", {})
    for key, val in env.items():
        if key in ("ENGRAM_DIR", "PYTHONPATH") and val:
            p = Path(val.replace("\\\\", "\\"))
            if not p.exists():
                issues.append(f"环境变量 {key} 路径不存在: {val}")

    return issues


def run_doctor(fix: bool = False) -> int:
    """扫描系统中所有已安装的 AI 工具，检查 Engram MCP 配置健康状况。

    流程：
    1. 扫描系统中装了哪些 AI 工具
    2. 检查哪些已配置 Engram、哪些还没配
    3. 验证已配置的条目路径是否有效（stale path 检测）
    4. 可选自动修复

    Args:
        fix: True 时自动修复发现的问题。

    Returns:
        发现的问题数量（0 = 健康）。
    """
    W._configure_utf8_stdio()
    print("\n========================================")
    print("  Engram Doctor - Config Health Check")
    print("========================================\n")

    # ── 第一步：扫描已安装的 AI 工具 ──
    tools = _detect_installed_tools()

    if not tools:
        print("  [!] No supported AI tools detected on this system.\n")
        print("  Evidence-tracked setup paths: Claude Code, Claude Desktop, Cursor, Codex")
        print("  Expected/community setup paths: Windsurf, Copilot, Cline, Roo Code, Amazon Q, Augment, Zed\n")
        return 0

    print(f"  Detected {len(tools)} AI tool(s):\n")

    verified_tools = [t for t in tools if t.get("verified")]
    community_tools = [t for t in tools if not t.get("verified")]
    configured_count = 0
    unconfigured: list[dict] = []

    if verified_tools:
        print("  Evidence-tracked setup paths:")
        for t in verified_tools:
            if t["status"] == "configured":
                W._safe_print(f"    [ok] {t['name']} — Engram configured")
                configured_count += 1
            else:
                W._safe_print(f"    [--] {t['name']} — Engram NOT configured")
                unconfigured.append(t)

    if community_tools:
        if verified_tools:
            print()
        print("  Expected/community setup paths:")
        for t in community_tools:
            if t["status"] == "configured":
                W._safe_print(f"    [ok] {t['name']} — Engram configured")
                configured_count += 1
            else:
                W._safe_print(f"    [--] {t['name']} — installed, Engram not configured")
                unconfigured.append(t)
    print()

    # ── 第二步：验证已配置的条目 ──
    issues: list[tuple[dict, str]] = []  # (tool_info, 描述)

    for t in tools:
        if t["status"] != "configured":
            continue
        servers = t["servers"]

        # 旧版 server 名称
        stale = [n for n in W.LEGACY_SERVER_NAMES if n in servers]
        if stale:
            issues.append((t, f"包含旧版 server: {', '.join(stale)}"))

        # 路径验证（核心：stale path 检测）
        path_issues = _validate_engram_entry(servers, t["config_path"])
        for desc in path_issues:
            issues.append((t, desc))

    # ── 第三步：报告 ──
    if unconfigured:
        print(f"  [info] {len(unconfigured)} tool(s) detected but Engram not configured:")
        for t in unconfigured:
            print(f"    - {t['name']} ({t['config_path']})")
        print("    Run 'engram setup' to configure them.\n")

    if not issues:
        if configured_count > 0:
            print("  [ok] All configured tools look healthy.\n")
        func_issues = _run_functional_checks(fix=fix)
        return func_issues

    print(f"  [!] Found {len(issues)} issue(s):\n")
    for t, desc in issues:
        print(f"  {t['name']} ({t['config_path']})")
        print(f"    -> {desc}")
    print()

    if not fix:
        print("  Run 'engram doctor --fix' to auto-repair.\n")
        return len(issues)

    # ── 第四步：自动修复 ──
    python_path = W._find_python()
    mcp_server_path = W._find_mcp_server()
    if not python_path or not mcp_server_path:
        print("  [error] Cannot auto-fix: Python 3.10+ or mcp_server.py not found.")
        print("          Run 'engram setup' to complete installation first.\n")
        return len(issues)

    fixed = 0
    file_safety_root = Path(os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    seen_paths: set[Path] = set()
    for t, _ in issues:
        path = t["config_path"]
        if path in seen_paths:
            continue
        seen_paths.add(path)
        try:
            W._write_tool_mcp_config(
                t,
                python_path,
                mcp_server_path,
                file_safety_root=file_safety_root,
                authorized_external_write=True,
            )
            print(f"  [fixed] {t['name']} ({path})")
            fixed += 1
        except Exception as exc:
            print(f"  [error] {t['name']} ({path}): {exc}")

    remaining = len(issues) - fixed
    print(f"\n  Done: {fixed} config(s) updated.")
    print(_t("  重启以下工具生效：", "  Restart the following tools to apply:"))
    W._print_restart_hints()
    print()
    func_issues = _run_functional_checks(fix=fix)
    return remaining + func_issues


def _run_governance_visibility_check(eng) -> int:
    try:
        from piia_engram import governance_runtime as gov_rt

        perms = gov_rt.describe_caller_permissions(eng.root)
    except Exception as exc:
        print(f"    [!!] Agent governance check failed: {exc}")
        return 1

    if not perms.get("governance_enabled"):
        print("    [--] Agent governance: off (unrestricted caller policy)")
        print(
            "         Set ENGRAM_GOVERNANCE=1 in each MCP client env for "
            "per-caller gates; recommended for multi-tool use."
        )
        print("         Add one of these snippets to each client's Engram env block:")
        print('           JSON: "ENGRAM_GOVERNANCE": "1", "ENGRAM_CLIENT_TYPE": "<client_type>"')
        print('           TOML: ENGRAM_GOVERNANCE = "1"')
        print('                 ENGRAM_CLIENT_TYPE = "<client_type>"')
        print(
            "         Common client_type values: claude_code, codex, cursor, "
            "windsurf. This is guidance only; configs are not written automatically."
        )
        return 0

    client_type = os.environ.get("ENGRAM_CLIENT_TYPE", "") or "(unset)"
    print(
        "    [ok] Agent governance: on "
        f"(client={client_type}, trust={perms.get('trust_level')}, "
        f"max={perms.get('max_sensitivity')}, write={perms.get('write_policy')})"
    )
    if perms.get("revoked"):
        print("         Current caller is revoked; future disclosures should be refused.")
    return 0


def _run_functional_checks(*, fix: bool = False) -> int:
    """运行功能性验证：MCP server 能否启动、知识库能否读写、quick_context 是否可用。

    Args:
        fix: If True, attempt to auto-repair issues (e.g. refresh stale quick_context.md).

    Returns:
        发现的问题数量（0 = 健康）。
    """
    W._safe_print("  -- Functional Checks --\n")
    problems = 0

    # 1. 核心库导入
    try:
        from piia_engram.core import Engram  # noqa: F811

        print("    [ok] piia_engram.core importable")
    except Exception as exc:
        print(f"    [!!] piia_engram.core import failed: {exc}")
        problems += 1
        return problems  # 后续检查都依赖 core

    # 2. Engram 实例化（读取 ~/.engram/）
    try:
        eng = Engram()
        print(f"    [ok] Engram initialized ({eng.root})")
    except Exception as exc:
        print(f"    [!!] Engram init failed: {exc}")
        problems += 1
        return problems

    # 3. 身份数据读取
    try:
        profile = eng.get_profile()
        role = profile.get("role", "")
        if role:
            print(f"    [ok] Identity loaded (role: {role})")
        else:
            print("    [--] Identity empty — run 'engram setup' to create your profile")
    except Exception as exc:
        print(f"    [!!] Profile read failed: {exc}")
        problems += 1

    # 3.5 setup 导入的规则可见性（让用户知道安装时导入了多少、去哪复核）
    try:
        # _update_access/_migrate_fields=False：doctor 读取知识库时一律只读，
        # 不计访问、不回写迁移旧知识文件（与 fix/非 fix 模式无关——这是统计读，
        # 不该有任何写副作用）。
        imported = eng.get_lessons(
            source_tool="engram_setup",
            limit=None,
            _update_access=False,
            _migrate_fields=False,
        )
        if imported:
            user_n = sum(
                1 for l in imported
                if "user_preference" in {
                    d.strip() for d in (l.get("domain") or "").split(",") if d.strip()
                }
            )
            project_n = sum(
                1 for l in imported
                if "project_rules" in {
                    d.strip() for d in (l.get("domain") or "").split(",") if d.strip()
                }
            )
            print(
                f"    [ok] {len(imported)} rule import(s) from setup "
                f"({user_n} user / {project_n} project) — run 'engram review' to refine"
            )
        else:
            print("    [--] No setup rule imports found — run 'engram setup' to ingest CLAUDE.md / AGENTS.md")
    except Exception as exc:
        print(f"    [!!] Imported-rules check failed: {exc}")
        problems += 1

    # 4. quick_context.md 可用性 + 过期检测
    qc = eng.root / "quick_context.md"
    if qc.exists() and qc.stat().st_size > 0:
        # Check staleness: compare mtime with identity/knowledge files
        qc_mtime = qc.stat().st_mtime
        stale = False
        source_dirs = [eng.root / "identity", eng.root / "knowledge"]
        for src_dir in source_dirs:
            if not src_dir.is_dir():
                continue
            for src_file in src_dir.iterdir():
                if src_file.is_file() and src_file.stat().st_mtime > qc_mtime:
                    stale = True
                    break
            if stale:
                break
        if stale:
            if fix:
                try:
                    eng.refresh_quick_context()
                    print(f"    [fixed] quick_context.md refreshed ({qc.stat().st_size} bytes)")
                except Exception as exc:
                    print(f"    [!!] quick_context.md refresh failed: {exc}")
                    problems += 1
            else:
                print("    [--] quick_context.md is stale (identity/knowledge updated since last generation)")
                print("         Run 'engram doctor --fix' to regenerate.")
        else:
            print(f"    [ok] quick_context.md ready ({qc.stat().st_size} bytes)")
    else:
        if fix:
            try:
                eng.refresh_quick_context()
                print(f"    [fixed] quick_context.md generated ({qc.stat().st_size} bytes)")
            except Exception as exc:
                print(f"    [!!] quick_context.md generation failed: {exc}")
                problems += 1
        else:
            print("    [--] quick_context.md missing or empty — cold-start will be slower")

    # 5. MCP server 工具注册
    try:
        from piia_engram import mcp_server  # noqa: F811

        tool_count = len(mcp_server.mcp._tool_manager._tools)
        print(f"    [ok] MCP server: {tool_count} tools registered")
        details = mcp_server._resolve_capability_mode_details(
            os.environ.get("ENGRAM_TOOLS", "")
        )
        raw_tools = str(details["raw"]) or "<unset>"
        modes = ", ".join(sorted(details["modes"]))  # type: ignore[arg-type]
        unknown = ", ".join(details["unknown_tokens"])  # type: ignore[arg-type]
        if unknown:
            print(
                "    [--] ENGRAM_TOOLS capability modes: "
                f"raw={raw_tools!r}, modes={modes}, "
                f"tools={len(details['tools'])}, ignored_unknown={unknown}"
            )
        else:
            print(
                "    [ok] ENGRAM_TOOLS capability modes: "
                f"raw={raw_tools!r}, modes={modes}, "
                f"tools={len(details['tools'])}, ignored_unknown=none"
            )
    except Exception as exc:
        print(f"    [!!] MCP server import failed: {exc}")
        problems += 1

    problems += _run_governance_visibility_check(eng)

    problems += W._run_terminal_encoding_check()

    try:
        _print_config_integrity_report(_build_config_integrity_report(cwd=Path.cwd()))
    except Exception as exc:
        print(f"    [!!] Config integrity check failed: {exc}")
        problems += 1

    problems += W._run_continuity_checks(eng)

    # 6. AI instruction snippet injection status
    # v3.31 P0: doctor now checks BOTH presence AND freshness. A snippet
    # that lacks _SNIPPET_FRESHNESS_TOKEN ("get_resume_brief") was injected
    # by v3.30 or earlier and is missing the cross-tool resume directive;
    # doctor --fix overwrites it with the current snippet.
    print()
    W._safe_print("  -- AI Instruction Snippets --\n")
    home = Path.home()
    snippet_found = False
    missing_snippets: list[str] = []  # path missing OR file lacks snippet
    stale_snippets: list[str] = []    # snippet present but missing freshness token
    for tool_id, info in W._INSTRUCTION_SNIPPETS.items():
        target_path = info["path_fn"](home)
        if not target_path.is_file():
            W._safe_print(f"    [--] {tool_id}: no instruction file")
            missing_snippets.append(tool_id)
            continue
        try:
            content = target_path.read_text(encoding="utf-8")
        except Exception:
            W._safe_print(f"    [--] {tool_id}: file exists but unreadable")
            continue

        # Cursor mdc is entirely ours; everything else uses the marker.
        if tool_id == "cursor":
            present = bool(content.strip())
        else:
            present = W._INSTRUCTION_MARKER in content or (
                # back-compat: also match v=1 marker before bump
                "<!-- piia-engram:auto-injected -->" in content
            )

        if not present:
            W._safe_print(f"    [--] {tool_id}: file exists but no Engram snippet")
            missing_snippets.append(tool_id)
            continue

        if W._SNIPPET_FRESHNESS_TOKEN not in content:
            W._safe_print(
                f"    [stale] {tool_id}: snippet missing "
                f"'{W._SNIPPET_FRESHNESS_TOKEN}' directive (pre-v3.31)"
            )
            stale_snippets.append(tool_id)
        else:
            W._safe_print(f"    [ok] {tool_id}: snippet up to date in {target_path}")
            snippet_found = True

    refresh_targets = missing_snippets + stale_snippets

    if refresh_targets and fix:
        # Detect language from existing identity
        lang = "zh"
        try:
            profile = eng.get_profile()
            pref_lang = profile.get("language", "")
            if pref_lang and "en" in pref_lang.lower():
                lang = "en"
        except Exception:
            pass
        fixed_snippets = []
        for tool_id in refresh_targets:
            result = W._inject_instruction_snippet(
                tool_id,
                lang=lang,
                file_safety_root=eng.root,
                authorized_external_write=True,
            )
            if result:
                action = "refreshed" if tool_id in stale_snippets else "fixed"
                fixed_snippets.append(f"[{action}] {tool_id}: {result}")
        if fixed_snippets:
            print()
            for s in fixed_snippets:
                W._safe_print(f"    {s}")
    elif refresh_targets and not fix:
        print()
        if stale_snippets:
            print(
                "    [info] Stale snippets detected — missing the "
                "cross-tool resume directive."
            )
        else:
            print("    [info] Missing AI instruction snippets.")
        print("           Run 'engram doctor --fix' to update them.")
        print("           Without the latest snippet, AI may not auto-resume across tools.")
    elif not snippet_found:
        print()
        print("    [info] No AI instruction snippets found.")
        print("           Run 'engram setup' to inject them.")

    # ── Claude Code Hooks (Stop / PreCompact / SessionStart) ──
    # v3.30 M7: doctor must check all three events the setup wizard
    # registers, not only Stop. Otherwise users who upgrade from
    # v3.29.x and run ``engram doctor --fix`` end up missing PreCompact
    # (mechanism 4) and SessionStart (mechanism 6) silently.
    print()
    W._safe_print("  -- Claude Code Hooks --\n")
    settings_path = Path.home() / ".claude" / "settings.json"
    settings: dict = {}
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            settings = {}

    hook_specs = (
        # (event, human label, markers, match_mode, injector)
        # match_mode: "any" = any marker hit → ok (Stop, SessionStart)
        #             "all" = ALL markers must hit → ok (PreCompact)
        (
            "Stop",
            "Stop",
            ("auto_save_on_stop", "piia_engram.hooks.auto_save_on_stop"),
            "any",
            W._inject_claude_code_hook,
        ),
        (
            "PreCompact",
            "PreCompact",
            # M3 fix: require BOTH the module AND the env marker.
            # A hook with just the module name (no CLAUDE_INVOKED_BY=
            # engram_precompact) is a misconfigured Stop-style hook that
            # would use the wrong flush threshold.
            ("CLAUDE_INVOKED_BY=engram_precompact",
             "piia_engram.hooks.auto_save_on_stop"),
            "all",
            W._inject_claude_code_precompact_hook,
        ),
        (
            "SessionStart",
            "SessionStart (resume brief inject)",
            ("auto_inject_resume_brief",
             "piia_engram.hooks.auto_inject_resume_brief"),
            "any",
            W._inject_claude_code_sessionstart_hook,
        ),
        (
            "PostCompact",
            "PostCompact (summary absorb)",
            ("auto_absorb_compact",
             "piia_engram.hooks.auto_absorb_compact"),
            "any",
            W._inject_claude_code_postcompact_hook,
        ),
    )

    python_path_for_fix: str | None = None
    for event, label, markers, match_mode, injector in hook_specs:
        found = False
        matcher = all if match_mode == "all" else any
        for event_group in settings.get("hooks", {}).get(event, []):
            for hook in event_group.get("hooks", []):
                cmd = hook.get("command", "")
                if matcher(m in cmd for m in markers):
                    found = True
                    break
            if found:
                break

        if found:
            W._safe_print(f"    [ok] {label} hook registered")
            continue

        W._safe_print(f"    [--] No Engram {label} hook in Claude Code settings")
        if fix:
            if python_path_for_fix is None:
                python_path_for_fix = W._find_python() or ""
            if python_path_for_fix:
                # v3.30.1: pass force_rewrite=True so doctor --fix can
                # upgrade stale hooks (e.g. script-path style → -m form,
                # or hooks whose env markers no longer satisfy doctor's
                # strict-match check). Without force_rewrite, idempotent
                # skip would let "PreCompact present but stale" survive.
                result = injector(
                    python_path_for_fix,
                    force_rewrite=True,
                    file_safety_root=eng.root,
                    authorized_external_write=True,
                )
                if result:
                    W._safe_print(f"    [fixed] {label} hook registered: {result}")
                else:
                    W._safe_print(
                        f"    [info] {label} hook already up to date"
                    )
            else:
                W._safe_print("    [info] Cannot fix: Python not found")
        else:
            print(
                f"           Run 'engram doctor --fix' or 'engram setup' "
                f"to register the {label} hook."
            )

    print()
    W._safe_print("  -- Encoding health --\n")
    try:
        from piia_engram.encoding_repair import repair_engram_root, scan_engram_root

        if fix:
            repair_report = repair_engram_root(eng.root, apply=True)
            if repair_report.repairable_count:
                W._safe_print(
                    "    [fixed] Encoding health: repaired "
                    f"{repair_report.repairable_count} text field(s) in "
                    f"{len(repair_report.changed_files)} file(s)"
                )
                if repair_report.backup_dir is not None:
                    W._safe_print(f"           Backup: {repair_report.backup_dir}")
            if repair_report.suspect_count:
                W._safe_print(
                    "    [!!] Encoding health: "
                    f"{repair_report.suspect_count} suspect mojibake field(s) "
                    "need manual review"
                )
                problems += 1
            elif not repair_report.repairable_count:
                print("    [ok] Encoding health: no mojibake detected")
        else:
            scan_report = scan_engram_root(eng.root)
            if scan_report.repairable_count or scan_report.suspect_count:
                W._safe_print(
                    "    [!!] Encoding health: found "
                    f"{scan_report.repairable_count} repairable mojibake field(s) "
                    f"and {scan_report.suspect_count} suspect mojibake field(s)"
                )
                for finding in scan_report.findings[:3]:
                    W._safe_print(f"         - {finding.relative_path}:{finding.json_path}")
                print("           Run 'engram doctor --fix' or 'engram repair-encoding --apply'.")
                problems += 1
            else:
                print("    [ok] Encoding health: no mojibake detected")
    except Exception as exc:
        print(f"    [!!] Encoding health check failed: {exc}")
        problems += 1

    # ── Search mode (informational; never affects exit code) ──────────────
    try:
        print()
        mode = os.environ.get("ENGRAM_SEARCH", "keyword").strip().lower()
        if mode == "hybrid":
            try:
                import fastembed  # noqa: F401
                import sqlite_vec  # noqa: F401

                W._safe_print(
                    "    [ok] Search mode: hybrid (keyword + FTS + semantic vector)"
                )
            except ImportError:
                W._safe_print(
                    "    [!] Search mode: hybrid requested but vector deps missing "
                    "— falls back to keyword+FTS only. "
                    "Install: pip install 'piia-engram[vector]'"
                )
        else:
            W._safe_print(
                "    [--] Search mode: keyword (default stays keyword; hybrid is "
                "opt-in). Better cross-lingual recall available: set "
                "ENGRAM_SEARCH=hybrid and run 'engram reindex'. The vector layer "
                "is optional via pip install 'piia-engram[vector]' "
                "(see docs/hybrid-search.md)"
            )
    except Exception as exc:
        W._safe_print(f"    [--] Search mode check skipped: {exc}")

    # ── Version freshness (read-only PyPI check; never affects exit code) ──
    try:
        from piia_engram import __version__
        from piia_engram.update_check import check_for_update, is_disabled

        print()
        if is_disabled():
            W._safe_print(
                f"    [--] Version: {__version__} (update check disabled)"
            )
        else:
            latest = check_for_update(__version__, force=True)
            if latest:
                W._safe_print(
                    f"    [!] Version: {__version__} — newer release {latest} "
                    "available; upgrade: pip install -U piia-engram"
                )
            else:
                W._safe_print(f"    [ok] Version: {__version__} (up to date)")
    except Exception as exc:
        W._safe_print(f"    [--] Version check skipped: {exc}")

    # 8. Store footprint (warn-only: never counts as a problem, --fix maintains)
    try:
        from piia_engram.store_health import apply_store_maintenance, store_footprint

        footprint = store_footprint(eng.root)
        mb = 1024 * 1024
        print()
        W._safe_print(
            f"    [ok] Store footprint: total {footprint['total_bytes'] // mb}MB "
            f"(backups {footprint['backups_bytes'] // mb}MB, "
            f"logs {(footprint['ledger_bytes'] + footprint['audit_bytes']) // mb}MB)"
        )
        for warning in footprint["warnings"]:
            W._safe_print(f"    [--] {warning}")
        if fix and footprint["warnings"]:
            for action in apply_store_maintenance(eng.root):
                W._safe_print(f"    [fixed] {action}")
    except Exception as exc:
        W._safe_print(f"    [--] Store footprint check skipped: {exc}")

    # 9. Activation status (warn-only, derived from existing markers)
    try:
        from piia_engram.store_health import activation_status

        act = activation_status(eng.root)
        if act["has_memory"]:
            W._safe_print("    [ok] Activation: memory present; next session will recall it")
        else:
            W._safe_print(
                "    [--] Activation: no durable memory yet — save one lesson to activate"
            )
    except Exception as exc:
        W._safe_print(f"    [--] Activation check skipped: {exc}")

    print()
    return problems
