"""
Engram one-click setup checker.

Run:
    python setup_engram.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


ENGRAM_DIRS = ("identity", "knowledge", "projects", "exports", "compat")
DEFAULT_PYTHON = "python"
MCP_RELATIVE_PATH = "src/engram_core/mcp_server.py"


def configure_output() -> None:
    """Prefer UTF-8 output on Windows consoles to avoid encoding failures."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def read_json(path: Path, fallback: Any) -> Any:
    if not path.is_file():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def parse_version(value: Any) -> tuple[int, ...]:
    parts = str(value or "0").strip().lstrip("vV").split(".")
    numbers: list[int] = []
    for part in parts:
        digits = "".join(ch for ch in part if ch.isdigit())
        numbers.append(int(digits) if digits else 0)
    return tuple(numbers or [0])


def is_at_least_version(current: Any, target: str) -> bool:
    left = parse_version(current)
    right = parse_version(target)
    size = max(len(left), len(right))
    return left + (0,) * (size - len(left)) >= right + (0,) * (size - len(right))


def ensure_engram_dirs(engram_root: Path) -> bool:
    existed = engram_root.is_dir()
    for subdir in ENGRAM_DIRS:
        (engram_root / subdir).mkdir(parents=True, exist_ok=True)

    if existed:
        print(f"[OK] Engram 目录已存在: {engram_root}")
    else:
        print(f"[OK] Engram 目录已创建: {engram_root}")
    return existed


def check_schema(engram_root: Path) -> str:
    data = read_json(engram_root / "schema_version.json", {})
    version = data.get("schema_version") if isinstance(data, dict) else None

    if is_at_least_version(version, "2.0"):
        print("[OK] Schema v2.0")
        return "v2.0"

    print("[!] Schema 需要升级，请运行 Engram MCP server 自动迁移")
    return "needs_upgrade"


def profile_has_content(profile: Any) -> bool:
    return isinstance(profile, dict) and bool(profile) and bool(profile.get("role"))


def ensure_profile(
    engram_root: Path,
    input_func: Callable[[str], str] = input,
) -> str:
    profile_path = engram_root / "identity" / "profile.json"
    profile = read_json(profile_path, {})

    if profile_has_content(profile):
        role = profile.get("role", "未填写")
        language = profile.get("language", "未填写")
        print(f"[OK] 身份画像: {role} / {language}")
        return "existing"

    print("[!] 身份画像为空，请填写基本信息")
    role = input_func("role（例如：创业者/产品负责人）: ").strip()
    language = input_func("language（例如：中文）: ").strip()
    technical_level = input_func("technical_level（例如：非技术背景，通过AI工具学习编程）: ").strip()

    profile = {
        "role": role,
        "language": language,
        "technical_level": technical_level,
        "updated_at": now_iso(),
    }
    write_json(profile_path, profile)
    print(f"[OK] 身份画像已写入: {role} / {language}")
    return "created"


def normalize_list_file(path: Path) -> list[Any]:
    data = read_json(path, [])
    if isinstance(data, list):
        return data
    if not path.is_file():
        write_json(path, [])
    return []


def check_knowledge_assets(engram_root: Path) -> tuple[int, int]:
    lessons_path = engram_root / "knowledge" / "lessons.json"
    decisions_path = engram_root / "knowledge" / "decisions.json"

    if not lessons_path.is_file():
        write_json(lessons_path, [])
    if not decisions_path.is_file():
        write_json(decisions_path, [])

    lessons = normalize_list_file(lessons_path)
    decisions = normalize_list_file(decisions_path)
    print(f"[OK] 经验教训: {len(lessons)} 条 | 关键决策: {len(decisions)} 条")
    return len(lessons), len(decisions)


def mcp_snippet(project_root: Path) -> dict[str, Any]:
    python_path = Path(sys.executable) if sys.executable else Path(DEFAULT_PYTHON)
    mcp_script = project_root / MCP_RELATIVE_PATH
    return {
        "mcpServers": {
            "engram": {
                "command": str(python_path),
                "args": [str(mcp_script)],
            }
        }
    }


def check_mcp_config(claude_home: Path, project_root: Path) -> bool:
    mcp_path = claude_home / ".mcp.json"
    data = read_json(mcp_path, {})
    configured = False

    if isinstance(data, dict):
        servers = data.get("mcpServers")
        configured = isinstance(servers, dict) and "engram" in servers

    if configured:
        print("[OK] Claude Code MCP 已配置")
        return True

    print("[!] Claude Code MCP 未配置")
    print("请将下面的 JSON 片段合并到 ~/.claude/.mcp.json：")
    print(json.dumps(mcp_snippet(project_root), ensure_ascii=False, indent=2))
    return False


def print_summary(
    engram_root: Path,
    schema_status: str,
    profile_status: str,
    lesson_count: int,
    decision_count: int,
    mcp_configured: bool,
) -> None:
    print()
    print("=" * 60)
    print("Engram 初始化检查完成")
    print(f"- Engram 目录: {engram_root}")
    print(f"- Schema: {'v2.0' if schema_status == 'v2.0' else '需要升级'}")
    print(f"- 身份画像: {'已存在' if profile_status == 'existing' else '已创建'}")
    print(f"- 知识资产: 经验教训 {lesson_count} 条 / 关键决策 {decision_count} 条")
    print(f"- Claude Code MCP: {'已配置' if mcp_configured else '未配置'}")
    print()
    print("下一步：")
    if schema_status != "v2.0":
        print("1. 启动 Engram MCP server，让它自动迁移到 Schema v2.0。")
    if not mcp_configured:
        print("2. 将上面的 engram 配置片段加入 ~/.claude/.mcp.json。")
    if schema_status == "v2.0" and mcp_configured:
        print("全部核心配置已就绪，可以开始跨工具共享 Engram。")
    print("=" * 60)


def run_setup(
    engram_root: Path | None = None,
    claude_home: Path | None = None,
    project_root: Path | None = None,
    input_func: Callable[[str], str] = input,
) -> dict[str, Any]:
    engram_root = engram_root or (Path.home() / ".engram")
    claude_home = claude_home or (Path.home() / ".claude")
    project_root = project_root or Path(__file__).resolve().parent.parent

    print("=" * 60)
    print("Engram 一键初始化检查")
    print("=" * 60)

    ensure_engram_dirs(engram_root)
    schema_status = check_schema(engram_root)
    profile_status = ensure_profile(engram_root, input_func=input_func)
    lesson_count, decision_count = check_knowledge_assets(engram_root)
    mcp_configured = check_mcp_config(claude_home, project_root)
    print_summary(
        engram_root=engram_root,
        schema_status=schema_status,
        profile_status=profile_status,
        lesson_count=lesson_count,
        decision_count=decision_count,
        mcp_configured=mcp_configured,
    )

    return {
        "schema_status": schema_status,
        "profile_status": profile_status,
        "lesson_count": lesson_count,
        "decision_count": decision_count,
        "mcp_configured": mcp_configured,
    }


def main() -> None:
    configure_output()
    run_setup()


if __name__ == "__main__":
    main()
