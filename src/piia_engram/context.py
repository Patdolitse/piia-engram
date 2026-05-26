"""Engram context layer — context generation, ingestion, and LLM-based extraction.

ContextMixin provides:
- generate_context: smart cold-start context block for any AI session
- _estimate_tokens: simple CJK/ASCII token estimator
- _infer_domain, _has_content_chars: ingestion helpers
- ingest_notes: parse free-form notes into lessons/decisions
- extract_session_insights: extract knowledge from session summaries

Top-level functions:
- extract_knowledge: LLM-driven structured extraction
- ingest_extraction: apply extracted knowledge to an Engram
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from .storage import (
    DECISION_TRIGGERS,
    DOMAIN_KEYWORDS,
    LESSON_TRIGGERS,
    PLAYBOOK_TRIGGERS,
    STALE_KNOWLEDGE_DAYS,
    _now_iso,
)

if TYPE_CHECKING:  # pragma: no cover - import only for type hints
    from .core import Engram


class ContextMixin:
    """Context generation, token estimation, and ingestion helpers."""

    # ------------------------------------------------------------------
    # Token estimation
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate token count for mixed CJK/ASCII text (no tiktoken dep)."""
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        return cjk + (len(text) - cjk) // 4

    # Section priorities: lower number = higher priority (included first when
    # budget is tight).  Display order is a separate axis so the output reads
    # naturally regardless of which sections survive the budget cut.
    _SECTION_PRIORITY: dict[str, int] = {
        "fragmentation": 0,
        "profile": 1,
        "lessons": 2,
        "decisions": 3,
        "playbooks": 4,
        "preferences": 5,
        "quality": 6,
        "conflicts": 7,
        "project": 8,
        "tools": 9,
        "domains": 10,
        "stale": 11,
        "staging": 12,
        "sync": 13,
    }
    _SECTION_DISPLAY: dict[str, int] = {
        "fragmentation": 0,
        "profile": 1,
        "preferences": 2,
        "quality": 3,
        "domains": 4,
        "tools": 5,
        "lessons": 6,
        "decisions": 7,
        "playbooks": 8,
        "conflicts": 9,
        "project": 10,
        "stale": 11,
        "staging": 12,
        "sync": 13,
    }

    # Tiered context levels for cold-start latency control.
    # quick:    profile + preferences only — pure JSON reads, no scans.
    # standard: + quality, domains, top lessons/decisions, project snapshot.
    # full:     everything (conflicts, stale, staging, auto-sync side effects).
    _LEVEL_SECTIONS: dict[str, set[str] | None] = {
        "quick": {"profile", "preferences"},
        "standard": {"profile", "preferences", "quality", "domains",
                     "tools", "lessons", "decisions", "playbooks", "project"},
        "full": None,  # None means "include all sections"
    }

    # ------------------------------------------------------------------
    # Domain inference / content helpers
    # ------------------------------------------------------------------

    def _infer_domain(self, text: str, fallback: str = "") -> str:
        """Infer domain(s) from text. Returns comma-separated if multiple match."""
        text_lower = text.lower()
        matched = [
            domain
            for domain, keywords in DOMAIN_KEYWORDS.items()
            if any(kw in text_lower for kw in keywords)
        ]
        if matched:
            return ",".join(matched)
        return fallback

    def _has_content_chars(self, text: str) -> bool:
        return any(ch.isalnum() for ch in text)

    # ------------------------------------------------------------------
    # Free-form ingestion
    # ------------------------------------------------------------------

    def ingest_notes(self, text: str, source_tool: str = "", domain: str = "") -> dict:
        """Parse free-form notes and extract lesson/decision candidates."""
        lines = text.splitlines()
        saved_lessons = saved_decisions = duplicates = skipped = 0
        results = []

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if len(line) < 5 or not self._has_content_chars(line):
                skipped += 1
                results.append({
                    "type": "unknown",
                    "status": "skipped",
                    "reason": "too short",
                    "text": line,
                })
                continue

            line_lower = line.lower()
            is_decision = any(trigger in line_lower for trigger in DECISION_TRIGGERS)
            is_lesson = any(trigger in line_lower for trigger in LESSON_TRIGGERS)
            if not is_decision and not is_lesson and len(line) <= 15:
                skipped += 1
                results.append({
                    "type": "unknown",
                    "status": "skipped",
                    "reason": "too short",
                    "text": line,
                })
                continue

            item_domain = self._infer_domain(line, domain)
            if is_decision:
                result = self.add_decision({
                    "title": line,
                    "choice": "",
                    "domain": item_domain,
                    "source_tool": source_tool,
                })
                if result.get("status") == "duplicate":
                    duplicates += 1
                    results.append({
                        "type": "decision",
                        "status": "duplicate",
                        "title": line,
                        "existing_id": result.get("existing_id"),
                        "domain": item_domain,
                    })
                else:
                    saved_decisions += 1
                    results.append({
                        "type": "decision",
                        "status": "saved",
                        "id": result.get("id", ""),
                        "title": self._entry_identity_text(result, "decision") or line,
                        "domain": item_domain,
                    })
            else:
                result = self.add_lesson({
                    "summary": line,
                    "domain": item_domain,
                    "source_tool": source_tool,
                })
                if result.get("status") == "duplicate":
                    duplicates += 1
                    results.append({
                        "type": "lesson",
                        "status": "duplicate",
                        "summary": line,
                        "existing_id": result.get("existing_id"),
                        "domain": item_domain,
                    })
                else:
                    saved_lessons += 1
                    results.append({
                        "type": "lesson",
                        "status": "saved",
                        "id": result.get("id", ""),
                        "summary": result.get("summary", line),
                        "domain": item_domain,
                    })

        parsed = saved_lessons + saved_decisions + duplicates
        return {
            "total_lines": len(lines),
            "parsed": parsed,
            "saved_lessons": saved_lessons,
            "saved_decisions": saved_decisions,
            "duplicates": duplicates,
            "skipped": skipped,
            "results": results,
        }

    def extract_session_insights(
        self,
        summary: str,
        source_tool: str = "",
    ) -> dict:
        """Extract lessons and decisions from a free-form session summary."""
        if not summary or not summary.strip():
            return {
                "saved_lessons": 0,
                "saved_decisions": 0,
                "duplicates": 0,
                "skipped": 0,
                "results": [],
            }

        sentences = re.split(r"[。！？.!?\n]+", summary)
        saved_lessons = saved_decisions = duplicates = skipped = 0
        results = []

        for raw in sentences:
            sentence = raw.strip()
            if not sentence or len(sentence) < 8:
                skipped += 1
                continue
            if not self._has_content_chars(sentence):
                skipped += 1
                continue

            sentence_lower = sentence.lower()
            is_decision = any(trigger in sentence_lower for trigger in DECISION_TRIGGERS)
            is_lesson = any(trigger in sentence_lower for trigger in LESSON_TRIGGERS)

            if not is_decision and not is_lesson:
                if re.search(
                    r"(应该|需要|必须|建议|最好|注意|避免|不要|要|should|must|need|avoid|remember|make sure)",
                    sentence_lower,
                ):
                    is_lesson = True
                elif re.search(
                    r"(因此|所以|最终|改为|使用|采用|选择了|therefore|so we|decided to|chose|switched)",
                    sentence_lower,
                ):
                    is_decision = True

            if not is_decision and not is_lesson:
                skipped += 1
                results.append({"status": "skipped", "text": sentence[:80]})
                continue

            item_domain = self._infer_domain(sentence)
            if is_decision:
                result = self.add_decision({
                    "title": sentence,
                    "choice": "",
                    "domain": item_domain,
                    "source_tool": source_tool,
                    "tier": "staging",
                })
                if result.get("status") == "duplicate":
                    duplicates += 1
                    results.append({
                        "type": "decision",
                        "status": "duplicate",
                        "title": sentence[:80],
                        "existing_id": result.get("existing_id"),
                    })
                else:
                    saved_decisions += 1
                    results.append({
                        "type": "decision",
                        "status": "saved",
                        "id": result.get("id", ""),
                        "title": sentence[:80],
                        "domain": item_domain,
                    })
            else:
                result = self.add_lesson({
                    "summary": sentence,
                    "domain": item_domain,
                    "source_tool": source_tool,
                    "tier": "staging",
                })
                if result.get("status") == "duplicate":
                    duplicates += 1
                    results.append({
                        "type": "lesson",
                        "status": "duplicate",
                        "summary": sentence[:80],
                        "existing_id": result.get("existing_id"),
                    })
                else:
                    saved_lessons += 1
                    results.append({
                        "type": "lesson",
                        "status": "saved",
                        "id": result.get("id", ""),
                        "summary": sentence[:80],
                        "domain": item_domain,
                    })

        return {
            "saved_lessons": saved_lessons,
            "saved_decisions": saved_decisions,
            "duplicates": duplicates,
            "skipped": skipped,
            "results": results,
        }

    # ------------------------------------------------------------------
    # Playbook auto-extraction from session data
    # ------------------------------------------------------------------

    # Sequential markers that indicate step-by-step procedures
    _SEQ_MARKERS = [
        "先", "然后", "接着", "最后", "之后", "随后", "完成后", "接下来",
        "first", "then", "next", "finally", "after that", "subsequently",
        "step 1", "step 2", "step 3",
    ]
    # Action verbs that indicate operational (not analytical) work
    _ACTION_VERBS = [
        "安装", "配置", "部署", "发布", "执行", "运行", "创建", "更新",
        "下载", "编译", "构建", "推送", "上传", "提交", "打包", "上架",
        "install", "configure", "deploy", "publish", "execute", "run",
        "create", "update", "download", "build", "push", "upload",
        "commit", "package", "release",
    ]
    # Pitfall indicators
    _PITFALL_MARKERS = [
        "踩坑", "报错", "失败", "不行", "不能", "不被接受", "需要改",
        "注意", "小心", "陷阱", "坑",
        "error", "failed", "rejected", "gotcha", "caveat", "workaround",
    ]

    # Sensitive-info patterns for redaction before storing playbooks.
    # Compiled once at class level for performance.
    _SENSITIVE_PATTERNS: list[tuple["re.Pattern[str]", str]] = [
        # API keys / tokens (sk-..., Bearer ..., token=..., key=...)
        (re.compile(
            r'(sk-|api[_\-]?key\s*[=:]\s*|token\s*[=:]\s*|Bearer\s+|password\s*[=:]\s*)'
            r'[A-Za-z0-9._\-]{16,}',
            re.IGNORECASE,
        ), r'\1{{REDACTED}}'),
        # Absolute Windows paths  (C:\Users\... etc.)
        (re.compile(r'[A-Za-z]:\\(?:[^\s,;，。！？]+)'), '{{PATH}}'),
        # Absolute Unix paths (/home/..., /Users/..., /var/... etc.)
        (re.compile(r'/(?:home|Users|var|etc|opt|tmp|root)/[^\s,;，。！？]+'), '{{PATH}}'),
        # Email addresses
        (re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}'), '{{EMAIL}}'),
        # Env-var secrets  (SECRET_KEY=xxx, AUTH_TOKEN=xxx etc.)
        (re.compile(
            r'((?:SECRET|PASSWORD|TOKEN|CREDENTIAL|AUTH)[_A-Z]*)\s*[=:]\s*\S+',
            re.IGNORECASE,
        ), r'\1={{REDACTED}}'),
    ]

    @classmethod
    def _redact_sensitive(cls, text: str) -> str:
        """Strip tokens, passwords, absolute paths, and emails from text."""
        if not text:
            return text
        for pattern, replacement in cls._SENSITIVE_PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    @staticmethod
    def _count_matches(text_lower: str, wordlist: list[str]) -> int:
        """Count keyword matches with proper word boundaries.

        Chinese terms use substring matching (each char is a word).
        ASCII terms use ``\\b`` word boundaries to prevent "run" matching
        inside "runbook", etc.
        """
        count = 0
        for word in wordlist:
            if word.isascii():
                if re.search(rf'\b{re.escape(word)}\b', text_lower):
                    count += 1
            else:
                if word in text_lower:
                    count += 1
        return count

    def _detect_procedural_workflow(self, text: str) -> bool:
        """Check if text describes a multi-step operational workflow.

        Only uses text signals (sequential markers + action verbs).
        Checkpoint-based detection is handled by the caller
        (extract_playbook_from_session) which bypasses this method entirely
        when enough checkpoints exist.
        """
        if not text or len(text) < 20:
            return False
        text_lower = text.lower()
        seq_count = self._count_matches(text_lower, self._SEQ_MARKERS)
        action_count = self._count_matches(text_lower, self._ACTION_VERBS)
        trigger_count = self._count_matches(text_lower, list(PLAYBOOK_TRIGGERS))
        # Rule 1: sequential markers + action verbs → procedure
        if seq_count >= 2 and action_count >= 3:
            return True
        # Rule 2: playbook trigger keywords MUST be backed by at least one
        # action verb that is NOT itself a trigger — prevents "讨论了发布流程"
        # (where 发布 is both trigger and verb) from double-counting.
        trigger_set = set(PLAYBOOK_TRIGGERS)
        non_trigger_verbs = [v for v in self._ACTION_VERBS if v not in trigger_set]
        non_trigger_actions = self._count_matches(text_lower, non_trigger_verbs)
        if trigger_count >= 3 and non_trigger_actions >= 1:
            return True
        return False

    def _extract_steps_from_actions(self, session_content: str) -> list[dict]:
        """Extract ordered steps from structured #### Actions blocks in context files."""
        steps = []
        # Actions blocks are formatted as:
        # #### Actions
        # 1. `tool_name` — args_summary → result_summary
        action_blocks = re.findall(
            r"####\s+Actions\n((?:\d+\.\s+`.+`.*\n?)+)",
            session_content,
        )
        order = 0
        for block in action_blocks:
            for line in block.strip().splitlines():
                m = re.match(
                    r"\d+\.\s+`([^`]+)`(?:\s*[—–-]\s*(.+?))?(?:\s*→\s*(.+))?$",
                    line.strip(),
                )
                if m:
                    tool_called = m.group(1)
                    args_summary = (m.group(2) or "").strip()
                    action_text = tool_called
                    if args_summary:
                        action_text = f"{tool_called}: {args_summary}"
                    order += 1
                    steps.append({"order": order, "action": action_text[:200]})
        return steps

    def _extract_steps_from_checkpoints(self, session_content: str) -> list[dict]:
        """Extract ordered steps from context checkpoint content."""
        steps = []
        # Checkpoints have "### HH:MM" headers with content like
        # "已完成：..." or "当前任务：..." or free text
        sections = re.split(r"###\s+\d{1,2}:\d{2}", session_content)
        order = 0
        for section in sections:
            section = section.strip()
            if not section:
                continue
            # Look for "已完成" items
            completed = re.findall(
                r"(?:已完成|完成|done|completed)[：:]\s*(.+?)(?:\n|$)",
                section, re.IGNORECASE,
            )
            for item in completed:
                item = item.strip().rstrip("。.")
                if item and len(item) > 3:
                    order += 1
                    steps.append({"order": order, "action": item[:200]})
        return steps

    def _extract_steps_from_summary(self, summary: str) -> list[dict]:
        """Extract ordered steps from summary text by splitting on sequential markers."""
        steps = []
        # Try numbered list first: "1. xxx 2. xxx 3. xxx"
        numbered = re.findall(
            r"(?:^|\n)\s*(\d+)[.、）)]\s*(.+?)(?=\n\s*\d+[.、）)]|\n\n|$)",
            summary, re.DOTALL,
        )
        if len(numbered) >= 3:
            for order_str, action in numbered:
                action = action.strip().rstrip("。.")
                if action and len(action) > 3:
                    steps.append({"order": int(order_str), "action": action[:200]})
            return steps

        # Split by sequential markers
        marker_pattern = "|".join(re.escape(m) for m in self._SEQ_MARKERS if len(m) > 1)
        segments = re.split(
            rf"[,，。.;；]\s*(?:{marker_pattern})",
            summary, flags=re.IGNORECASE,
        )
        order = 0
        for seg in segments:
            seg = seg.strip().rstrip("。.")
            # Must contain an action verb to be a step
            seg_lower = seg.lower()
            has_action = any(v in seg_lower for v in self._ACTION_VERBS)
            if seg and len(seg) > 5 and has_action:
                order += 1
                steps.append({"order": order, "action": seg[:200]})
        return steps

    def _extract_pitfalls(self, text: str) -> list[str]:
        """Extract pitfall/caveat sentences from text."""
        pitfalls = []
        sentences = re.split(r"[。！？.!?\n]+", text)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence or len(sentence) < 8:
                continue
            sentence_lower = sentence.lower()
            if any(m in sentence_lower for m in self._PITFALL_MARKERS):
                pitfalls.append(sentence[:300])
        return pitfalls[:5]  # cap at 5

    def _infer_playbook_title(self, summary: str, steps: list[dict]) -> str:
        """Infer a concise playbook title from summary and steps."""
        # Take the first line/sentence as candidate
        first_line = summary.strip().split("\n")[0].strip()
        # Remove common prefixes
        for prefix in ["完成", "本次完成", "已完成", "finished", "completed"]:
            if first_line.lower().startswith(prefix):
                first_line = first_line[len(prefix):].lstrip("了：:, ")
        # If too long, truncate
        if len(first_line) > 60:
            first_line = first_line[:57] + "..."
        return first_line or "操作流程"

    def _extract_triggers_from_text(self, text: str) -> list[str]:
        """Extract trigger keywords from text for playbook retrieval."""
        triggers = []
        text_lower = text.lower()
        # Add matched PLAYBOOK_TRIGGERS
        for t in PLAYBOOK_TRIGGERS:
            if t in text_lower and t not in triggers:
                triggers.append(t)
        # Add domain keywords
        domain = self._infer_domain(text)
        if domain:
            for d in domain.split(","):
                d = d.strip()
                if d and d not in triggers:
                    triggers.append(d)
        return triggers[:8]

    def extract_playbook_from_session(
        self,
        summary: str,
        source_tool: str = "",
        session_id: str = "",
    ) -> dict | None:
        """Try to auto-extract a Playbook draft from session data.

        Detection priority:
        1. Checkpoints (save_agent_context) ≥ 3 → high confidence, skip text scan
        2. Text-based detection (sequential markers + action verbs) → medium confidence

        Returns the saved playbook dict (tier=staging, with confidence field)
        if a procedural workflow is detected, otherwise None.
        """
        if not summary or not summary.strip():
            return None

        # ── Step 0: Check kill-switch ───────────────────────────────
        prefs = self.get_preferences()
        if prefs.get("playbook_auto_extract") is False:
            return None

        # ── Step 1: Collect evidence — structured actions > checkpoints > text ──
        action_steps: list[dict] = []
        checkpoint_steps: list[dict] = []
        if session_id and source_tool:
            try:
                sessions = self.get_recent_context(tool=source_tool, limit=5)
                for s in sessions:
                    if s.get("session_id") == session_id:
                        session_content = s.get("content", "")
                        # Structured actions are highest fidelity
                        action_steps = self._extract_steps_from_actions(session_content)
                        if not action_steps:
                            checkpoint_steps = self._extract_steps_from_checkpoints(
                                session_content
                            )
                        break
            except Exception:
                pass  # session data is optional

        # ── Step 2: Detection — structured actions > checkpoints > text ──
        confidence = "none"
        if len(action_steps) >= 3:
            # Highest evidence: actual tool call sequences
            confidence = "high"
        elif len(checkpoint_steps) >= 3:
            # Strong evidence: execution checkpoints with timestamps
            confidence = "high"
        elif self._detect_procedural_workflow(summary):
            # Weaker evidence: text pattern matching on summary
            confidence = "medium"

        if confidence == "none":
            return None

        # ── Step 3: Extract steps ───────────────────────────────────
        if len(action_steps) >= 3:
            steps = action_steps
        elif len(checkpoint_steps) >= 3:
            steps = checkpoint_steps
        else:
            steps = self._extract_steps_from_summary(summary)

        if len(steps) < 3:
            return None

        # ── Step 4: Extract metadata ────────────────────────────────
        title = self._infer_playbook_title(summary, steps)
        triggers = self._extract_triggers_from_text(summary)
        pitfalls = self._extract_pitfalls(summary)
        domain = self._infer_domain(summary)

        # ── Step 5: Redact sensitive info before persisting ─────────
        steps = [
            {"order": s["order"], "action": self._redact_sensitive(s["action"])}
            for s in steps
        ]
        pitfalls = [self._redact_sensitive(p) for p in pitfalls]
        description = self._redact_sensitive(summary[:500])

        playbook = {
            "title": title,
            "triggers": triggers,
            "steps": steps,
            "description": description,
            "domain": domain,
            "pitfalls": pitfalls,
            "source_tool": source_tool,
            "tier": "staging",
            "confidence": confidence,
        }

        # Save via add_playbook (inherits duplicate detection)
        result = self.add_playbook(playbook, source_tool=source_tool)

        if result.get("status") == "duplicate":
            # Cross-session merge: if the existing playbook is staging, merge instead
            existing_id = result.get("existing_id")
            if existing_id:
                existing_pb = self._read_playbook_by_id(existing_id)
                if existing_pb and existing_pb.get("tier") == "staging":
                    merged = self.merge_playbooks(existing_id, playbook)
                    if not merged.get("error"):
                        merged["confidence"] = confidence
                        return merged
            return None
        if result.get("error"):
            return None

        # ── Step 6: Auto-link related knowledge ────────────────────
        pb_id = result.get("id")
        if pb_id and title:
            self._auto_link_playbook_knowledge(pb_id, title)

        return result

    def _auto_link_playbook_knowledge(self, playbook_id: str, playbook_title: str) -> None:
        """Auto-link a playbook to recently added lessons/decisions with similar titles."""
        try:
            lessons = self.get_lessons(limit=10, _update_access=False)
            decisions = self.get_decisions(limit=10, _update_access=False)
            for item in lessons + decisions:
                item_text = item.get("summary") or item.get("question") or item.get("title") or ""
                if item_text and self._bigram_similarity(playbook_title, item_text) > 0.3:
                    item_id = item.get("id")
                    if item_id:
                        self.link_knowledge(playbook_id, item_id)
        except Exception:
            pass  # Auto-linking is best-effort

    # ------------------------------------------------------------------
    # Smart cold-start context generation
    # ------------------------------------------------------------------

    def generate_context(
        self,
        project_folder: str | None = None,
        max_tokens: int | None = None,
        level: str = "full",
    ) -> str:
        """Generate a concise context block that any AI can consume.

        This is the magic moment — inject this into any AI's system prompt
        and it immediately "knows" you.

        Args:
            project_folder: Optional project path for project-specific context.
            max_tokens: Optional token budget.  When set, sections are included
                by priority until the budget is exhausted.  Lower-priority
                sections are dropped first.  When *None* (default), all
                sections are included (backward-compatible).
            level: Context detail tier — "quick" | "standard" | "full".
                - "quick": profile + preferences only (pure JSON reads,
                  no filesystem scans). Use for low-latency cold start.
                - "standard": adds quality, domains, top lessons/decisions,
                  and project snapshot. Skips expensive reconciliation.
                - "full" (default): everything, including conflict detection,
                  stale/staging warnings, and auto-reconcile side effects.
                Backward-compatible: defaults to "full" so existing callers
                see no behaviour change.
        """
        # Normalise level; unknown values fall back to full for safety.
        level = (level or "full").lower()
        if level not in self._LEVEL_SECTIONS:
            level = "full"
        allowed = self._LEVEL_SECTIONS[level]

        def _wants(section: str) -> bool:
            """True iff this section should be built at the current level."""
            return allowed is None or section in allowed

        # ── Build each section independently ──────────────────────────
        sections: dict[str, str] = {}

        # Data fragmentation warning — surface before any content.
        if getattr(self, "data_orphans", None):
            lines = [
                "## ⚠ 数据碎片警告",
                f"当前数据目录: `{self.root}`",
                "以下路径也包含 Engram 数据，但**未被读取**:",
            ]
            for orphan in self.data_orphans:
                lines.append(f"- `{orphan}`")
            lines.append("")
            lines.append("这意味着部分经验教训或身份信息可能丢失。")
            lines.append("请运行 `engram doctor` 或手动合并后删除多余目录。")
            sections["fragmentation"] = "\n".join(lines)

        # Profile
        profile = self.get_safe_profile()
        plines: list[str] = ["## 关于用户"]
        if profile:
            if profile.get("role"):
                plines.append(f"- 角色: {profile['role']}")
            if profile.get("language"):
                plines.append(f"- 沟通语言: {profile['language']}")
            if profile.get("technical_level"):
                plines.append(f"- 技术水平: {profile['technical_level']}")
            if profile.get("description"):
                plines.append(f"- 简介: {profile['description']}")
        else:
            plines.append("- ⚠ 身份画像未设置。")
            plines.append("- 你可以通过对话了解用户并调用 `update_identity` 设置画像（role、language、technical_level、description）。")
            plines.append("- 或者建议用户运行 `engram setup` 完成引导式设置。")
            plines.append("- 设置后，所有 AI 工具都能从第一条消息开始就了解这位用户。")
        sections["profile"] = "\n".join(plines)

        # Preferences
        prefs = self.get_preferences()
        if prefs:
            pr: list[str] = ["\n## 工作偏好"]
            if prefs.get("work_patterns"):
                for k, v in list(prefs["work_patterns"].items())[:8]:
                    pr.append(f"- {k}: {v}")
            if prefs.get("communication"):
                pr.append(f"- 沟通偏好: {prefs['communication']}")
            if prefs.get("tool_preferences"):
                pr.append("- 工具偏好:")
                for k, v in list(prefs["tool_preferences"].items())[:4]:
                    pr.append(f"  - {k}: {v}")
            sections["preferences"] = "\n".join(pr)

        # Quality standards
        if _wants("quality"):
            standards = self.get_quality_standards()
            if standards:
                qs: list[str] = ["\n## 质量标准"]
                if standards.get("acceptance_threshold"):
                    qs.append(f"- 验收严格度: {standards['acceptance_threshold']}/5")
                if standards.get("rules"):
                    for rule in standards["rules"][:5]:
                        qs.append(f"- {rule}")
                sections["quality"] = "\n".join(qs)

        # Domains
        if _wants("domains"):
            domains = self.get_domains()
            if domains:
                dl: list[str] = ["\n## 经验领域"]
                sorted_domains = sorted(
                    domains.items(),
                    key=lambda x: x[1].get("project_count", 0),
                    reverse=True,
                )
                for name, info in sorted_domains[:6]:
                    count = info.get("project_count", 0)
                    dl.append(f"- {name}: {count} 个项目经验")
                sections["domains"] = "\n".join(dl)

        # Environment tools summary
        if _wants("tools"):
            tools = self.list_tools()
            if tools:
                tl: list[str] = ["\n## 本地工具环境"]
                for t in tools[:10]:
                    line = f"- {t.get('name', '?')}"
                    if t.get("version"):
                        line += f" v{t['version']}"
                    if t.get("path"):
                        line += f" → `{t['path']}`"
                    if t.get("notes"):
                        line += f"  ⚠ {t['notes']}"
                    tl.append(line)
                if len(tools) > 10:
                    tl.append(f"- ...及其他 {len(tools) - 10} 个工具（用 list_tools 查看完整列表）")
                sections["tools"] = "\n".join(tl)

        # Lessons — _update_access=False: context injection ≠ user review
        # Note: lessons/decisions variables are reused by the "conflicts" section,
        # so we initialise them as empty lists when skipped to keep that logic safe.
        if _wants("lessons"):
            lessons = self.get_relevant_lessons(
                project_folder=project_folder, limit=8, _update_access=False
            )
            if lessons:
                ll: list[str] = ["\n## 相关经验教训（请在开发中主动避免）"]
                for l in lessons:
                    ll.append(f"- {l.get('summary', '')}")
                sections["lessons"] = "\n".join(ll)
        else:
            lessons = []

        # Decisions
        if _wants("decisions"):
            decisions = self.get_decisions(limit=6, _update_access=False)
            if decisions:
                dc: list[str] = ["\n## 已做的关键决策（请遵循）"]
                for d in decisions:
                    question = d.get("question") or d.get("title") or ""
                    choice = d.get("choice", "")
                    if question and choice:
                        dc.append(f"- {question} → {choice}")
                    elif question:
                        dc.append(f"- {question}")
                    elif choice:
                        dc.append(f"- {choice}")
                sections["decisions"] = "\n".join(dc)
        else:
            decisions = []

        # Recent playbooks
        if _wants("playbooks"):
            recent_pbs = self.get_recent_playbooks(limit=5)
            if recent_pbs:
                pb_lines: list[str] = ["\n## 近期操作手册"]
                for pb in recent_pbs:
                    title = pb.get("title", "")
                    triggers = ", ".join(pb.get("triggers", []))
                    params = pb.get("parameters", [])
                    line = f"- {title}"
                    if triggers:
                        line += f"（触发: {triggers}）"
                    if params:
                        line += f" [参数: {', '.join(params)}]"
                    pb_lines.append(line)
                sections["playbooks"] = "\n".join(pb_lines)

        # Conflicts
        if _wants("conflicts"):
            conflict_items: list[str] = []
            if decisions:
                for c in self._detect_decision_conflicts(decisions):
                    conflict_items.append(
                        f"- 决策冲突: 「{c['q1']}→{c['c1']}」与「{c['q2']}→{c['c2']}」可能矛盾，请与用户确认以哪个为准"
                    )
            if lessons:
                for c in self._detect_lesson_conflicts(lessons):
                    conflict_items.append(
                        f"- 经验冲突: 「{c['s1'][:30]}」与「{c['s2'][:30]}」给出矛盾建议，请与用户确认"
                    )
            if conflict_items:
                sections["conflicts"] = "\n## 知识冲突提醒\n" + "\n".join(conflict_items)

        # Project history
        if _wants("project") and project_folder:
            proj = self.get_project_snapshot(project_folder)
            if proj:
                ph: list[str] = ["\n## 当前项目历史"]
                if proj.get("title"):
                    ph.append(f"- 项目: {proj['title']}")
                if proj.get("session_count"):
                    ph.append(f"- 已协作 {proj['session_count']} 次")
                if proj.get("tech_stack"):
                    ph.append(f"- 技术栈: {', '.join(proj['tech_stack'])}")
                if proj.get("known_issues"):
                    ph.append("- 已知问题:")
                    for issue in proj["known_issues"][:3]:
                        ph.append(f"  - {issue}")
                sections["project"] = "\n".join(ph)

        # Stale warning
        if _wants("stale"):
            stale = self.get_stale_knowledge(days=STALE_KNOWLEDGE_DAYS, limit=None)
            stale_count = len(stale["lessons"]) + len(stale["decisions"])
            if stale_count > 5:
                sections["stale"] = (
                    "\n## stale_knowledge_warning\n"
                    f"- 有 {stale_count} 条知识超过 {STALE_KNOWLEDGE_DAYS} 天未复习，建议运行 get_stale_knowledge 查看。"
                )

        # Staging reminder
        if _wants("staging"):
            staging = self.get_staging_summary()
            if staging["total_staging"] > 10:
                sections["staging"] = (
                    "\n## staging_review_reminder\n"
                    f"- 有 {staging['total_staging']} 条自动导入的知识尚未审核。"
                    " 建议运行 review_knowledge 查看并确认或归档。"
                )

        # Auto-reconcile (filesystem-scanning side effects — only at "full" level).
        # Skipping these is the main latency win for quick/standard cold start.
        if _wants("sync"):
            sync_msgs: list[str] = []
            try:
                reconcile = self.reconcile_memories()
                if reconcile["imported"] > 0:
                    sync_msgs.append(
                        f"- 记忆同步：导入了 {reconcile['imported']} 条外部 AI 记忆"
                        f"（来源：{', '.join(reconcile['sources'][:5])}）"
                    )
            except Exception as exc:
                logger.warning("reconcile_memories failed: %s", exc)

            try:
                cfg_sync = self.reconcile_ai_configs()
                if cfg_sync["imported"] > 0:
                    sync_msgs.append(
                        f"- 配置对齐：从 {cfg_sync['scanned_files']} 个 AI 配置文件"
                        f"导入了 {cfg_sync['imported']} 条规则"
                        f"（来源：{', '.join(cfg_sync['sources'][:5])}）"
                    )
            except Exception as exc:
                logger.warning("reconcile_ai_configs failed: %s", exc)

            if sync_msgs:
                sections["sync"] = "\n## auto_sync\n" + "\n".join(sync_msgs)

        # ── Assemble ──────────────────────────────────────────────────
        if not sections:
            return ""

        if max_tokens is None:
            # No budget — include all, display order
            parts = sorted(sections.items(), key=lambda kv: self._SECTION_DISPLAY.get(kv[0], 99))
            return "\n".join(text for _, text in parts)

        # Budget-limited — include by priority until exhausted
        budget = max_tokens
        included: list[tuple[int, str]] = []
        by_priority = sorted(sections.items(), key=lambda kv: self._SECTION_PRIORITY.get(kv[0], 99))
        for key, text in by_priority:
            cost = self._estimate_tokens(text)
            if cost <= budget:
                included.append((self._SECTION_DISPLAY.get(key, 99), text))
                budget -= cost
        # Re-sort to display order
        included.sort()
        return "\n".join(text for _, text in included)

    # ------------------------------------------------------------------
    # Quick-context snapshot file (cross-tool / offline fallback)
    # ------------------------------------------------------------------

    def refresh_quick_context(
        self,
        target: "Path | None" = None,
        level: str = "standard",
    ) -> "Path":
        """Write a portable cold-start snapshot to ``<root>/quick_context.md``.

        Any AI tool — even one without the Engram MCP server connected —
        can `Read` this file to get the user's identity, preferences, and
        top knowledge. Default level is "standard" so the file stays under
        a few KB and free of expensive reconcile output.

        Args:
            target: Override output path. Defaults to ``self.root / "quick_context.md"``.
            level: Detail tier for the snapshot ("quick" | "standard" | "full").
                Defaults to "standard" — covers most cold-start needs.

        Returns:
            Path to the written file.
        """
        from datetime import datetime
        import os as _os
        import tempfile as _tempfile
        from pathlib import Path as _Path

        body = self.generate_context(level=level)
        timestamp = datetime.now().isoformat(timespec="seconds")
        content = (
            f"<!-- Engram quick_context snapshot — level={level} — generated {timestamp} -->\n"
            f"<!-- This file is regenerated by Engram. Editing it has no effect. -->\n\n"
            f"{body}\n"
        )

        path = _Path(target) if target else (self.root / "quick_context.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp file in same dir + os.replace
        fd, tmp_name = _tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        try:
            with _os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                _os.fsync(f.fileno())
            _os.replace(tmp_name, path)
        except Exception:
            try:
                _Path(tmp_name).unlink()
            except OSError:
                pass
            raise
        return path


# ---------------------------------------------------------------------------
# LLM-driven knowledge extraction
# ---------------------------------------------------------------------------

# Prompt for extracting knowledge from a conversation
EXTRACTION_PROMPT = """分析以下 AI 协作对话，从中提取用户的个人知识。

## 对话内容
{conversation}

## 项目信息
- 项目文件夹: {project_folder}
- 项目文件: {project_files}

## 要提取的内容

请从对话中识别以下信息（只提取能观察到的，不要推测）：

1. **用户偏好** — 用户表达了哪些偏好？（技术选择、方案倾向、沟通方式等）
2. **质量标准** — 用户对结果有什么要求？（验收标准、修改要求、不满意的点）
3. **教训** — 过程中遇到了什么问题？用户将来应该避免什么？
4. **决策** — 用户做了哪些选择？为什么选A不选B？
5. **技能信号** — 用户展示了哪些领域的知识？哪些领域明确不熟悉？
6. **工作风格** — 用户倾向简单方案还是复杂方案？一步到位还是渐进式？

## 返回格式（JSON）

{{
  "profile_updates": {{
    "language": "用户使用的语言",
    "technical_level": "非技术/初级/中级/高级",
    "description": "一句话描述用户"
  }},
  "work_style_updates": {{
    "preferences": {{"偏好名": "偏好值"}},
    "communication": "沟通风格描述"
  }},
  "quality_updates": {{
    "acceptance_threshold": 1-5,
    "rules": ["规则1", "规则2"]
  }},
  "lessons": [
    {{"summary": "一句话教训", "detail": "详细说明", "domain": "领域"}}
  ],
  "decisions": [
    {{"question": "决策问题", "choice": "选择", "reasoning": "理由"}}
  ],
  "domains_used": ["python", "frontend"],
  "project_info": {{
    "title": "项目简短标题",
    "tech_stack": ["python", "html"],
    "known_issues": ["已知问题1"]
  }}
}}

只返回 JSON，不要其他文字。只提取实际观察到的内容，没有的字段留空或省略。"""


def extract_knowledge(
    conversation: list[dict],
    project_folder: str,
    project_files: str,
    provider=None,
) -> dict | None:
    """Extract structured knowledge from a conversation using LLM.

    Returns a dict with extracted knowledge, or None on failure.
    """
    if not provider or not conversation:
        return None

    # Build conversation text (last 20 messages to stay within context)
    recent = conversation[-20:]
    conv_text = ""
    for msg in recent:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")[:500]
        conv_text += f"[{role}]: {content}\n\n"

    prompt = EXTRACTION_PROMPT.format(
        conversation=conv_text[:4000],
        project_folder=project_folder,
        project_files=project_files[:500],
    )

    try:
        messages = [{"role": "user", "content": prompt}]
        raw = provider.chat(messages, project_folder)

        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as exc:
        logger.warning("extract_knowledge LLM call failed: %s", exc)
    return None


def ingest_extraction(engram: "Engram", extracted: dict,
                      project_folder: str, session_id: str = "") -> dict:
    """Apply extracted knowledge to the Engram. Returns a summary of what was learned."""
    learned: list[str] = []
    source = {"project": project_folder, "session": session_id, "time": _now_iso()}

    # Profile updates
    profile_updates = extracted.get("profile_updates", {})
    if profile_updates:
        # Only update non-empty values
        clean = {k: v for k, v in profile_updates.items() if v}
        if clean:
            engram.update_profile(clean)
            learned.append(f"了解到你的基本信息（{', '.join(clean.keys())}）")

    # Work style updates
    style_updates = extracted.get("work_style_updates", {})
    if style_updates:
        clean = {}
        if style_updates.get("preferences"):
            clean["preferences"] = style_updates["preferences"]
        if style_updates.get("communication"):
            clean["communication"] = style_updates["communication"]
        if clean:
            engram.update_work_style(clean)
            learned.append("了解到你的工作风格偏好")

    # Quality standards
    quality = extracted.get("quality_updates", {})
    if quality:
        clean = {}
        if quality.get("acceptance_threshold"):
            try:
                clean["acceptance_threshold"] = int(quality["acceptance_threshold"])
            except (ValueError, TypeError):
                pass
        if quality.get("rules"):
            existing = engram.get_quality_standards()
            existing_rules = set(existing.get("rules", []))
            new_rules = [r for r in quality["rules"] if r not in existing_rules]
            if new_rules:
                all_rules = list(existing_rules) + new_rules
                clean["rules"] = all_rules[-15:]  # keep last 15 rules
        if clean:
            engram.update_quality_standards(clean)
            learned.append("更新了你的质量标准")

    # Lessons
    lessons = extracted.get("lessons", [])
    for l in lessons[:5]:
        if isinstance(l, dict) and l.get("summary"):
            l["source_project"] = project_folder
            l["source_session"] = session_id
            engram.add_lesson(l)
            learned.append(f"记住了教训: {l['summary'][:40]}")

    # Decisions
    decisions = extracted.get("decisions", [])
    for d in decisions[:5]:
        if isinstance(d, dict) and d.get("question"):
            d["source_project"] = project_folder
            d["source_session"] = session_id
            engram.add_decision(d)
            learned.append(f"记录了决策: {d['question'][:40]}")

    # Domain usage
    domains = extracted.get("domains_used", [])
    for domain in domains[:5]:
        if isinstance(domain, str) and domain:
            engram.increment_domain_usage(domain)

    # Project snapshot
    proj_info = extracted.get("project_info", {})
    if proj_info:
        existing = engram.get_project_snapshot(project_folder)
        session_count = existing.get("session_count", 0) + 1
        proj_info["session_count"] = session_count
        engram.save_project_snapshot(project_folder, proj_info)

    return {
        "items_learned": len(learned),
        "summary": learned,
    }
