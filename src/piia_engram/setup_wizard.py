"""Engram 安装向导 — engram setup / engram doctor 命令入口。"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# 旧版 MCP server 名称，迁移时需要清理
LEGACY_SERVER_NAMES = ["piia-pkc", "piia_pkc", "piia-pkc-mcp"]

# ---------------------------------------------------------------------------
# i18n — 双语支持（中文/English）
# ---------------------------------------------------------------------------

_lang = "zh"  # 默认中文，setup 开始时由用户选择


def _t(zh: str, en: str) -> str:
    """根据当前语言返回对应文案。"""
    return zh if _lang == "zh" else en


def _safe_print(text: str) -> None:
    """Print with fallback for consoles that can't handle certain Unicode chars (e.g. Windows GBK)."""
    try:
        print(text)
    except UnicodeEncodeError:
        # Strip chars the console encoding can't handle
        import sys
        enc = sys.stdout.encoding or "ascii"
        safe = text.encode(enc, errors="ignore").decode(enc)
        print(safe)

# ---------------------------------------------------------------------------
# 智能扫描 + 分流导入
# ---------------------------------------------------------------------------

# 用户身份类关键词
_USER_KEYWORDS = re.compile(
    r"(语言|language|中文|english|角色|role|我是|i am|偏好|prefer|always|never"
    r"|禁止|必须|风格|style|tone|沟通|communicate|所有沟通|技术水平|technical.?level"
    r"|工作方式|work.?style|习惯|habit)",
    re.IGNORECASE,
)

# 项目规则类关键词
_PROJECT_KEYWORDS = re.compile(
    r"(这个.?repo|this.?repo|测试|test|build|deploy|ci/cd|ci |cd "
    r"|pre.?commit|hook|lint|tailwind|webpack|vite|docker|makefile"
    r"|\.env|package\.json|tsconfig|eslint|prettier|migration"
    r"|数据库|database|schema|endpoint|路由|route|api)",
    re.IGNORECASE,
)


def _scan_rule_files(cwd: Path | None = None) -> list[dict]:
    """扫描全局和项目级规则文件，返回 [{path, scope, lines}]。

    scope: "global" (全局文件，倾向用户身份) / "project" (项目文件，倾向项目规则)
    """
    home = Path.home()
    current_dir = cwd or Path.cwd()
    found: list[dict] = []

    # 全局文件
    global_candidates = [
        home / ".claude" / "CLAUDE.md",
    ]
    # Cursor 全局规则目录
    cursor_rules_dir = home / ".cursor" / "rules"
    if cursor_rules_dir.is_dir():
        global_candidates.extend(sorted(cursor_rules_dir.glob("*.mdc"))[:5])

    # Claude Code 项目级指令（全局目录下的各项目）
    claude_projects = home / ".claude" / "projects"
    if claude_projects.is_dir():
        for proj_claude in sorted(claude_projects.glob("*/CLAUDE.md"))[:10]:
            global_candidates.append(proj_claude)

    for path in global_candidates:
        entry = _read_rule_file(path, "global")
        if entry:
            found.append(entry)

    # 项目文件（CWD）
    project_candidates = [
        current_dir / "CLAUDE.md",
        current_dir / ".cursorrules",
        current_dir / "AGENTS.md",
        current_dir / ".github" / "copilot-instructions.md",
    ]
    for path in project_candidates:
        entry = _read_rule_file(path, "project")
        if entry:
            found.append(entry)

    return found


def _read_rule_file(path: Path, scope: str) -> dict | None:
    """读取单个规则文件，返回 {path, scope, lines} 或 None。"""
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return None

    lines = text.splitlines()[:200]  # 最多 200 行
    content_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
    if len(content_lines) < 2:
        return None  # 太少，跳过

    return {"path": path, "scope": scope, "lines": lines}


def _classify_line(line: str, scope: str) -> str:
    """将一行内容分类为 "user" / "project" / "skip"。

    Args:
        line: 文本行
        scope: "global" (全局文件) / "project" (项目文件)
    """
    stripped = line.strip()

    # 跳过：空行、纯标记、过短
    if not stripped or stripped.startswith("#") or stripped.startswith("---"):
        return "skip"
    # CJK characters carry more information per char than ASCII
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in stripped)
    min_len = 4 if has_cjk else 8
    if len(stripped) < min_len:
        return "skip"
    # 跳过 frontmatter / code fences
    if stripped.startswith("```") or stripped.startswith("<!--"):
        return "skip"

    has_user = bool(_USER_KEYWORDS.search(stripped))
    has_project = bool(_PROJECT_KEYWORDS.search(stripped))

    if has_user and not has_project:
        return "user"
    if has_project and not has_user:
        return "project"
    if has_user and has_project:
        # 歧义：看文件来源
        return "user" if scope == "global" else "project"

    # 无关键词命中：看来源文件默认倾向
    return "user" if scope == "global" else "project"


def _import_with_split(
    rule_files: list[dict],
    engram,
) -> dict:
    """将扫描到的规则文件按分流规则导入 Engram。

    Returns: {user_count, project_count, skipped, files}
    """
    user_rules: list[str] = []
    project_rules: list[str] = []
    skipped = 0

    for rf in rule_files:
        scope = rf["scope"]
        for line in rf["lines"]:
            category = _classify_line(line, scope)
            if category == "user":
                user_rules.append(line.strip())
            elif category == "project":
                project_rules.append(line.strip())
            else:
                skipped += 1

    # 写入用户偏好
    if user_rules:
        # 提取特定偏好
        prefs_update: dict = {}
        remaining_user: list[str] = []

        for rule in user_rules:
            rule_lower = rule.lower()
            if any(kw in rule_lower for kw in ["语言", "language", "中文", "english", "沟通"]):
                # 语言偏好 → profile
                if "中文" in rule:
                    prefs_update["language"] = "中文"
                elif "english" in rule_lower:
                    prefs_update["language"] = "English"
                remaining_user.append(rule)
            elif any(kw in rule_lower for kw in ["角色", "role", "我是", "i am"]):
                remaining_user.append(rule)
            else:
                remaining_user.append(rule)

        if prefs_update:
            engram.update_profile(prefs_update)

        # 剩余用户规则存为 lesson（domain=user_preference）
        for rule in remaining_user:
            engram.add_lesson(rule, domain="user_preference", source_tool="engram_setup")

    # 写入项目规则
    for rule in project_rules:
        engram.add_lesson(rule, domain="project_rules", source_tool="engram_setup")

    return {
        "user_count": len(user_rules),
        "project_count": len(project_rules),
        "skipped": skipped,
        "files": [str(rf["path"]) for rf in rule_files],
    }

# ---------------------------------------------------------------------------
# 工具检测配置
# ---------------------------------------------------------------------------

