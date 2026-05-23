"""Engram agent context auto-save — 像 Office 自动保存一样保护 AI 对话上下文。

设计原则：
- 静默记录，按需找回（行车记录仪模式）
- 按 tool 分隔，互不干扰
- 不自动恢复到新会话，不过期删除
- 文件极小（几 KB），永久保留
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _sanitize_tool_name(name: str) -> str:
    """Normalize tool name for filesystem use."""
    return name.strip().lower().replace(" ", "_").replace("/", "_")


class ContextStoreMixin:
    """Mixin: agent context auto-save and recovery.

    Stores conversation checkpoints per-tool in ``~/.engram/contexts/{tool}/``.
    Each session is one ``.md`` file, append-only during the session.
    """

    root: Path  # provided by Engram base class

    @property
    def _contexts_dir(self) -> Path:
        return self.root / "contexts"

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save_agent_context(
        self,
        tool: str,
        content: str,
        session_id: str = "",
        project_folder: str = "",
    ) -> dict[str, Any]:
        """Save or append a context checkpoint for a tool session.

        Args:
            tool: Tool name (e.g. ``claude_code``, ``codex``, ``cursor``).
            content: Free-text checkpoint — tasks, progress, next steps.
            session_id: Reuse to append to an existing session file.
                        If empty, a new session file is created.
            project_folder: Optional project path (written in the header).

        Returns:
            ``{session_id, file, tool, appended}``
        """
        tool_safe = _sanitize_tool_name(tool)
        tool_dir = self._contexts_dir / tool_safe
        tool_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now()

        if not session_id:
            session_id = now.strftime("%Y-%m-%dT%H-%M-%S")

        file_path = tool_dir / f"{session_id}.md"
        timestamp = now.strftime("%H:%M")
        appended = file_path.exists()

        if appended:
            existing = file_path.read_text(encoding="utf-8")
            entry = f"\n### {timestamp}\n{content}\n"
            file_path.write_text(existing + entry, encoding="utf-8")
        else:
            header = f"# Session: {tool} @ {now.strftime('%Y-%m-%d %H:%M')}\n"
            if project_folder:
                header += f"## Project: {project_folder}\n"
            header += f"\n### {timestamp}\n{content}\n"
            file_path.write_text(header, encoding="utf-8")

        return {
            "session_id": session_id,
            "file": str(file_path),
            "tool": tool_safe,
            "appended": appended,
        }

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_recent_context(
        self,
        tool: str = "",
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        """Return the most recent context sessions.

        Args:
            tool: Tool name.  If empty, searches **all** tools.
            limit: Max sessions to return (default 1 = latest only).

        Returns:
            List of ``{tool, session_id, content, modified_at}`` dicts,
            sorted newest-first.
        """
        results: list[dict[str, Any]] = []

        if tool:
            tool_names = [_sanitize_tool_name(tool)]
        else:
            if not self._contexts_dir.exists():
                return []
            tool_names = [
                d.name for d in self._contexts_dir.iterdir() if d.is_dir()
            ]

        for t in tool_names:
            tool_dir = self._contexts_dir / t
            if not tool_dir.exists():
                continue
            files = sorted(
                tool_dir.glob("*.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for f in files[:limit]:
                results.append({
                    "tool": t,
                    "session_id": f.stem,
                    "content": f.read_text(encoding="utf-8"),
                    "modified_at": datetime.fromtimestamp(
                        f.stat().st_mtime,
                    ).replace(microsecond=0).isoformat(),
                })

        results.sort(key=lambda x: x["modified_at"], reverse=True)
        return results[:limit]

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_agent_sessions(
        self,
        tool: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List available context sessions (metadata only, no content).

        Args:
            tool: Tool name.  If empty, lists **all** tools.
            limit: Max sessions to return.

        Returns:
            List of ``{tool, session_id, modified_at, size_bytes}`` dicts,
            sorted newest-first.
        """
        results: list[dict[str, Any]] = []

        if tool:
            tool_names = [_sanitize_tool_name(tool)]
        else:
            if not self._contexts_dir.exists():
                return []
            tool_names = [
                d.name for d in self._contexts_dir.iterdir() if d.is_dir()
            ]

        for t in tool_names:
            tool_dir = self._contexts_dir / t
            if not tool_dir.exists():
                continue
            files = sorted(
                tool_dir.glob("*.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for f in files:
                results.append({
                    "tool": t,
                    "session_id": f.stem,
                    "modified_at": datetime.fromtimestamp(
                        f.stat().st_mtime,
                    ).replace(microsecond=0).isoformat(),
                    "size_bytes": f.stat().st_size,
                })

        results.sort(key=lambda x: x["modified_at"], reverse=True)
        return results[:limit]
