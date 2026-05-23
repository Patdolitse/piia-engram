"""Engram retrieval layer — search, scoring, tokenization, batch operations, and conflict detection.

Provided as ``RetrievalMixin`` so the methods can be composed onto the ``Engram``
class at runtime. Methods reference ``self._knowledge_dir``, ``self._read_entries``,
etc. — those attributes live on the Engram instance and remain authoritative in core.py.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from .storage import (
    CONFLICT_C_CEILING,
    CONFLICT_Q_THRESHOLD,
    DOMAIN_KEYWORDS,
    FIELD_WEIGHTS,
    MAX_KNOWLEDGE_ENTRIES,
    SEARCH_RELEVANCE_THRESHOLD,
    SIMILARITY_THRESHOLD,
    _AFFIRMATION_MARKERS,
    _ALIAS_LOOKUP,
    _NEGATION_MARKERS,
    _TERM_ALIASES,
    _now_iso,
    _read_json,
    _write_json,
)


class RetrievalMixin:
    """Search, scoring, batch operations and conflict detection."""

    # Promotion threshold — only real evidence (access count) triggers auto-promote.
    # Time-based auto-promote removed: mere survival is not proof of value.
    _PROMOTE_ACCESS_COUNT = 3   # Referenced 3+ times → auto-promote

    # ------------------------------------------------------------------
    # Tier promotion / staging
    # ------------------------------------------------------------------

    def evaluate_tiers(self) -> dict:
        """Batch-evaluate tier promotions for all knowledge.

        Called explicitly during wrap_up_session, NOT on every read.
        Only promotes staging→verified when access_count >= threshold.
        Returns summary of changes.
        """
        promoted = 0
        for entry_type, path_name in [("lesson", "lessons.json"), ("decision", "decisions.json")]:
            path = self._knowledge_dir / path_name
            entries = _read_json(path)
            if not isinstance(entries, list):
                continue
            changed = False
            for entry in entries:
                tier = entry.get("tier", "verified")
                if tier == "staging":
                    access = entry.get("access_count", 0)
                    if access >= self._PROMOTE_ACCESS_COUNT:
                        entry["tier"] = "verified"
                        entry["promoted_at"] = _now_iso()
                        entry["promotion_reason"] = f"referenced {access} times"
                        promoted += 1
                        changed = True
            if changed:
                _write_json(path, entries)

        # Playbooks are deliberately excluded from access-count auto-promotion.
        # Playbook errors can cause operational harm, so promotion requires
        # explicit user confirmation or successful reuse — not just reads.

        return {"promoted": promoted}

    def get_staging_summary(self) -> dict:
        """Count active staging items across lessons and decisions."""
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")
        staging_lessons = [
            lesson
            for lesson in lessons
            if lesson.get("tier") == "staging" and lesson.get("status") == "active"
        ]
        staging_decisions = [
            decision
            for decision in decisions
            if decision.get("tier") == "staging" and decision.get("status") == "active"
        ]
        staging_playbooks = []
        for idx_entry in self._read_playbook_index():
            if idx_entry.get("status") != "active":
                continue
            pb = self._read_playbook_by_id(idx_entry.get("id", ""))
            if pb and pb.get("tier") == "staging":
                staging_playbooks.append(pb)
        all_staging = staging_lessons + staging_decisions
        return {
            "staging_lessons": len(staging_lessons),
            "staging_decisions": len(staging_decisions),
            "staging_playbooks": len(staging_playbooks),
            "total_staging": len(staging_lessons) + len(staging_decisions) + len(staging_playbooks),
            "oldest_staging": min(
                (
                    entry.get("created_at", "")
                    for entry in all_staging
                    if entry.get("created_at", "")
                ),
                default="",
            ),
        }

    # ------------------------------------------------------------------
    # Tokenization & similarity
    # ------------------------------------------------------------------

    def _tokenize(self, text: str, *, expand_aliases: bool = True) -> set[str]:
        """Tokenize text into character n-grams plus normalized aliases."""
        if not text:
            return set()

        text_lower = text.lower()
        tokens: set[str] = set()

        for word in re.split(r"[^a-z0-9]+", text_lower):
            if not word:
                continue
            tokens.add(word)
            canonical = _ALIAS_LOOKUP.get(word) if expand_aliases else None
            if canonical:
                tokens.add(canonical)
                tokens.update(_TERM_ALIASES.get(canonical, []))

        cjk_chars = [ch for ch in text_lower if "\u4e00" <= ch <= "\u9fff"]
        for ch in cjk_chars:
            tokens.add(ch)
            canonical = _ALIAS_LOOKUP.get(ch) if expand_aliases else None
            if canonical:
                tokens.add(canonical)
                tokens.update(_TERM_ALIASES.get(canonical, []))
        for i in range(len(cjk_chars) - 1):
            bigram = cjk_chars[i] + cjk_chars[i + 1]
            tokens.add(bigram)
            canonical = _ALIAS_LOOKUP.get(bigram) if expand_aliases else None
            if canonical:
                tokens.add(canonical)
                tokens.update(_TERM_ALIASES.get(canonical, []))
        if expand_aliases:
            for i in range(len(cjk_chars) - 2):
                trigram = cjk_chars[i] + cjk_chars[i + 1] + cjk_chars[i + 2]
                canonical = _ALIAS_LOOKUP.get(trigram)
                if canonical:
                    tokens.add(trigram)
                    tokens.add(canonical)
                    tokens.update(_TERM_ALIASES.get(canonical, []))

        return tokens

    def _bigram_similarity(self, a: str, b: str) -> float:
        """Similarity score using tokenized sets (works for CJK and ASCII)."""
        if not a or not b:
            return 0.0
        a_tokens = {
            token for token in self._tokenize(a, expand_aliases=False)
            if not (len(token) == 1 and "\u4e00" <= token <= "\u9fff")
        }
        b_tokens = {
            token for token in self._tokenize(b, expand_aliases=False)
            if not (len(token) == 1 and "\u4e00" <= token <= "\u9fff")
        }
        if not a_tokens or not b_tokens:
            return 0.0
        intersection = a_tokens & b_tokens
        return 2.0 * len(intersection) / (len(a_tokens) + len(b_tokens))

    def _score_item(self, item: dict, terms: list[str]) -> float:
        """Score a knowledge item against query terms using weighted fields."""
        if not terms:
            return 0.0

        query_tokens: set[str] = set()
        for term in terms:
            query_tokens.update(self._tokenize(term))
        if not query_tokens:
            return 0.0

        score = 0.0
        all_matched: set[str] = set()
        for field, weight in FIELD_WEIGHTS.items():
            raw = item.get(field, "")
            if isinstance(raw, list):
                value = " ".join(str(v) for v in raw).lower()
            else:
                value = str(raw).lower()
            if not value:
                continue
            field_tokens = self._tokenize(value)
            if not field_tokens:
                continue
            matched_tokens = query_tokens & field_tokens
            all_matched.update(matched_tokens)
            score += weight * (len(matched_tokens) / len(query_tokens))

        # Query coverage bonus: reward items matching more unique query terms
        coverage = len(all_matched) / len(query_tokens)
        score += coverage * 2.0

        primary = str(
            item.get("summary")
            or item.get("title")
            or item.get("question")
            or ""
        ).lower()
        if primary:
            query_str = " ".join(terms)
            score += self._bigram_similarity(query_str, primary) * 1.5

        score += math.log1p(item.get("access_count", 0)) * 0.1

        # Trigger exact-match bonus (playbooks)
        triggers = item.get("triggers")
        if triggers and isinstance(triggers, list):
            query_lower = {t.lower() for t in terms}
            trigger_lower = {str(t).lower() for t in triggers}
            exact_hits = query_lower & trigger_lower
            score += len(exact_hits) * 5.0

        return score

    # ------------------------------------------------------------------
    # Search API
    # ------------------------------------------------------------------

    def search_knowledge(self, query: str, scope: str = "all", limit: int = 10) -> dict:
        """Search lessons, decisions, and playbooks by weighted multi-term relevance."""
        terms = [term for term in (query or "").lower().split() if term]
        results: dict = {"lessons": [], "decisions": [], "playbooks": []}
        limit = max(0, int(limit))
        if not terms or limit == 0:
            return results

        if scope in ("all", "lessons"):
            path = self._knowledge_dir / "lessons.json"
            lessons = self._read_entries(path, "lesson")
            for lesson in lessons:
                if lesson.get("status") != "active":
                    continue
                score = self._score_item(lesson, terms)
                if score >= SEARCH_RELEVANCE_THRESHOLD:
                    item = dict(lesson)
                    item["_score"] = round(score, 3)
                    results["lessons"].append(item)
            results["lessons"] = sorted(
                results["lessons"],
                key=lambda item: item["_score"],
                reverse=True,
            )[:limit]

        if scope in ("all", "decisions"):
            path = self._knowledge_dir / "decisions.json"
            decisions = self._read_entries(path, "decision")
            for decision in decisions:
                if decision.get("status") != "active":
                    continue
                score = self._score_item(decision, terms)
                if score >= SEARCH_RELEVANCE_THRESHOLD:
                    item = dict(decision)
                    item["_score"] = round(score, 3)
                    results["decisions"].append(item)
            results["decisions"] = sorted(
                results["decisions"],
                key=lambda item: item["_score"],
                reverse=True,
            )[:limit]

        if scope in ("all", "playbooks"):
            index = self._read_playbook_index()
            for entry in index:
                if entry.get("status") != "active":
                    continue
                pb = self._read_playbook_by_id(entry.get("id", ""))
                if not pb:
                    continue
                score = self._score_item(pb, terms)
                if score >= SEARCH_RELEVANCE_THRESHOLD:
                    item = dict(pb)
                    item["_score"] = round(score, 3)
                    results["playbooks"].append(item)
            results["playbooks"] = sorted(
                results["playbooks"],
                key=lambda item: item["_score"],
                reverse=True,
            )[:limit]

        return results

    def get_relevant_lessons(self, project_folder: str | None = None,
                             limit: int = 8,
                             _update_access: bool = True) -> list[dict]:
        """根据项目技术栈智能筛选教训：相关领域优先，兼顾通用教训。

        策略：
        1. 从项目快照获取 tech_stack → 映射到 domain 标签
        2. 匹配领域的教训排前面，通用/产品策略教训补充
        3. 最终按时间倒序在各组内排列

        Returns: 最多 limit 条教训（相关度排序）
        """
        all_lessons = self.get_lessons(limit=MAX_KNOWLEDGE_ENTRIES, _update_access=_update_access)
        if not all_lessons:
            return []

        # 确定当前项目相关的领域
        relevant_domains: set = set()
        if project_folder:
            proj = self.get_project_snapshot(project_folder)
            tech_stack = proj.get("tech_stack", [])
            # 技术栈 → 领域映射
            stack_to_domain = {
                "python": "python", "Python": "python",
                "javascript": "frontend", "JS": "frontend",
                "HTML/CSS/JS": "frontend", "html": "frontend",
                "TypeScript": "frontend", "React": "frontend",
                "MCP": "mcp_dev", "FastMCP": "mcp_dev",
                "Claude": "claude_code", "Claude Code": "claude_code",
                "DeepSeek": "python",
            }
            for tech in tech_stack:
                domain = stack_to_domain.get(tech)
                if domain:
                    relevant_domains.add(domain)

        # 通用领域（总是相关）
        universal_domains = {"产品策略", "架构"}

        # 分桶：相关领域 / 通用 / 其他（支持多标签 domain）
        relevant = []
        universal = []
        other = []
        for lesson in reversed(all_lessons):  # 最新的在前
            lesson_domains = {d.strip() for d in (lesson.get("domain") or "").split(",") if d.strip()}
            if lesson_domains & relevant_domains:
                relevant.append(lesson)
            elif lesson_domains & universal_domains:
                universal.append(lesson)
            else:
                other.append(lesson)

        # 按比例分配: 相关领域占 60%, 通用 30%, 其他 10%
        # 空桶的 slots 回收给非空桶，避免大量浪费
        n_relevant = min(len(relevant), max(1, int(limit * 0.6)))
        n_universal = min(len(universal), max(1, int(limit * 0.3)))
        n_other = limit - n_relevant - n_universal

        result = relevant[:n_relevant] + universal[:n_universal] + other[:n_other]
        return result[:limit]

    def get_knowledge_inheritance(
        self,
        description: str,
        limit: int = 10,
    ) -> dict:
        """Return a ranked lessons + decisions inheritance pack for free text."""
        terms = [term for term in (description or "").lower().split() if term]
        limit = max(1, int(limit))

        if not terms:
            return {
                "description": description,
                "total": 0,
                "recommended_domains": [],
                "items": [],
            }

        scored: list[tuple[float, str, dict]] = []

        lessons_path = self._knowledge_dir / "lessons.json"
        for lesson in self._read_entries(lessons_path, "lesson"):
            if lesson.get("status") != "active":
                continue
            score = self._score_item(lesson, terms)
            if score > 0:
                scored.append((score, "lesson", lesson))

        decisions_path = self._knowledge_dir / "decisions.json"
        for decision in self._read_entries(decisions_path, "decision"):
            if decision.get("status") != "active":
                continue
            score = self._score_item(decision, terms)
            if score > 0:
                scored.append((score, "decision", decision))

        scored.sort(key=lambda entry: entry[0], reverse=True)
        top = scored[:limit]

        domain_counts: dict[str, int] = {}
        for _, _, item in top:
            for _d in str(item.get("domain", "")).split(","):
                _d = _d.strip()
                if _d:
                    domain_counts[_d] = domain_counts.get(_d, 0) + 1
        recommended_domains = sorted(
            domain_counts,
            key=lambda domain: (-domain_counts[domain], domain),
        )

        items = []
        for rank, (score, item_type, item) in enumerate(top, start=1):
            view = self._knowledge_view(item_type, item)
            view["rank"] = rank
            view["type"] = item_type
            view["score"] = round(score, 3)
            items.append(view)

        return {
            "description": description,
            "total": len(items),
            "recommended_domains": recommended_domains,
            "items": items,
        }

    def find_similar_knowledge(self, item_id: str, limit: int = 5) -> dict:
        """Find active knowledge items with similar primary content."""
        item_type, item = self._find_item_by_id(item_id)
        if item is None or item_type is None:
            return {"error": f"Item not found: {item_id}"}

        source_text = str(
            item.get("summary")
            or item.get("title")
            or item.get("question")
            or ""
        )
        if not source_text:
            return {
                "source": self._knowledge_view(item_type, item),
                "similar": [],
                "total": 0,
            }

        candidates = []
        sources = (
            ("lesson", self._knowledge_dir / "lessons.json"),
            ("decision", self._knowledge_dir / "decisions.json"),
        )
        for entry_type, path in sources:
            entries = self._read_entries(path, entry_type)
            for entry in entries:
                if entry.get("status") != "active":
                    continue
                if entry.get("id") == item_id:
                    continue
                candidate_text = str(
                    entry.get("summary")
                    or entry.get("title")
                    or entry.get("question")
                    or ""
                )
                similarity = self._bigram_similarity(source_text, candidate_text)
                if similarity > 0.2:
                    candidate = self._knowledge_view(entry_type, entry)
                    candidate["similarity"] = round(similarity, 3)
                    candidates.append(candidate)

        candidates.sort(key=lambda candidate: candidate["similarity"], reverse=True)
        candidates = candidates[:max(0, int(limit))]
        return {
            "source": self._knowledge_view(item_type, item),
            "similar": candidates,
            "total": len(candidates),
        }

    # ------------------------------------------------------------------
    # Bulk add operations
    # ------------------------------------------------------------------

    def bulk_add_lessons(self, lessons: list, source_tool: str = "") -> dict:
        """Add multiple lessons while reusing add_lesson validation and dedupe."""
        if not isinstance(lessons, list):
            return {
                "total": 0,
                "saved": 0,
                "duplicates": 0,
                "errors": 1,
                "results": [{"status": "error", "reason": "lessons must be a list", "input": str(lessons)[:100]}],
            }
        total = len(lessons)
        saved = duplicates = errors = 0
        results = []

        for original in lessons:
            item = original
            try:
                if isinstance(item, str):
                    item = {"summary": item}
                elif isinstance(item, dict):
                    item = dict(item)
                else:
                    raise ValueError("lesson item must be a dict or string")

                summary = str(item.get("summary", "")).strip()
                if not summary:
                    raise ValueError("empty summary")
                item["summary"] = summary
                if source_tool and not item.get("source_tool"):
                    item["source_tool"] = source_tool

                result = self.add_lesson(item)
                if result.get("status") == "duplicate":
                    duplicates += 1
                    results.append({
                        "status": "duplicate",
                        "existing_id": result.get("existing_id"),
                        "summary": summary,
                    })
                else:
                    saved += 1
                    results.append({
                        "status": "saved",
                        "id": result.get("id"),
                        "summary": result.get("summary", summary),
                    })
            except Exception as exc:
                errors += 1
                results.append({
                    "status": "error",
                    "reason": str(exc),
                    "input": str(original)[:100],
                })

        return {
            "total": total,
            "saved": saved,
            "duplicates": duplicates,
            "errors": errors,
            "results": results,
        }

    def bulk_add_decisions(self, decisions: list, source_tool: str = "") -> dict:
        """Add multiple decisions while reusing add_decision validation and dedupe."""
        if not isinstance(decisions, list):
            return {
                "total": 0,
                "saved": 0,
                "duplicates": 0,
                "errors": 1,
                "results": [{"status": "error", "reason": "decisions must be a list", "input": str(decisions)[:100]}],
            }
        total = len(decisions)
        saved = duplicates = errors = 0
        results = []

        for original in decisions:
            item = original
            try:
                if isinstance(item, str):
                    item = {"title": item, "choice": ""}
                elif isinstance(item, dict):
                    item = dict(item)
                else:
                    raise ValueError("decision item must be a dict or string")

                title = str(item.get("title") or item.get("question") or "").strip()
                if not title:
                    raise ValueError("empty title")
                if "title" in item:
                    item["title"] = title
                else:
                    item["question"] = title
                item.setdefault("choice", "")
                if source_tool and not item.get("source_tool"):
                    item["source_tool"] = source_tool

                result = self.add_decision(item)
                if result.get("status") == "duplicate":
                    duplicates += 1
                    results.append({
                        "status": "duplicate",
                        "existing_id": result.get("existing_id"),
                        "title": title,
                    })
                else:
                    saved += 1
                    results.append({
                        "status": "saved",
                        "id": result.get("id"),
                        "title": self._entry_identity_text(result, "decision") or title,
                    })
            except Exception as exc:
                errors += 1
                results.append({
                    "status": "error",
                    "reason": str(exc),
                    "input": str(original)[:100],
                })

        return {
            "total": total,
            "saved": saved,
            "duplicates": duplicates,
            "errors": errors,
            "results": results,
        }

    def bulk_add_knowledge(
        self,
        items: list,
        item_type: str = "lesson",
        source_tool: str = "",
    ) -> dict:
        """Add multiple lessons or decisions in one call."""
        if item_type == "lesson":
            return self.bulk_add_lessons(items, source_tool=source_tool)
        if item_type == "decision":
            return self.bulk_add_decisions(items, source_tool=source_tool)
        return {"error": f"Unknown item_type: {item_type}. Use 'lesson' or 'decision'."}

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def _detect_decision_conflicts(self, decisions: list[dict]) -> list[dict]:
        """Find decision pairs with similar topics but different choices."""
        conflicts: list[dict] = []
        for i, d1 in enumerate(decisions):
            for d2 in decisions[i + 1:]:
                # Domain overlap check (skip if explicitly different domains)
                dom1 = {d.strip() for d in (d1.get("domain") or d1.get("project") or "").split(",") if d.strip()}
                dom2 = {d.strip() for d in (d2.get("domain") or d2.get("project") or "").split(",") if d.strip()}
                if dom1 and dom2 and not (dom1 & dom2):
                    continue

                q1 = self._entry_identity_text(d1, "decision")
                q2 = self._entry_identity_text(d2, "decision")
                q_sim = self._bigram_similarity(q1, q2)
                if q_sim < CONFLICT_Q_THRESHOLD:
                    continue

                c1 = d1.get("choice", "")
                c2 = d2.get("choice", "")
                c_sim = self._bigram_similarity(c1, c2)
                if c_sim >= CONFLICT_C_CEILING:
                    continue  # same choice, not a conflict

                conflicts.append({
                    "type": "decision",
                    "q1": q1, "c1": c1,
                    "q2": q2, "c2": c2,
                })
        return conflicts

    def _detect_lesson_conflicts(self, lessons: list[dict]) -> list[dict]:
        """Find lesson pairs giving contradictory advice on the same topic."""
        conflicts: list[dict] = []
        for i, l1 in enumerate(lessons):
            for l2 in lessons[i + 1:]:
                dom1 = {d.strip() for d in (l1.get("domain") or "").split(",") if d.strip()}
                dom2 = {d.strip() for d in (l2.get("domain") or "").split(",") if d.strip()}
                if dom1 and dom2 and not (dom1 & dom2):
                    continue

                s1 = l1.get("summary", "")
                s2 = l2.get("summary", "")

                # Must share a significant token (multi-char keyword)
                t1 = {t for t in self._tokenize(s1, expand_aliases=False) if len(t) >= 2}
                t2 = {t for t in self._tokenize(s2, expand_aliases=False) if len(t) >= 2}
                if not (t1 & t2):
                    continue

                # Sentiment asymmetry: one affirms, the other negates
                has_neg1 = any(m in s1 for m in _NEGATION_MARKERS)
                has_neg2 = any(m in s2 for m in _NEGATION_MARKERS)
                has_pos1 = any(m in s1 for m in _AFFIRMATION_MARKERS)
                has_pos2 = any(m in s2 for m in _AFFIRMATION_MARKERS)

                if not ((has_neg1 and has_pos2) or (has_neg2 and has_pos1)):
                    continue

                conflicts.append({
                    "type": "lesson",
                    "s1": s1, "s2": s2,
                })
        return conflicts
