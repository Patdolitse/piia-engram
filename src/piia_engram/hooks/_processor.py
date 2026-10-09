"""Deferred hook work. Imported only by an explicit drain, never by producers."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import _cursor_payload as cursor


def validate_payload(event: dict) -> None:
    payload = event["payload"]
    kind = event["kind"]
    for key in ("summary", "transcript_path", "project_folder", "session_id", "event", "hook_cwd"):
        if key in payload and not isinstance(payload[key], str):
            raise ValueError("payload text type")
    if "roots" in payload and (not isinstance(payload["roots"], list) or
                               any(not isinstance(x, str) for x in payload["roots"])):
        raise ValueError("payload root type")
    if "threshold" in payload and (type(payload["threshold"]) is not int or payload["threshold"] < 1):
        raise ValueError("payload threshold")
    attempts = event.get("transcript_missing_attempts", 0)
    if type(attempts) is not int or attempts < 0:
        raise ValueError("transcript retry count")
    if "prepared" in event:
        prepared = event["prepared"]
        if not isinstance(prepared, dict) or any(not isinstance(prepared.get(key, ""), str)
                                               for key in ("context", "summary", "session_id")):
            raise ValueError("prepared type")
        if any(key in prepared and type(prepared[key]) is not bool for key in ("skip", "digest")):
            raise ValueError("prepared flag type")
        if "project_revision" in prepared:
            if type(prepared["project_revision"]) is not int or prepared["project_revision"] < 0:
                raise ValueError("prepared revision")
            captured = datetime.fromisoformat(prepared["project_revision_captured_at"])
            if captured.tzinfo is None:
                raise ValueError("prepared revision capture time")
        elif "project_revision_captured_at" in prepared:
            raise ValueError("prepared revision missing")
        if prepared.get("skip") is True:
            return
        required = {"claude_stop": ("context", "summary", "digest"),
                    "claude_compact": ("summary",),
                    "cursor_save": ("context", "session_id"),
                    "cursor_writeback": ("summary",)}[kind]
        if any(key not in prepared for key in required):
            raise ValueError("prepared required fields")
        if kind in {"claude_stop", "cursor_save"} and not prepared["context"].strip():
            raise ValueError("prepared empty context")
        if kind == "claude_compact" and not prepared["summary"].strip():
            raise ValueError("prepared empty compact summary")
    elif kind in {"claude_stop", "claude_compact"}:
        if not payload.get("transcript_path", "").strip():
            raise ValueError("required transcript reference")
    elif kind == "cursor_writeback":
        if not (payload.get("summary", "").strip() or payload.get("transcript_path", "").strip()):
            raise ValueError("required writeback input")


def _claude_summary(payload: dict, root: Path, engram) -> dict:
    transcript = Path(payload.get("transcript_path", ""))
    # A missing reference is retryable, not a successfully captured empty session.
    count = 0
    tools = []
    first = last = ""
    with transcript.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            count += 1
            timestamp = entry.get("timestamp", "")
            if isinstance(timestamp, str) and timestamp:
                first = first or timestamp
                last = timestamp
            blocks = entry.get("content", [])
            if not isinstance(blocks, list):
                blocks = []
            for block in [entry] + blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "")
                    if isinstance(name, str) and name and name not in tools:
                        tools.append(name)
    threshold = payload.get("threshold", 10)
    if count < max(4, threshold // 2):
        return {"skip": True}
    duration = "unknown"
    try:
        minutes = max(1, int((datetime.fromisoformat(last.replace("Z", "+00:00")) -
                             datetime.fromisoformat(first.replace("Z", "+00:00"))).total_seconds() / 60))
        duration = f"{minutes} 分钟"
    except (ValueError, TypeError):
        pass
    cwd = payload.get("project_folder", "")
    context = f"[Claude Code Stop Hook 自动记录]\n工作目录: {cwd}\n会话消息数: {count}\n会话时长: {duration}\n"
    if tools:
        context += f"使用的工具: {', '.join(tools[:30])}\n"
    summary = ""
    digest_appended = False
    if count >= threshold:
        summary = f"Claude Code 会话 ({duration}, {count} 消息)\n工作目录: {cwd}\n"
        if tools:
            summary += f"使用工具: {', '.join(tools[:20])}\n"
        from ..hook_digest import PREFERENCE_KEY_V2, build_digest, digest_enabled
        if engram is None:
            from ..core import Engram
            engram = Engram(root=root, read_only=True)
        if digest_enabled(engram.get_preferences().get(PREFERENCE_KEY_V2)):
            digest = build_digest(transcript.read_text(encoding="utf-8", errors="replace").splitlines())
            if digest:
                summary += "\n" + digest
                digest_appended = True
    return {"context": context, "summary": summary, "digest": digest_appended}


def prepare(event: dict, root: Path, engram=None) -> dict:
    """Freeze input before any store write so crash retries see identical candidates."""
    kind, payload = event["kind"], event["payload"]
    if kind == "claude_stop":
        return _claude_summary(payload, root, engram)
    if kind == "claude_compact":
        from .auto_absorb_compact import _extract_compact_summary
        path = Path(payload.get("transcript_path", ""))
        summary = _extract_compact_summary(str(path), raise_errors=True)
        if not summary:
            return {"skip": True}
        if len(summary) > 3000:
            summary = summary[:3000] + "\n\n…（已截断）"
        return {"summary": summary}
    text = payload.get("summary", "")
    maximum = 4000 if kind == "cursor_save" else 20_000
    if not text and payload.get("transcript_path"):
        path = Path(payload["transcript_path"])
        try:
            text = cursor._summary_from_transcript(
                str(path), maximum, hook_input={"workspace_roots": payload.get("roots", [])},
                raise_errors=True)
        except cursor.TranscriptReferenceError as exc:
            from .spool import PoisonEvent
            raise PoisonEvent("invalid transcript reference") from exc
    if kind == "cursor_writeback":
        return {"summary": text[-maximum:]}
    cwd = payload.get("project_folder", "")
    event_name = payload.get("event", "stop")
    context = f"[Cursor Hook 自动记录 · {event_name}]\n"
    if cwd:
        context += f"工作目录: {cwd}\n"
    context += "---\n"
    if text:
        context += text[-maximum:]
    else:
        context += "（Cursor 本次事件未携带会话内容 payload，记录最小检查点。）\n"
        if payload.get("hook_cwd"):
            context += f"hook 进程 cwd: {payload['hook_cwd']}\n"
    session = payload.get("session_id", "")
    if not session and not text:
        session = "hook-" + datetime.fromisoformat(event["created_at"]).strftime("%Y-%m-%d")
    return {"context": context, "session_id": session}


def freeze_checkpoint_provenance(event: dict, root: Path, engram=None) -> bool:
    """Capture revision at first deferred preparation, before checkpoint writes.

    This is the observed revision when deferred content is first frozen, not a
    claim about the earlier producer timestamp. Persist it before processing so
    replay cannot adopt a newer checkpoint revision. Older prepared events are
    upgraded at their first drain with this provenance support.
    """
    prepared = event["prepared"]
    project = event["payload"].get("project_folder", "")
    if prepared.get("skip") or not prepared.get("context") or not project or "project_revision" in prepared:
        return False
    if engram is None:
        from ..core import Engram
        engram = Engram(root=root, read_only=True)
    from ..contexts import ContextStoreMixin
    prepared["project_revision"] = ContextStoreMixin._checkpoint_project_revision(engram, project)
    prepared["project_revision_captured_at"] = datetime.now(timezone.utc).isoformat()
    return True


class _EventWriter:
    """Apply native extraction gates while protecting each partially committed row."""
    def __init__(self, engram, event):
        self.engram = engram
        self.event = event

    def __getattr__(self, name):
        return getattr(self.engram, name)

    def _add(self, kind, payload, **kwargs):
        from ..storage import hold_directory_lock
        # Evidence/extraction timestamps can change on retry. The operation is
        # the frozen candidate text in this event, even if an owner later edits it.
        identity = {"text": payload.get("summary" if kind == "lesson" else "title", ""),
                    "project_folder": payload.get("project_folder", "")}
        operation = hashlib.sha256((kind + json.dumps(identity, sort_keys=True, ensure_ascii=True)).encode()).hexdigest()
        with hold_directory_lock(self.engram._knowledge_dir):
            rows = self.engram._read_entries(self.engram._knowledge_dir / (kind + "s.json"), kind)
            rows += self.engram._read_overflow_archive(kind)
            if any(row.get("hook_event_id") == self.event["event_id"] and
                   row.get("hook_operation_id") == operation for row in rows):
                return {"status": "duplicate"}
            value = dict(payload, tier="staging", hook_event_id=self.event["event_id"],
                         hook_operation_id=operation)
            return getattr(self.engram, "add_" + kind)(value, **kwargs)

    def add_lesson(self, payload, **kwargs):
        return self._add("lesson", payload, **kwargs)

    def add_decision(self, payload, **kwargs):
        return self._add("decision", payload, **kwargs)

    def extract(self, summary, **kwargs):
        from ..context import ContextMixin
        return ContextMixin.extract_session_insights(self, summary, force_staging=True, **kwargs)


def _archive(engram, event, content: str, *, daily: bool = False) -> None:
    """Atomic text replacement with embedded marker; a receipt crash cannot append twice."""
    from .spool import _publish
    import portalocker
    from ..contexts import _sanitize_session_id_for_path
    when = datetime.fromisoformat(event["created_at"])
    project = event["payload"].get("project_folder", "")
    session_id = event["prepared"].get("session_id") or "hook-" + event["event_id"]
    session_id = _sanitize_session_id_for_path(session_id, when)
    if daily:
        path = engram._daily_log_path(project, when.strftime("%Y-%m-%d"))
        lock = ".daily.lock"
        header = f"# Daily Log · {when:%Y-%m-%d}\n\n**Project**: {project or '(no-project)'}\n\n"
        entry = f"## {when:%H:%M:%S}  [compact] · {event['client']}\n\n{content}\n\n"
    else:
        path = engram._context_session_path(event["client"], session_id)
        lock = ".engram-write.lock"
        header = f"# Session: {event['client']} @ {when:%Y-%m-%d %H:%M}\n"
        if project:
            header += f"## Project: {project}\n"
        entry = f"\n### {when:%H:%M}\n{content}\n"
    marker = f"<!-- hook-event:{event['event_id']} -->"
    path.parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(path.parent / lock, "a", timeout=5):
        existing = path.read_text(encoding="utf-8") if path.exists() else header
        if marker not in existing:
            _publish(path, (existing + entry + marker + "\n").encode("utf-8"))
    if not daily:
        from ..storage import _update_json, SkipWrite
        digest = engram._build_checkpoint_digest(
            content, tool=event["client"], session_id=session_id, project_folder=project,
            generated_at=event["created_at"], project_revision=event["prepared"].get("project_revision"),
            revision_captured_at=event["prepared"].get("project_revision_captured_at", ""))
        if engram._digest_has_session_signal(digest):
            def replace_digest(existing):
                previous_time = existing.get("generated_at")
                if previous_time:
                    previous_time = datetime.fromisoformat(previous_time.replace("Z", "+00:00"))
                    incoming_time = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
                    if previous_time.tzinfo is None or incoming_time < previous_time:
                        raise SkipWrite()
                return digest
            # Comparison and replacement share the directory/session write lock.
            _update_json(engram._session_digest_path(event["client"], session_id), replace_digest)


def _snapshot(engram, cwd: str) -> None:
    """Preserve the former Stop hook's project snapshot, off the client lifecycle."""
    root = Path(cwd)
    manifest = root / "pyproject.toml"
    if not cwd or not manifest.is_file():
        return
    text = manifest.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    snapshot = {"last_auto_snapshot": datetime.now().isoformat()}
    if match:
        snapshot["version"] = match.group(1)
    source, tests = root / "src", root / "tests"
    if source.is_dir():
        snapshot["module_count"] = sum(1 for p in source.rglob("*.py") if "__pycache__" not in str(p))
    if tests.is_dir():
        snapshot["test_count"] = sum(1 for p in tests.rglob("*.py") if "__pycache__" not in str(p)
                                     for line in p.read_text(encoding="utf-8").splitlines()
                                     if line.lstrip().startswith(("def test_", "async def test_")))
    engram.save_project_snapshot(cwd, snapshot)


def process(event: dict, root: Path, engram=None) -> None:
    prepared = event["prepared"]
    if prepared.get("skip"):
        return
    if engram is None:
        from ..core import Engram
        engram = Engram(root=root)
    kind, payload = event["kind"], event["payload"]
    project = payload.get("project_folder", "")
    if kind == "claude_compact":
        summary = prepared["summary"]
        _archive(engram, event, f"[PostCompact Hook 自动记录]\n工作目录: {project}\n"
                 f"压缩摘要长度: {len(summary)} 字符\n\n{summary}", daily=True)
        return  # archival only, preserving the existing extraction boundary
    if prepared.get("context"):
        _archive(engram, event, prepared["context"])
    if prepared.get("summary"):
        _EventWriter(engram, event).extract(
            prepared["summary"], source_tool=event["client"], source_ref=event["event_id"],
            project_folder=project if kind == "claude_stop" else "",
            capture_origin="hook_content_digest" if prepared.get("digest") else "")
    if kind == "claude_stop":
        if prepared.get("summary"):
            engram.refresh_quick_context()
        _snapshot(engram, project)