def _tool_configs() -> dict:
    """返回各工具的 MCP 配置路径（运行时构建，确保 Path.home() 正确）。

    每个条目包含:
    - name: 工具显示名
    - config_paths: 配置文件路径列表
    - format: "json" | "toml"（默认 json）
    - verified: True = 团队实测验证过, False = 社区级支持（路径来自官方文档，未实测）
    - server_key: MCP servers 在配置中的顶层 key（默认 "mcpServers"）
    """
    home = Path.home()
    is_mac = platform.system() == "Darwin"
    is_win = platform.system() == "Windows"
    appdata = Path(os.environ.get("APPDATA", "")) if is_win else None
    vscode_storage = (appdata / "Code" / "User") if appdata else (
        home / "Library" / "Application Support" / "Code" / "User" if is_mac
        else home / ".config" / "Code" / "User"
    )

    configs: dict = {
        # ── 已验证（团队实测） ─────────────────────────────
        "claude_code": {
            "name": "Claude Code",
            "config_paths": [home / ".claude" / ".mcp.json"],
            "verified": True,
        },
        "cursor": {
            "name": "Cursor",
            "config_paths": [home / ".cursor" / "mcp.json"],
            "verified": True,
        },
        "claude_desktop": {
            "name": "Claude Desktop",
            "config_paths": (
                [home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"]
                if is_mac
                else [appdata / "Claude" / "claude_desktop_config.json"]
                if appdata
                else []
            ),
            "verified": True,
        },
        "codex": {
            "name": "Codex",
            "config_paths": [home / ".codex" / "config.toml"],
            "format": "toml",
            "server_key": "mcp_servers",
            "verified": True,
        },

        # ── 社区级支持（路径来自官方文档，未实测） ──────────
        "windsurf": {
            "name": "Windsurf",
            "config_paths": [home / ".codeium" / "windsurf" / "mcp_config.json"],
            "verified": False,
        },
        "copilot_vscode": {
            "name": "GitHub Copilot (VS Code)",
            "config_paths": [vscode_storage / "mcp.json"] if vscode_storage else [],
            "server_key": "servers",
            "verified": False,
        },
        "cline": {
            "name": "Cline",
            "config_paths": [
                vscode_storage / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json",
            ] if vscode_storage else [],
            "verified": False,
        },
        "roo_code": {
            "name": "Roo Code",
            "config_paths": [
                vscode_storage / "globalStorage" / "rooveterinaryinc.roo-cline" / "settings" / "cline_mcp_settings.json",
            ] if vscode_storage else [],
            "verified": False,
        },
        "amazon_q": {
            "name": "Amazon Q Developer",
            "config_paths": [home / ".aws" / "amazonq" / "mcp.json"],
            "verified": False,
        },
        "augment": {
            "name": "Augment Code",
            "config_paths": [home / ".augment" / "settings.json"],
            "verified": False,
        },
        "zed": {
            "name": "Zed",
            "config_paths": (
                [home / ".config" / "zed" / "settings.json"] if not is_win
                else [appdata / "Zed" / "settings.json"] if appdata
                else []
            ),
            "server_key": "context_servers",
            "verified": False,
        },
    }
    return configs


# ---------------------------------------------------------------------------
# 辅助函数（可单独测试）
# ---------------------------------------------------------------------------

def _find_python() -> str | None:
    """找到可用的 Python 3.10+ 可执行路径。优先用当前 Python。"""
    candidates = [
        sys.executable,
        shutil.which("python3"),
        shutil.which("python"),
        "/opt/homebrew/bin/python3",    # Mac Apple Silicon Homebrew
        "/usr/local/bin/python3",        # Mac Intel Homebrew
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = subprocess.run(
                [
                    candidate, "-c",
                    "import sys; assert sys.version_info >= (3, 10); print(sys.executable)",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            continue
    return None


def _find_mcp_server() -> str | None:
    """找到已安装的 mcp_server.py 绝对路径。"""
    spec = importlib.util.find_spec("piia_engram")
    if spec and spec.origin:
        path = Path(spec.origin).parent / "mcp_server.py"
        if path.is_file():
            return str(path)
    return None


def _detect_tools() -> list[dict]:
    """检测已安装的 AI 工具，返回可配置的工具列表。"""
    detected = []
    for tool_id, cfg in _tool_configs().items():
        for config_path in cfg["config_paths"]:
            # 配置文件已存在，或父目录存在（工具已装但未配置 MCP）
            if config_path.exists() or config_path.parent.exists():
                detected.append(
                    {
                        "id": tool_id,
                        "name": cfg["name"],
                        "config_path": config_path,
                    }
                )
                break
    return detected


def _read_mcp_config(config_path: Path, fmt: str = "json") -> dict:
    """读取现有 MCP 配置，不存在或解析失败时返回空结构。

    Args:
        config_path: 配置文件路径。
        fmt: "json" 或 "toml"。
    """
    if not config_path.is_file():
        return {}
    try:
        raw = config_path.read_text(encoding="utf-8")
        if fmt == "toml":
            return _parse_toml(raw)
        return json.loads(raw)
    except Exception as exc:
        logger.warning("read config failed (%s): %s", config_path, exc)
    return {}


def _parse_toml(text: str) -> dict:
    """解析 TOML 文本，兼容 Python 3.10（无 tomllib）。"""
    try:
        import tomllib  # Python 3.11+
        return tomllib.loads(text)
    except ImportError:
        pass
    try:
        import tomli  # third-party fallback
        return tomli.loads(text)
    except ImportError:
        pass
    # 最后手段：只提取 [mcp_servers.*] 段（覆盖 doctor 的核心需求）
    return _parse_toml_mcp_minimal(text)


def _parse_toml_mcp_minimal(text: str) -> dict:
    """从 TOML 文本中最小化提取 mcp_servers 配置。

    只处理 doctor 需要的字段：command, args, env。
    不是通用 TOML 解析器，仅用于 Python 3.10 无 tomllib 的回退。
    """
    import re as _re
    servers: dict = {}
    current_server: str | None = None
    current_section: str | None = None  # "root" | "env"

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # [mcp_servers.NAME]
        m = _re.match(r'^\[mcp_servers\.([a-zA-Z0-9_-]+)\]$', line)
        if m:
            current_server = m.group(1)
            current_section = "root"
            servers[current_server] = {"command": "", "args": [], "env": {}}
            continue

        # [mcp_servers.NAME.env]
        m = _re.match(r'^\[mcp_servers\.([a-zA-Z0-9_-]+)\.env\]$', line)
        if m:
            current_server = m.group(1)
            current_section = "env"
            if current_server not in servers:
                servers[current_server] = {"command": "", "args": [], "env": {}}
            continue

        # Other section header → stop tracking current server
        if line.startswith("["):
            current_server = None
            current_section = None
            continue

        if not current_server:
            continue

        # key = value
        m = _re.match(r'^(\w+)\s*=\s*(.+)$', line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()

        # Unquote string values
        if (val.startswith('"') and val.endswith('"')) or \
           (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]

        if current_section == "env":
            servers[current_server]["env"][key] = val
        elif key == "command":
            servers[current_server]["command"] = val
        elif key == "args":
            # Parse simple TOML array: ["a", "b"]
            arr_m = _re.findall(r'"([^"]*)"', val)
            servers[current_server]["args"] = arr_m

    return {"mcp_servers": servers}


def _write_mcp_config(
    config_path: Path,
    python_path: str,
    mcp_server_path: str,
    data_dir: str | None = None,
) -> None:
    """将 engram 写入指定工具的 MCP 配置（合并，不覆盖其他工具的配置）。
    同时自动清理已知的旧版 server 名称（piia-pkc 等）。
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = _read_mcp_config(config_path)

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    # 清理旧版 server 名称
    removed = [name for name in LEGACY_SERVER_NAMES if name in config["mcpServers"]]
    for name in removed:
        del config["mcpServers"][name]
    if removed:
        print(f"  [migrated] removed legacy server(s): {', '.join(removed)}")

    entry: dict = {
        "command": python_path,
        "args": [mcp_server_path],
    }
    if data_dir:
        entry["env"] = {"ENGRAM_DIR": data_dir}

    config["mcpServers"]["engram"] = entry

    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_mcp_config_toml(
    config_path: Path,
    python_path: str,
    mcp_server_path: str,
) -> None:
    """修复 TOML 格式配置文件中的 engram MCP 条目（如 Codex config.toml）。

    策略：原地替换 [mcp_servers.engram] 段，保留文件其余内容不动。
    """
    if not config_path.is_file():
        return

    lines = config_path.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    skip_until_next_section = False

    # 构建新的 engram 条目
    # 优先用 -m 模块调用（不依赖绝对路径）
    engram_block = [
        '[mcp_servers.engram]',
        f'command = "{python_path}"',
        f'args = ["-m", "piia_engram.mcp_server"]',
    ]
    # 如果 mcp_server_path 不是通过 -m 可达的（不在 site-packages），加 PYTHONPATH
    spec = importlib.util.find_spec("piia_engram")
    if not spec:
        # piia_engram 不在默认路径，需要 PYTHONPATH
        src_dir = str(Path(mcp_server_path).parent.parent)
        engram_block.append('')
        engram_block.append('[mcp_servers.engram.env]')
        engram_block.append(f'PYTHONPATH = "{src_dir}"')

    inserted = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 检测 [mcp_servers.engram] 段开始
        if stripped == '[mcp_servers.engram]':
            skip_until_next_section = True
            if not inserted:
                new_lines.extend(engram_block)
                inserted = True
            i += 1
            continue

        # 检测 [mcp_servers.engram.env] 子段（也要跳过）
        if stripped == '[mcp_servers.engram.env]':
            skip_until_next_section = True
            i += 1
            continue

        # 遇到其他段头，结束跳过
        if stripped.startswith('[') and skip_until_next_section:
            skip_until_next_section = False

        if not skip_until_next_section:
            new_lines.append(line)

        i += 1

    # 如果原文件没有 engram 段，追加到末尾
    if not inserted:
        new_lines.append('')
        new_lines.extend(engram_block)

    config_path.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')


# ---------------------------------------------------------------------------
# 向导交互
# ---------------------------------------------------------------------------

def _prompt(message: str, default: str = "") -> str:
    """带默认值的输入提示。Ctrl+C 或 EOF 时退出。"""
    display = f"{message} [{default}]: " if default else f"{message}: "
    try:
        value = input(display).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return value if value else default


def _yn(message: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = _prompt(f"{message} [{hint}]").lower()
    if not answer:
        return default
    return answer.startswith("y")


def _choice(message: str, options: list[str], allow_custom: bool = True) -> str:
    """数字菜单选择。支持自定义输入。

    Args:
        message: 提示语
        options: 预设选项列表
        allow_custom: 是否允许自定义输入

    Returns: 选中的选项文本，或空字符串（跳过）
    """
    print(f"  {message}")
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    if allow_custom:
        print(f"    {len(options) + 1}. 其他（自行输入）")
    print(f"    0. 跳过")

    answer = _prompt("  请选择").strip()
    if not answer or answer == "0":
        return ""

    try:
        idx = int(answer)
        if 1 <= idx <= len(options):
            return options[idx - 1]
        if allow_custom and idx == len(options) + 1:
            return _prompt("  请输入")
    except ValueError:
        # 直接输入了文本而非数字，也接受
        return answer

    return ""


def _configure_utf8_stdio() -> None:
    """Prefer UTF-8 output so Windows setup can print Chinese and status icons."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not reconfigure:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (TypeError, ValueError, OSError):
            pass


def _get_engram_class():
    """Import Engram from package or local script execution context."""
    try:
        from piia_engram.core import Engram
    except ImportError:  # pragma: no cover - direct script fallback
        from core import Engram  # type: ignore
    return Engram


# ---------------------------------------------------------------------------
# Cold-start: environment probing
# ---------------------------------------------------------------------------

def _probe_environment(cwd: Path | None = None) -> dict:
    """Silently extract identity signals from the user's dev environment.

    Returns a dict with discovered signals:
      name, email, language_hint, tech_stack_hint, commit_style
    All fields are optional — any failure is silently ignored.
    """
    signals: dict = {}
    current_dir = cwd or Path.cwd()

    # 1. Git config → name, email
    for key, field in [("user.name", "name"), ("user.email", "email")]:
        try:
            r = subprocess.run(
                ["git", "config", "--global", key],
                capture_output=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                signals[field] = r.stdout.strip().decode("utf-8", errors="replace")
        except Exception:
            pass

    # 2. Git log → language hint, commit style
    try:
        r = subprocess.run(
            ["git", "log", "--format=%s", "-50"],
            capture_output=True, timeout=5,
            cwd=str(current_dir),
        )
        if r.returncode == 0 and r.stdout.strip():
            msgs = r.stdout.strip().decode("utf-8", errors="replace").splitlines()
            # Language detection: if >40% messages contain CJK → zh
            cjk_count = sum(1 for m in msgs if any('\u4e00' <= c <= '\u9fff' for c in m))
            if msgs and cjk_count / len(msgs) > 0.4:
                signals["language_hint"] = "中文"
            # Conventional commits detection
            conventional = sum(1 for m in msgs if re.match(r'^(feat|fix|chore|docs|refactor|test|ci|build)[:(]', m))
            if msgs and conventional / len(msgs) > 0.3:
                signals["commit_style"] = "conventional commits"
    except Exception:
        pass

    # 3. Project file detection → tech stack hint
    tech_hints: list[str] = []
    detectors = [
        ("pyproject.toml", "Python"), ("setup.py", "Python"),
        ("requirements.txt", "Python"), ("Pipfile", "Python"),
        ("package.json", "TypeScript / JavaScript"),
        ("tsconfig.json", "TypeScript"),
        ("Cargo.toml", "Rust"), ("go.mod", "Go"),
        ("pom.xml", "Java"), ("build.gradle", "Java"),
        ("Gemfile", "Ruby"),
    ]
    for filename, tech in detectors:
        if (current_dir / filename).exists() and tech not in tech_hints:
            tech_hints.append(tech)
    if tech_hints:
        signals["tech_stack_hint"] = " + ".join(tech_hints[:3])

    return signals


# ---------------------------------------------------------------------------
# Cold-start: seed templates
# ---------------------------------------------------------------------------

_SEED_TEMPLATES: dict[str, list[dict]] = {
    "Python": [
        {"summary": "Pin dependency versions in pyproject.toml or requirements.txt", "domain": "python"},
        {"summary": "Use virtual environments (venv/conda) to isolate project dependencies", "domain": "python"},
    ],
    "TypeScript / JavaScript": [
        {"summary": "Enable strict mode in tsconfig.json for better type safety", "domain": "javascript"},
        {"summary": "Prefer named exports over default exports for refactoring ease", "domain": "javascript"},
    ],
    "TypeScript": [
        {"summary": "Enable strict mode in tsconfig.json for better type safety", "domain": "javascript"},
    ],
    "Go": [
        {"summary": "Always check returned errors — never use blank identifier for errors", "domain": "go"},
    ],
    "Rust": [
        {"summary": "Prefer Result over unwrap in production code paths", "domain": "rust"},
    ],
    "Java": [
        {"summary": "Use try-with-resources for auto-closeable objects", "domain": "java"},
    ],
    "_universal": [
        {"summary": "Write commit messages explaining why, not just what", "domain": "git"},
        {"summary": "Add comments for non-obvious business logic, not for self-documenting code", "domain": "best-practices"},
        {"summary": "Test edge cases, not just happy paths", "domain": "testing"},
    ],
}


def _apply_seed_templates(engram, tech_stack: str) -> int:
    """Inject starter lessons based on detected tech stack. Returns count added."""
    templates: list[dict] = list(_SEED_TEMPLATES.get("_universal", []))

    # Match tech stack keywords
    for key, items in _SEED_TEMPLATES.items():
        if key.startswith("_"):
            continue
        if key.lower() in tech_stack.lower():
            templates.extend(items)

    added = 0
    for t in templates:
        try:
            result = engram.add_lesson(
                t["summary"], domain=t["domain"],
                source_tool="engram_setup", tier="staging",
            )
            if result.get("status") != "duplicate":
                added += 1
        except Exception:
            pass
    return added


def _run_seed_knowledge_onboarding(
    data_dir: str | None = None,
    cwd: Path | None = None,
) -> dict:
    """Guide first-time users to save enough seed context for get_user_context."""
    Engram = _get_engram_class()
    root = Path(data_dir).expanduser().resolve() if data_dir else Path.home() / ".engram"
    root.mkdir(parents=True, exist_ok=True)
    engram = Engram(root=root)
    current_dir = cwd or Path.cwd()

    # --- Environment probing (zero-interaction) ---
    print(_t("  🔍 正在探测开发环境…", "  🔍 Probing dev environment…"))
    env_signals = _probe_environment(cwd=current_dir)

    probed_parts: list[str] = []
    if env_signals.get("name"):
        probed_parts.append(_t(f"姓名: {env_signals['name']}", f"Name: {env_signals['name']}"))
    if env_signals.get("tech_stack_hint"):
        probed_parts.append(_t(f"技术栈: {env_signals['tech_stack_hint']}", f"Tech: {env_signals['tech_stack_hint']}"))
    if env_signals.get("language_hint"):
        probed_parts.append(_t(f"语言: {env_signals['language_hint']}", f"Language: {env_signals['language_hint']}"))
    if env_signals.get("commit_style"):
        probed_parts.append(_t(f"提交风格: {env_signals['commit_style']}", f"Commits: {env_signals['commit_style']}"))

    if probed_parts:
        print(_t("  ✅ 自动探测到：", "  ✅ Auto-detected:"))
        for p in probed_parts:
            _safe_print(f"     {p}")
        print()
    else:
        print(_t("  （未探测到额外信息）\n", "  (no extra signals detected)\n"))

    # Auto-fill name from git config
    if env_signals.get("name"):
        existing_profile = engram.get_profile()
        if not existing_profile.get("name"):
            engram.update_profile({"name": env_signals["name"]})

    print(_t("Step 2/3 — 录入种子知识（输入 0 跳过）\n",
             "Step 2/3 — Seed knowledge (enter 0 to skip)\n"))

    # Pre-select tech stack options: put detected stack first
    tech_options = [
        "Python",
        "TypeScript / JavaScript",
        "Go",
        "Java",
        "Rust",
        "Python + TypeScript",
    ]
    detected_tech = env_signals.get("tech_stack_hint", "")
    # Move detected tech to front if it matches an option
    for i, opt in enumerate(tech_options):
        if detected_tech and opt.lower() in detected_tech.lower():
            tech_options.insert(0, tech_options.pop(i))
            break

    role = _choice(_t("你的角色是什么？", "What is your role?"), [
        _t("全栈开发者", "Full-stack developer"),
        _t("后端开发者", "Backend developer"),
        _t("前端开发者", "Frontend developer"),
        _t("产品经理", "Product manager"),
        _t("数据科学家", "Data scientist"),
        _t("学生", "Student"),
    ])
    print()
    tech_stack = _choice(_t("你常用什么编程语言/技术栈？", "Primary language / tech stack?"), tech_options)
    print()

    # Pre-select language: put detected language first
    lang_options = [
        "中文",
        "English",
        _t("日本语", "Japanese"),
    ]
    if env_signals.get("language_hint") == "中文":
        pass  # already first
    elif env_signals.get("language_hint"):
        # Non-Chinese detected, move English first
        lang_options = ["English", "中文", _t("日本语", "Japanese")]

    language = _choice(_t("你偏好 AI 用什么语言跟你沟通？",
                          "Preferred language for AI communication?"), lang_options)

    profile_updates: dict[str, str] = {}
    if role:
        profile_updates["role"] = role
    if language:
        profile_updates["language"] = language
    if tech_stack:
        profile_updates["tech_stack"] = tech_stack
        existing_profile = engram.get_profile()
        if not existing_profile.get("description"):
            profile_updates["description"] = _t(
                f"常用技术栈：{tech_stack}",
                f"Primary tech stack: {tech_stack}",
            )
    if profile_updates:
        engram.update_profile(profile_updates)

    lessons_added = 0
    first_lesson = _prompt(_t(
        "  你有没有一条 AI 工具总是忘记的规则或偏好？录入一条试试",
        "  Any rule or preference your AI tools keep forgetting? Enter one to try",
    ), "")
    lesson_inputs = [first_lesson] if first_lesson else []
    while lesson_inputs and len(lesson_inputs) < 3:
        next_lesson = _prompt(_t("  还有吗？（直接回车跳过）",
                                 "  More? (Enter to skip)"), "")
        if not next_lesson:
            break
        lesson_inputs.append(next_lesson)

    for lesson in lesson_inputs:
        result = engram.add_lesson(lesson, domain="setup", source_tool="engram_setup")
        if result.get("status") != "duplicate":
            lessons_added += 1

    # --- Seed templates (auto-inject best practices) ---
    effective_tech = tech_stack or env_signals.get("tech_stack_hint", "")
    seed_count = 0
    if effective_tech:
        seed_count = _apply_seed_templates(engram, effective_tech)
        if seed_count:
            print(_t(f"\n  🌱 已注入 {seed_count} 条通用最佳实践（基于 {effective_tech}）",
                     f"\n  🌱 Injected {seed_count} starter best practices (based on {effective_tech})"))
            print(_t("     这些标记为 staging——使用 3 次后自动晋升为 verified。",
                     "     These are marked staging — auto-promoted to verified after 3 uses."))

    # Step 4.5 — 智能扫描 + 分流导入
    print(_t("\n  智能导入规则文件",
             "\n  Smart rule file import"))
    rule_files = _scan_rule_files(cwd=current_dir)
    import_result: dict = {"user_count": 0, "project_count": 0, "skipped": 0, "files": []}

    if rule_files:
        print(_t(f"\n  扫描到 {len(rule_files)} 个规则文件：",
                 f"\n  Found {len(rule_files)} rule file(s):"))
        for rf in rule_files:
            scope_label = _t("全局", "global") if rf["scope"] == "global" else _t("项目", "project")
            content_count = sum(1 for l in rf["lines"] if l.strip() and not l.strip().startswith("#"))
            print(_t(f"  [{scope_label}] {rf['path']} ({content_count} 行有效内容)",
                     f"  [{scope_label}] {rf['path']} ({content_count} content lines)"))

        # 预览分流
        user_preview = project_preview = skip_preview = 0
        for rf in rule_files:
            for line in rf["lines"]:
                cat = _classify_line(line, rf["scope"])
                if cat == "user":
                    user_preview += 1
                elif cat == "project":
                    project_preview += 1
                else:
                    skip_preview += 1

        print(_t("\n  分流预览：", "\n  Classification preview:"))
        print(_t(f"    用户身份: {user_preview} 条",
                 f"    User identity: {user_preview}"))
        print(_t(f"    项目规则: {project_preview} 条",
                 f"    Project rules: {project_preview}"))
        print(_t(f"    跳过:     {skip_preview} 条",
                 f"    Skipped:       {skip_preview}"))

        import_result = _import_with_split(rule_files, engram)
        print(_t(f"\n  ✅ 已导入: {import_result['user_count']} 条身份 + {import_result['project_count']} 条项目规则",
                 f"\n  ✅ Imported: {import_result['user_count']} identity + {import_result['project_count']} project rules"))
    else:
        print(_t("  未发现规则文件（CLAUDE.md / .cursorrules 等）。",
                 "  No rule files found (CLAUDE.md / .cursorrules etc.)."))

    total_imported = import_result["user_count"] + import_result["project_count"]

    print("\n========================================")
    print(_t("  Engram 初始化完成！", "  Engram setup complete!"))
    print("========================================\n")
    if role or tech_stack or language:
        identity_parts = [role or "-", tech_stack or "-", language or "-"]
        print(_t(f"  身份：{' | '.join(identity_parts)}",
                 f"  Identity: {' | '.join(identity_parts)}"))
    else:
        print(_t("  身份：未填写", "  Identity: not set"))
    print(_t(f"  经验：已录入 {lessons_added} 条",
             f"  Lessons: {lessons_added} recorded"))
    if total_imported > 0:
        print(_t(f"  导入：{total_imported} 条规则（{import_result['user_count']} 条身份 + {import_result['project_count']} 条项目）",
                 f"  Imported: {total_imported} rules ({import_result['user_count']} identity + {import_result['project_count']} project)"))
    if seed_count > 0:
        print(_t(f"  种子：{seed_count} 条最佳实践（staging 层级）",
                 f"  Seeds: {seed_count} best practices (staging tier)"))
    print()
    print(_t("  验证方法：打开你的 AI 工具，说这句话：",
             "  To verify: open your AI tool and say:"))
    print()
    print(_t("    请同步 Engram 上下文，然后告诉我你现在知道我什么。",
             "    Sync Engram context, then tell me what you know about me."))
    print()
    print(_t("  如果 AI 能说出你的角色、语言偏好、技术栈，",
             "  If the AI mentions your role, language, and tech stack,"))
    print(_t("  就说明 Engram 已经在工作了。\n",
             "  Engram is working.\n"))

    # --- First-day aha moment: show identity card preview ---
    try:
        from .core import Engram as _Engram
    except ImportError:
        try:
            from core import Engram as _Engram  # type: ignore
        except ImportError:
            _Engram = None  # type: ignore
    if _Engram is not None:
        try:
            _e = _Engram()
            card = _e.export_identity_card()
            if card and len(card.strip().splitlines()) > 5:
                print("----------------------------------------")
                print(_t("  [CARD] AI identity card preview:\n",
                         "  [CARD] AI identity card preview:\n"))
                for line in card.strip().splitlines():
                    _safe_print(f"  {line}")
                print()
                print("----------------------------------------")
                print(_t("  Tip: AI tools can see this via get_identity_card.",
                         "  Tip: AI tools can see this via get_identity_card."))
                print()
        except Exception:
            pass  # Non-critical — skip silently

    # --- Refresh quick_context.md so all tools can read it immediately ---
    try:
        _e2 = Engram(root=root)
        _e2.refresh_quick_context()
        print(_t("  📄 quick_context.md 已刷新 — 所有 AI 工具都可以立即读取。",
                 "  📄 quick_context.md refreshed — all AI tools can read it immediately."))
        print()
    except Exception:
        pass

    # --- Post-setup checklist ---
    print("========================================")
    print(_t("  接下来：", "  Next steps:"))
    print("========================================\n")
    print(_t("  1. 重启你的 AI 工具（Claude Code / Cursor / Codex 等）",
             "  1. Restart your AI tool (Claude Code / Cursor / Codex etc.)"))
    print(_t("  2. 对 AI 说：请同步 Engram 上下文",
             '  2. Say to AI: "Sync Engram context"'))
    print(_t("  3. 确认 AI 能说出你的角色和偏好",
             "  3. Confirm AI knows your role and preferences"))
    print(_t("  4. 随时运行 engram doctor 检查健康状态\n",
             "  4. Run 'engram doctor' anytime to check health\n"))

    return {
        "profile": profile_updates,
        "lessons_added": lessons_added,
        "seed_count": seed_count,
        "env_signals": env_signals,
        "imported_files": import_result["files"],
        "import_user_count": import_result["user_count"],
        "import_project_count": import_result["project_count"],
    }


# ---------------------------------------------------------------------------
# Privacy & data preferences (telemetry opt-in + reconcile authorization)
# ---------------------------------------------------------------------------

def _run_privacy_preferences(data_dir: str) -> None:
    """Ask user about auto-reconcile and anonymous usage statistics."""
    from piia_engram.telemetry import _load_config, _save_config, set_enabled

    cfg = _load_config()

    print(_t("\nStep 5 — 隐私与数据偏好", "\nStep 5 — Privacy & data preferences"))
    print(_t("  你的数据默认只留在本机。以下两项可选功能需要你明确同意。\n",
             "  Your data stays local by default. The following optional features require your explicit consent.\n"))

    # --- Reconcile authorization ---
    print(_t("  [1] 跨工具记忆同步",
             "  [1] Cross-tool memory sync"))
    print(_t("      Engram 可以在每次启动时自动扫描其他 AI 工具的配置文件",
             "      Engram can scan other AI tools' config files on each startup"))
    print(_t("      （如 ~/.claude/projects/*/memory/*.md、CLAUDE.md、.cursorrules 等）",
             "      (e.g. ~/.claude/projects/*/memory/*.md, CLAUDE.md, .cursorrules)"))
    print(_t("      并导入其中的规则和记忆到 Engram。",
             "      and import rules and memories into Engram."))
    print(_t("      扫描结果会显示在 get_user_context 输出中。\n",
             "      Results appear in get_user_context output.\n"))

    reconcile_authorized = _yn(
        _t("  允许 Engram 扫描其他 AI 工具的文件？",
           "  Allow Engram to scan other AI tools' files?"),
        default=True,
    )
    cfg["reconcile_authorized"] = reconcile_authorized
    if reconcile_authorized:
        print(_t("  ✅ 已授权跨工具同步\n", "  ✅ Cross-tool sync authorized\n"))
    else:
        print(_t("  ℹ️  已关闭跨工具同步。可设置 ENGRAM_RECONCILE=1 重新开启。\n",
                 "  ℹ️  Cross-tool sync disabled. Set ENGRAM_RECONCILE=1 to re-enable.\n"))

    # --- Anonymous usage statistics ---
    print(_t("  [2] 匿名使用统计",
             "  [2] Anonymous usage statistics"))
    print(_t("      帮助我们了解哪些功能被使用、哪些需要改进。",
             "      Help us understand which features are used and need improvement."))
    print(_t("      每天最多记录一次，内容如下：",
             "      Logged at most once per day:"))
    print(_t("        • 工具调用计数（只有工具名和次数，无参数和内容）",
             "        • Tool call counts (names + counts only, no arguments or content)"))
    print(_t("        • 知识条目总数（只有数字，无内容）",
             "        • Knowledge entry totals (counts only, no content)"))
    print(_t("        • Engram 版本号",
             "        • Engram version"))
    print(_t("      绝不发送：知识内容、prompt、文件路径、邮箱、IP 地址",
             "      Never sent: knowledge content, prompts, file paths, email, IP"))
    print(_t("      当前为 Phase 1：仅记录到本地文件，不发送网络请求。",
             "      Current Phase 1: logged to local file only, no network requests."))
    print(_t(f"      日志位置：{data_dir}/telemetry.log",
             f"      Log location: {data_dir}/telemetry.log"))
    print(_t("      查看将记录的内容：engram telemetry preview",
             "      Preview what's logged: engram telemetry preview"))
    print(_t("      随时关闭：engram telemetry off\n",
             "      Disable anytime: engram telemetry off\n"))

    telemetry_enabled = _yn(
        _t("  开启匿名使用统计？",
           "  Enable anonymous usage statistics?"),
        default=False,
    )
    set_enabled(telemetry_enabled)
    if telemetry_enabled:
        print(_t("  ✅ 已开启（仅本地日志，Phase 1 不发送网络请求）\n",
                 "  ✅ Enabled (local log only, Phase 1 sends no network requests)\n"))
    else:
        print(_t("  ℹ️  未开启。可随时运行 engram telemetry on 改变。\n",
                 "  ℹ️  Not enabled. Run engram telemetry on to change anytime.\n"))

    # Save reconcile pref to same config file
    cfg_all = _load_config()
    cfg_all["reconcile_authorized"] = reconcile_authorized
    _save_config(cfg_all)


def _run_privacy_defaults(data_dir: str) -> None:
    """Apply safe privacy defaults without prompting (reconcile=on, telemetry=off).

    For interactive privacy configuration, use ``engram setup --advanced``.
    """
    from piia_engram.telemetry import _load_config, _save_config, set_enabled

    cfg = _load_config()
    cfg["reconcile_authorized"] = True
    _save_config(cfg)
    set_enabled(False)

    print(_t("  隐私设置：跨工具同步=开启，匿名统计=关闭（默认）",
             "  Privacy: cross-tool sync=on, anonymous stats=off (defaults)"))
    print(_t("  可随时通过 'engram setup --advanced' 或 'engram telemetry on' 修改。\n",
             "  Change anytime via 'engram setup --advanced' or 'engram telemetry on'.\n"))


# ---------------------------------------------------------------------------
# 向导主流程
# ---------------------------------------------------------------------------

def run_setup(advanced: bool = False) -> None:
    """交互式安装向导主流程。

    Streamlined flow: auto-detect + auto-configure where possible,
    only prompt when there is a real choice to make.

    Args:
        advanced: If True, show full interactive privacy preferences.
    """
    global _lang
    _configure_utf8_stdio()

    # 语言选择（最先，决定后续所有文案）
    print("\n  Language / 语言选择:")
    print("    1. 中文")
    print("    2. English")
    lang_answer = _prompt("  Choose / 请选择", "1").strip()
    _lang = "en" if lang_answer == "2" else "zh"
    print()

    print("========================================")
    print(_t("  PIIA Engram 安装向导", "  PIIA Engram Setup Wizard"))
    print("========================================\n")

    # Step 1 — 自动检测环境
    print(_t("Step 1/3 — 检测环境", "Step 1/3 — Detecting environment"))
    python_path = _find_python()
    if not python_path:
        print(_t("❌ 未找到可用的 Python 3.10+。", "❌ Python 3.10+ not found."))
        print(_t("   请安装 Python 后重新运行：https://python.org/downloads/",
                 "   Please install Python and re-run: https://python.org/downloads/"))
        sys.exit(1)
    print(f"  ✅ Python: {python_path}")

    mcp_server_path = _find_mcp_server()
    if not mcp_server_path:
        print(_t("❌ 未找到 mcp_server.py，请确认已正确安装（pip install piia-engram）。",
                 "❌ mcp_server.py not found. Please ensure piia-engram is installed."))
        sys.exit(1)

    # 数据目录 — 优先读 ENGRAM_DIR 环境变量
    default_data_dir = os.environ.get("ENGRAM_DIR") or str(Path.home() / ".engram")
    data_dir: str | None = None
    print(_t(f"  ✅ 数据目录: {default_data_dir}",
             f"  ✅ Data dir: {default_data_dir}"))

    # 工具检测 — 自动配置
    tools = _detect_tools()
    if not tools:
        print(_t("  ⚠️  未检测到 AI 工具（Claude Code / Cursor / Claude Desktop）",
                 "  ⚠️  No AI tools detected (Claude Code / Cursor / Claude Desktop)"))
        print(_t("  安装后重新运行 'engram setup' 即可。\n",
                 "  Re-run 'engram setup' after installing.\n"))
    else:
        success = []
        failed = []
        for tool in tools:
            try:
                _write_mcp_config(tool["config_path"], python_path, mcp_server_path, data_dir)
                success.append(tool["name"])
            except Exception as exc:
                failed.append(f"{tool['name']} ({exc})")
        for name in success:
            print(_t(f"  ✅ {name} 已配置", f"  ✅ {name} configured"))
        for name in failed:
            print(_t(f"  ❌ {name} 配置失败", f"  ❌ {name} failed"))
    print()

    # Step 2 — 录入身份信息
    selected_data_dir = data_dir or default_data_dir
    _run_seed_knowledge_onboarding(selected_data_dir)

    # Step 3 — 隐私偏好
    if advanced:
        _run_privacy_preferences(selected_data_dir)
    else:
        _run_privacy_defaults(selected_data_dir)

    # 完成
    print(_t("  重启你的 AI 工具（Claude Code / Cursor）即可使用。",
             "  Restart your AI tool (Claude Code / Cursor) to get started."))
    print()
    print(_t("  觉得有用？来聊聊你怎么用的：",
             "  Find Engram useful? Share how you use it:"))
    print("  https://github.com/Patdolitse/piia-engram/discussions\n")
    print(_t("  遇到问题？",
             "  Issues?"))
    print("  https://github.com/Patdolitse/piia-engram/issues\n")


def auto_migrate() -> None:
    """升级后首次启动时静默迁移旧配置，每个版本只运行一次。

    由 mcp_server.py 在 stdio 模式启动前调用。
    不向 stdout 输出任何内容（避免破坏 MCP 协议）。
    迁移日志写入 ~/.engram/migration.log。
    """
    try:
        import os as _os
        from datetime import datetime

        # 确定数据目录和哨兵文件
        data_dir = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
        sentinel = data_dir / ".migrated_version"

        # 读取当前版本
        try:
            from piia_engram import __version__ as _ver  # type: ignore[import]
        except Exception:
            return

        # 已迁移过则跳过
        if sentinel.is_file() and sentinel.read_text(encoding="utf-8").strip() == _ver:
            return

        # 扫描所有工具配置，清理旧版名称
        log_lines: list[str] = []
        for _tool_id, cfg in _tool_configs().items():
            for config_path in cfg["config_paths"]:
                if not config_path.is_file():
                    continue
                config = _read_mcp_config(config_path)
                servers = config.get("mcpServers", {})
                stale = [n for n in LEGACY_SERVER_NAMES if n in servers]
                if not stale:
                    continue
                for name in stale:
                    del servers[name]
                config_path.write_text(
                    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                log_lines.append(f"  {config_path}: removed {stale}")

        # 写哨兵（无论是否有迁移，都标记当前版本已处理过）
        data_dir.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(_ver, encoding="utf-8")

        # 写迁移日志（仅在有实际变更时）
        if log_lines:
            log_file = data_dir / "migration.log"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now().isoformat()}] engram v{_ver} migration:\n")
                for line in log_lines:
                    f.write(line + "\n")
                f.write("  Restart affected AI tools to apply changes.\n")

    except Exception as exc:
        logger.warning("migration failed: %s", exc)


def _detect_installed_tools() -> list[dict]:
    """扫描系统中实际安装的 AI 工具。

    不仅检查配置文件是否存在，还检查工具本身是否安装（配置目录存在）。
    返回 [{tool_id, name, config_path, format, verified, status, config, servers}]。
    - status: "configured" (有 engram 条目), "installed" (工具在但没配 engram)
    - verified: True = 团队实测过, False = 社区级支持
    """
    results = []
    for tool_id, cfg in _tool_configs().items():
        fmt = cfg.get("format", "json")
        server_key = cfg.get("server_key", "mcpServers")
        verified = cfg.get("verified", False)
        for config_path in cfg["config_paths"]:
            # 检查工具是否安装（配置目录存在 = 工具装了）
            tool_dir = config_path.parent
            if not tool_dir.exists():
                continue

            config = _read_mcp_config(config_path, fmt=fmt)

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


def _validate_engram_entry(servers: dict, config_path: Path) -> list[str]:
    """验证 engram MCP 条目的所有路径是否有效。

    Returns:
        问题描述列表（空 = 健康）。
    """
    issues = []
    engram = servers.get("engram", {})
    if not engram:
        return issues

    # 1. Python 可执行路径
    python_exe = engram.get("command", "")
    if python_exe and python_exe not in ("npx", "node", "uvx"):
        exe_path = Path(python_exe.replace("\\\\", "\\"))
        if not exe_path.is_file():
            issues.append(f"Python 路径不存在: {python_exe}")

    # 2. 服务端脚本/模块路径
    args = engram.get("args") or []
    for arg in args:
        if arg.startswith("-"):
            # -m module.name — 不是文件路径，跳过
            continue
        p = Path(arg.replace("\\\\", "\\"))
        # 只验证看起来像路径的参数（含 / 或 \ 或 .py）
        if ("/" in arg or "\\" in arg or arg.endswith(".py")) and not p.is_file():
            issues.append(f"脚本路径不存在: {arg}")

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
    print("\n========================================")
    print("  Engram Doctor - Config Health Check")
    print("========================================\n")

    # ── 第一步：扫描已安装的 AI 工具 ──
    tools = _detect_installed_tools()

    if not tools:
        print("  [!] No supported AI tools detected on this system.\n")
        print("  Verified: Claude Code, Claude Desktop, Cursor, Codex")
        print("  Community: Windsurf, Copilot, Cline, Roo Code, Amazon Q, Augment, Zed\n")
        return 0

    print(f"  Detected {len(tools)} AI tool(s):\n")

    verified_tools = [t for t in tools if t.get("verified")]
    community_tools = [t for t in tools if not t.get("verified")]
    configured_count = 0
    unconfigured: list[dict] = []

    if verified_tools:
        print("  Verified (team tested):")
        for t in verified_tools:
            if t["status"] == "configured":
                _safe_print(f"    [ok] {t['name']} — Engram configured")
                configured_count += 1
            else:
                _safe_print(f"    [--] {t['name']} — Engram NOT configured")
                unconfigured.append(t)

    if community_tools:
        if verified_tools:
            print()
        print("  Community-supported (untested by our team):")
        for t in community_tools:
            if t["status"] == "configured":
                _safe_print(f"    [ok] {t['name']} — Engram configured")
                configured_count += 1
            else:
                _safe_print(f"    [--] {t['name']} — installed, Engram not configured")
                unconfigured.append(t)
    print()

    # ── 第二步：验证已配置的条目 ──
    issues: list[tuple[dict, str]] = []  # (tool_info, 描述)

    for t in tools:
        if t["status"] != "configured":
            continue
        servers = t["servers"]

        # 旧版 server 名称
        stale = [n for n in LEGACY_SERVER_NAMES if n in servers]
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
        func_issues = _run_functional_checks()
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
    python_path = _find_python()
    mcp_server_path = _find_mcp_server()
    if not python_path or not mcp_server_path:
        print("  [error] Cannot auto-fix: Python 3.10+ or mcp_server.py not found.")
        print("          Run 'engram setup' to complete installation first.\n")
        return len(issues)

    fixed = 0
    seen_paths: set[Path] = set()
    for t, _ in issues:
        path = t["config_path"]
        if path in seen_paths:
            continue
        seen_paths.add(path)
        fmt = t.get("format", "json")
        try:
            if fmt == "toml":
                _write_mcp_config_toml(path, python_path, mcp_server_path)
            else:
                _write_mcp_config(path, python_path, mcp_server_path)
            print(f"  [fixed] {t['name']} ({path})")
            fixed += 1
        except Exception as exc:
            print(f"  [error] {t['name']} ({path}): {exc}")

    remaining = len(issues) - fixed
    print(f"\n  Done: {fixed} config(s) updated. Restart your AI tools to apply changes.\n")
    func_issues = _run_functional_checks()
    return remaining + func_issues


def _run_functional_checks() -> int:
    """运行功能性验证：MCP server 能否启动、知识库能否读写、quick_context 是否可用。

    Returns:
        发现的问题数量（0 = 健康）。
    """
    print("  ── Functional Checks ──\n")
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

    # 4. quick_context.md 可用性
    qc = eng.root / "quick_context.md"
    if qc.exists() and qc.stat().st_size > 0:
        print(f"    [ok] quick_context.md ready ({qc.stat().st_size} bytes)")
    else:
        print("    [--] quick_context.md missing or empty — cold-start will be slower")

    # 5. MCP server 工具注册
    try:
        from piia_engram import mcp_server  # noqa: F811

        tool_count = len(mcp_server.mcp._tool_manager._tools)
        print(f"    [ok] MCP server: {tool_count} tools registered")
    except Exception as exc:
        print(f"    [!!] MCP server import failed: {exc}")
        problems += 1

    print()
    return problems


def _run_telemetry_cli(sub_args: list[str]) -> None:
    """Handle `engram telemetry <subcommand>`."""
    from piia_engram.telemetry import (
        get_status, is_enabled, preview_payload, set_enabled,
    )

    sub = sub_args[0] if sub_args else "status"

    if sub == "status":
        status = get_status()
        state = "ON" if status["enabled"] else "OFF"
        print(f"\n  Anonymous usage statistics: {state}")
        print(f"  Phase: {status['phase']}")
        print(f"  Config: {status['config_path']}")
        print(f"  Log: {status['log_path']}")
        if status["enabled"]:
            print(f"  Opted in: {status['opted_in_at']}")
        print()

    elif sub == "preview":
        print("\n  Next payload (if enabled):\n")
        print(preview_payload())
        print()

    elif sub in ("off", "disable"):
        set_enabled(False)
        print("\n  ✅ Anonymous usage statistics disabled.")
        print("  No data will be logged or sent.\n")

    elif sub in ("on", "enable"):
        set_enabled(True)
        print("\n  ✅ Anonymous usage statistics enabled (Phase 1: local log only).")
        print("  Run 'engram telemetry preview' to see what will be logged.\n")

    elif sub == "--show-payload":
        # Alias for preview (ChatGPT Pro recommendation)
        print("\n  Next payload (if enabled):\n")
        print(preview_payload())
        print()

    else:
        print(
            "\nUsage:\n"
            "  engram telemetry status       Show current status\n"
            "  engram telemetry preview      Show what data will be logged\n"
            "  engram telemetry on           Enable anonymous usage statistics\n"
            "  engram telemetry off          Disable anonymous usage statistics\n"
        )


def _run_privacy_report() -> None:
    """Handle `engram privacy` — show what data Engram stores and where."""
    import os as _os
    data_dir = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")

    print("\n========================================")
    print("  Engram Privacy Report")
    print("========================================\n")

    # 1. Data directory
    print(f"  [DIR] Data directory: {data_dir}")
    if data_dir.exists():
        files = list(data_dir.iterdir())
        total_size = sum(f.stat().st_size for f in files if f.is_file())
        print(f"        Files: {len([f for f in files if f.is_file()])}")
        print(f"        Total size: {total_size / 1024:.1f} KB")
    else:
        print("        (not created yet)")
    print()

    # 2. Identity data
    identity_file = data_dir / "identity.json"
    print("  [ID]  Identity data:")
    if identity_file.is_file():
        size = identity_file.stat().st_size
        print(f"        {identity_file} ({size / 1024:.1f} KB)")
        print("        Contains: profile, preferences, work_style, quality_standards, trust_boundaries")
        try:
            raw = identity_file.read_text(encoding="utf-8")
            encrypted_count = raw.count("enc:v")
            if encrypted_count > 0:
                print(f"        [ENCRYPTED] {encrypted_count} fields encrypted")
            else:
                print("        [PLAIN] No encrypted fields (set ENGRAM_KEY to enable)")
        except Exception:
            pass
    else:
        print("        (not created yet)")
    print()

    # 3. Knowledge base
    knowledge_file = data_dir / "knowledge.json"
    print("  [KB]  Knowledge base:")
    if knowledge_file.is_file():
        size = knowledge_file.stat().st_size
        print(f"        {knowledge_file} ({size / 1024:.1f} KB)")
        try:
            import json as _j
            kdata = _j.loads(knowledge_file.read_text(encoding="utf-8"))
            lessons = kdata.get("lessons", [])
            decisions = kdata.get("decisions", [])
            print(f"        Lessons: {len(lessons)}")
            print(f"        Decisions: {len(decisions)}")
        except Exception:
            pass
    else:
        print("        (not created yet)")
    print()

    # 4. Telemetry
    print("  [STAT] Anonymous usage statistics:")
    try:
        from piia_engram.telemetry import get_status
        status = get_status()
        state = "ON" if status["enabled"] else "OFF"
        print(f"        Status: {state}")
        print(f"        Config: {status['config_path']}")
        log_path = Path(status["log_path"])
        if log_path.is_file():
            log_size = log_path.stat().st_size
            log_lines = len(log_path.read_text(encoding="utf-8").strip().splitlines())
            print(f"        Log: {log_path} ({log_size / 1024:.1f} KB, {log_lines} entries)")
        else:
            print("        Log: (no entries yet)")
        print("        Collected: tool names + counts, knowledge totals, version, daily anonymous ID")
        print("        NOT collected: text content, prompts, file paths, PII, IP, OS info")
        print("        Network: Phase 1 = local only, NO network requests")
    except ImportError:
        print("        (telemetry module not available)")
    print()

    # 5. Reconcile
    print("  [SYNC] Cross-tool sync:")
    try:
        from piia_engram.reconcile import ReconcileMixin
        authorized = ReconcileMixin._reconcile_authorized()
        print(f"        Status: {'ON' if authorized else 'OFF'}")
        print("        Scans: ~/.claude/projects/*/memory/*.md, CLAUDE.md, .cursorrules, etc.")
        print("        Control: ENGRAM_RECONCILE=0 to disable")
    except ImportError:
        print("        (reconcile module not available)")
    print()

    # 6. Network
    print("  [NET]  Network requests:")
    print("        Core Engram: ZERO network requests (local files only)")
    print("        Optional: read_web_content (user-initiated only, via local Reader service)")
    print("        Optional: telemetry Phase 2 (NOT implemented, requires re-consent)")
    print()

    # 7. How to delete
    print("  [DEL]  Delete all data:")
    print(f"        rm -rf {data_dir}")
    print("        (This removes ALL Engram data permanently)")
    print()


def main() -> None:
    """CLI 入口：engram setup / engram doctor [--fix] / engram telemetry <sub> / engram privacy"""
    args = sys.argv[1:]
    if not args or args[0] == "setup":
        if "--advanced" in args:
            run_setup(advanced=True)
        else:
            run_setup()
    elif args[0] == "doctor":
        fix = "--fix" in args
        sys.exit(run_doctor(fix=fix))
    elif args[0] == "stats":
        from piia_engram.stats import run_stats, log_stats
        if "--log" in args:
            log_stats()
        else:
            run_stats()
    elif args[0] == "telemetry":
        _run_telemetry_cli(args[1:])
    elif args[0] == "privacy":
        _run_privacy_report()
    else:
        print(
            "Engram CLI\n\n"
            "Usage:\n"
            "  engram setup            Interactive install wizard (streamlined)\n"
            "  engram setup --advanced Full interactive setup with privacy prompts\n"
            "  engram doctor           Check config health (all AI tools)\n"
            "  engram doctor --fix     Auto-repair any issues found\n"
            "  engram stats            Show project growth metrics\n"
            "  engram stats --log      Append stats snapshot to local log\n"
            "  engram telemetry        Manage anonymous usage statistics\n"
            "  engram privacy          Show what data Engram stores\n\n"
            "Tool tiers:\n"
            "  Default: 13 核心工具 / core MCP tools.\n"
            "  Set ENGRAM_TOOLS=all to unlock all 48 tools.\n"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
