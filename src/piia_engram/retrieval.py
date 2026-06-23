"""Engram retrieval layer — search, scoring, tokenization, batch operations, and conflict detection.

Provided as ``RetrievalMixin`` so the methods can be composed onto the ``Engram``
class at runtime. Methods reference ``self._knowledge_dir``, ``self._read_entries``,
etc. — those attributes live on the Engram instance and remain authoritative in core.py.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from .search_index import (
    SearchIndex,
    _content_hash,
    _entry_document,
    reciprocal_rank_fusion,
    vector_backend_available,
)
from .storage import (
    CONFLICT_C_CEILING,
    CONFLICT_Q_THRESHOLD,
    DOMAIN_KEYWORDS,
    FIELD_WEIGHTS,
    HYBRID_RELEVANCE_THRESHOLD,
    MAX_KNOWLEDGE_ENTRIES,
    SEARCH_RELEVANCE_THRESHOLD,
    SEMANTIC_NEIGHBOR_THRESHOLD,
    SIMILARITY_THRESHOLD,
    _AFFIRMATION_MARKERS,
    _ALIAS_LOOKUP,
    _NEGATION_MARKERS,
    _TERM_ALIASES,
    _now_iso,
)


@lru_cache(maxsize=4096)
def _tokenize_cached(text: str, expand_aliases: bool) -> frozenset[str]:
    """Pure, process-wide memoized tokenizer.

    Depends only on ``text`` and the import-time-static alias tables
    (``_ALIAS_LOOKUP`` / ``_TERM_ALIASES``), so the result is stable for a
    given input and safe to cache without runtime invalidation. The hot search
    path re-tokenizes the same entry fields on every query; memoizing turns
    that repeated CPU work into a dict lookup.

    Returns a frozenset so the cached value cannot be mutated by callers.
    """
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

    cjk_chars = [ch for ch in text_lower if "一" <= ch <= "鿿"]
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

    return frozenset(tokens)


class RetrievalMixin:
    """Search, scoring, batch operations and conflict detection."""

    # Review-suggestion threshold. Access count is a weak signal: it can flag
    # staging items for review, but must never auto-promote them to verified.
    _REVIEW_SUGGEST_ACCESS_COUNT = 3

    # ------------------------------------------------------------------
    # Tier promotion / staging
    # ------------------------------------------------------------------

    def evaluate_tiers(self) -> dict:
        """Batch-evaluate tier review suggestions for all knowledge.

        Called explicitly during wrap_up_session, NOT on every read.
        Access_count >= threshold marks staging entries with a promotion
        suggestion; it does not change their tier.
        Returns summary of changes.
        """
        promoted = 0
        suggested = 0
        for entry_type, path_name in [("lesson", "lessons.json"), ("decision", "decisions.json")]:
            path = self._knowledge_dir / path_name
            entries = self._read_entries(path, entry_type)
            if not entries:
                continue
            changed = False
            for entry in entries:
                tier = entry.get("tier", "verified")
                if tier == "staging":
                    access = entry.get("access_count", 0)
                    if access >= self._REVIEW_SUGGEST_ACCESS_COUNT:
                        reason = f"referenced {access} times"
                        if (
                            entry.get("promotion_suggested") is not True
                            or entry.get("promotion_reason") != reason
                        ):
                            entry["promotion_suggested"] = True
                            entry["promotion_suggested_at"] = _now_iso()
                            entry["promotion_reason"] = reason
                            suggested += 1
                            changed = True
            if changed:
                self._write_entries(path, entries, entry_type)

        # Playbooks are also excluded from access-count promotion. Playbook
        # errors can cause operational harm, so promotion requires explicit
        # user confirmation or successful reuse — not just reads.

        return {"promoted": promoted, "suggested": suggested}

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
        """Tokenize text into character n-grams plus normalized aliases.

        Delegates to the process-wide memoized ``_tokenize_cached``. Callers
        mutate the returned set (e.g. ``_score_item`` does ``.update(...)``), so
        a fresh ``set`` copy of the cached frozenset is returned each call.
        """
        if not text:
            return set()
        return set(_tokenize_cached(text, expand_aliases))

    def _bigram_similarity(self, a: str, b: str) -> float:
        """Similarity score using tokenized sets (works for CJK and ASCII)."""
        if not a or not b:
            return 0.0
        a_tokens = {
            token for token in _tokenize_cached(a, False)
            if not (len(token) == 1 and "\u4e00" <= token <= "\u9fff")
        }
        b_tokens = {
            token for token in _tokenize_cached(b, False)
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
            # Read-only intersection below — consume the cached frozenset
            # directly to skip a per-field defensive set() copy in the hot loop.
            field_tokens = _tokenize_cached(value, True)
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
    # v4.0 hybrid search index (rebuildable; JSON stays source of truth)
    # ------------------------------------------------------------------

    def _hybrid_enabled(self) -> bool:
        """Hybrid search is opt-in via ENGRAM_SEARCH=hybrid (default keyword)."""
        return os.environ.get("ENGRAM_SEARCH", "keyword").strip().lower() == "hybrid"

    def _hybrid_index(self) -> SearchIndex:
        idx = getattr(self, "_search_index_cache", None)
        if idx is None:
            idx = SearchIndex(self.root / "search_index.db")
            self._search_index_cache = idx
        return idx

    def _corpus_encrypted(self) -> bool:
        """True when corpus encryption is active for this engram."""
        return bool(getattr(self, "_corpus_key", b""))

    # ------------------------------------------------------------------
    # Round-3: non-destructive semantic near-duplicate surfacing on write
    # ------------------------------------------------------------------

    def _semantic_neighbors_for_write(
        self,
        text: str,
        *,
        exclude_id: str = "",
        limit: int = 5,
        min_similarity: float = SEMANTIC_NEIGHBOR_THRESHOLD,
    ) -> list[tuple[str, float]]:
        """Embedding neighbors of a to-be-written item's identity text.

        Computed BEFORE the JSON write lock so embedding latency never extends
        the lock. Returns ``[]`` — today's lexical-only write path runs
        unchanged — unless ALL gates hold:

        * ``ENGRAM_SEARCH=hybrid`` (opt-in; default keyword), AND
        * the corpus is NOT encrypted (fail-closed: never read/derive a
          cleartext index under encryption), AND
        * the vector backend is installed.

        The neighbor source itself (:meth:`SearchIndex.semantic_neighbors`) is
        read-only and will not create the index file. The new item's own id is
        excluded (it is not in the index yet, but guard anyway).
        """
        if not text or not text.strip():
            return []
        if not self._hybrid_enabled():
            return []
        if self._corpus_encrypted():
            return []
        if not vector_backend_available():
            return []
        try:
            neighbors = self._hybrid_index().semantic_neighbors(
                text, limit=limit, min_similarity=min_similarity
            )
        except Exception:
            return []
        if exclude_id:
            neighbors = [(eid, sim) for eid, sim in neighbors if eid != exclude_id]
        return neighbors

    def _semantic_crosslink_in_lock(
        self,
        new_entry: dict,
        entries: list[dict],
        neighbors: list[tuple[str, float]],
        best_sim: float,
    ) -> None:
        """Cross-link ``new_entry`` to its top semantic neighbor — never reject.

        Runs INSIDE the write lock (where ``entries`` is the authoritative
        same-type active list) so the neighbor can be re-verified against fresh
        state. Fires only when:

        * the lexical tier PASSED (``best_sim < SIMILARITY_THRESHOLD``) — so a
          lexical "related"/"duplicate" decision is never overridden, AND
        * the lexical path did not already attach a ``_dedup_note``.

        The top neighbor is re-verified in-lock — it must be a *current,
        active, same-type, non-self* entry — else it is dropped (stale index /
        cross-type / archived id never pollutes ``related_ids``). Only the
        single best verified neighbor is linked (over-linking guard). The link
        is bidirectional; the item is always ADDED (no semantic reject, ever).
        """
        if best_sim >= SIMILARITY_THRESHOLD:
            return
        if not neighbors:
            return
        if new_entry.get("_dedup_note"):
            return
        new_id = new_entry.get("id", "")
        by_id = {
            e.get("id"): e
            for e in entries
            if e.get("status") == "active" and e.get("id")
        }
        for neighbor_id, sim in neighbors:
            if not neighbor_id or neighbor_id == new_id:
                continue
            match = by_id.get(neighbor_id)
            if match is None:
                continue  # stale / cross-type / archived — not verifiable
            if neighbor_id not in new_entry.get("related_ids", []):
                new_entry.setdefault("related_ids", []).append(neighbor_id)
            if new_id and new_id not in match.get("related_ids", []):
                match.setdefault("related_ids", []).append(new_id)
            new_entry["_dedup_note"] = (
                f"semantically related to {neighbor_id} (cos={sim:.2f})"
            )
            return  # top-1 only

    def purge_search_index(self) -> bool:
        """Delete any persisted hybrid search index from disk.

        When corpus encryption is enabled the FTS/vector tables would
        materialise decrypted bodies into ``<root>/search_index.db`` in
        cleartext, defeating the encryption. This removes the db file and its
        SQLite sidecars (-wal/-shm) and drops the in-process cache so a stale
        plaintext index left over from a pre-encryption run can't survive
        (Codex a5 round-2 P1-1/P1-2).

        Returns True if anything was removed.
        """
        # Drop the cached handle first. SearchIndex opens/closes a fresh
        # connection per operation, so no file handle lingers between calls,
        # but clearing the cache forces a clean rebuild path afterwards.
        self._search_index_cache = None
        removed = False
        for path in (self.root / "search_index.db",
                     self.root / "search_index.db-wal",
                     self.root / "search_index.db-shm"):
            try:
                if path.exists():
                    path.unlink()
                    removed = True
            except OSError:
                continue
        # Fail-closed: under corpus encryption a surviving search_index.db keeps
        # decrypted bodies readable on disk. If it could not be removed (locked
        # by another process, permission error), refuse to pretend the purge
        # succeeded — raise so the caller stops instead of silently leaving a
        # plaintext index in place (Codex a5 round-3 O2).
        if self._corpus_encrypted() and (self.root / "search_index.db").exists():
            raise RuntimeError(
                f"search_index.db in {self.root} could not be removed while "
                "corpus encryption is enabled; it may expose decrypted content "
                "on disk. Close any process holding it open and retry."
            )
        return removed

    def _all_indexable_entries(self) -> list[dict]:
        """All active lessons + decisions + playbooks, for the index."""
        entries: list[dict] = []
        for name, typ in (("lessons.json", "lesson"), ("decisions.json", "decision")):
            entries += [
                e for e in self._read_entries(self._knowledge_dir / name, typ)
                if e.get("status") == "active"
            ]
        for idx_entry in self._read_playbook_index():
            if idx_entry.get("status") != "active":
                continue
            pb = self._read_playbook_by_id(idx_entry.get("id", ""))
            if pb:
                entries.append(pb)
        return entries

    @staticmethod
    def _entries_fingerprint(entries: list[dict]) -> str:
        """Freshness fingerprint: changes iff any indexed entry's text OR the
        active embedding model changes.

        Including the model is essential — otherwise swapping
        ``ENGRAM_EMBED_MODEL`` (or upgrading the default) without touching
        content would NOT trigger a rebuild, leaving a stale vector table at
        the old dimension and silently disabling the vector signal until the
        next content edit / manual reindex.
        """
        from . import search_index as _si  # read model/backend at call time
        parts = sorted(
            f"{e.get('id')}:{_content_hash(_entry_document(e))}"
            for e in entries if e.get("id")
        )
        # Fold in BOTH the embedding model AND whether the vector backend is
        # available: installing the [vector] extra after a FTS-only build
        # flips this False->True, which must trigger a rebuild so the vector
        # signal actually gets built (otherwise it stays silently absent).
        header = f"{_si.EMBED_MODEL}|vec={_si.vector_backend_available()}"
        blob = header + "\n" + "\n".join(parts)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def _ensure_index_fresh(self, entries: list[dict]) -> SearchIndex:
        """Lazily (re)build the index when the source content changed.

        Any failure here is swallowed — the index is an optimization, never
        a hard dependency of search.
        """
        # Defense-in-depth: never materialise a persistent index while corpus
        # encryption is active. The fingerprint()/rebuild() calls below open a
        # connection and write decrypted bodies into search_index.db. The public
        # callers (search_knowledge / rebuild_index) already guard against this,
        # but keeping the sink-adjacent helper fail-safe means a future internal
        # caller can't silently reopen the leak (Codex a5 round-3 O4).
        if self._corpus_encrypted():
            self.purge_search_index()
            return self._hybrid_index()
        idx = self._hybrid_index()
        try:
            fp = self._entries_fingerprint(entries)
            need_rebuild = idx.fingerprint() != fp
            # Defensive: vector is enabled but the vec table is missing (e.g.
            # an older FTS-only index file on disk) — rebuild to populate it.
            if not need_rebuild and idx.vector_enabled and not idx.has_vector_table():
                need_rebuild = True
            if need_rebuild:
                idx.rebuild(entries, fingerprint=fp)
        except Exception:
            pass
        return idx

    def rebuild_index(self) -> dict:
        """Explicitly rebuild the search index from current JSON (CLI: reindex)."""
        # Corpus encryption on → the FTS/vector tables would materialise
        # decrypted bodies into search_index.db in cleartext. Refuse to build
        # and purge any stale plaintext index instead of leaking it through the
        # explicit reindex path (Codex a5 round-2 P1-1).
        if self._corpus_encrypted():
            purged = self.purge_search_index()
            return {
                "indexed": 0,
                "vector_enabled": False,
                "skipped": "corpus_encrypted",
                "purged": purged,
            }
        entries = self._all_indexable_entries()
        idx = self._hybrid_index()
        n = idx.rebuild(entries, fingerprint=self._entries_fingerprint(entries))
        return {"indexed": n, "vector_enabled": idx.vector_enabled}

    # ------------------------------------------------------------------
    # c0 — Decision threads (additive; does not change existing read paths)
    # ------------------------------------------------------------------

    def add_relation(self, src_id: str, rel: str, dst_id: str) -> dict:
        """Record a typed, directed relation between two knowledge items.

        ``rel`` ∈ {led_to, supersedes, implemented_by}. Idempotent; invalid
        relations are rejected (returns added=False). This is the explicit
        edge-building path (v1); semi-automatic supersedes suggestions land
        later.

        Staging-tier items are excluded: relations must only link verified
        knowledge so that decision threads and version chains are not
        polluted with unconfirmed items.
        """
        from .governance_store import RelationStore

        known = {
            str(e["id"])
            for e in self._all_indexable_entries()
            if e.get("id") and e.get("tier", "verified") != "staging"
        }
        if str(src_id) not in known or str(dst_id) not in known:
            return {"added": False, "reason": "unknown_id",
                    "src": str(src_id), "rel": rel, "dst": str(dst_id)}

        added = RelationStore(self.root).add_relation(src_id, rel, dst_id)
        self._audit.log("write", "knowledge/relations",
                        detail=f"{src_id} {rel} {dst_id} added={added}")
        return {"added": bool(added), "src": str(src_id), "rel": rel, "dst": str(dst_id)}

    def remove_relation(self, src_id: str, rel: str, dst_id: str) -> dict:
        """Remove a typed, directed relation. Idempotent (returns removed=False
        if the edge did not exist). This is the undo for ``add_relation``."""
        from .governance_store import RelationStore

        removed = RelationStore(self.root).remove_relation(src_id, rel, dst_id)
        self._audit.log("write", "knowledge/relations",
                        detail=f"{src_id} {rel} {dst_id} removed={removed}")
        return {"removed": bool(removed), "src": str(src_id), "rel": rel, "dst": str(dst_id)}

    def get_decision_thread(self, seed_id: str) -> dict:
        """Reconstruct the decision thread containing ``seed_id`` (how this
        evolved: idea → … → decision → implementation, with superseded items
        flagged and the current head(s) surfaced). Read-only."""
        from .decision_thread import build_thread
        from .governance_store import RelationStore

        entries = {
            str(e["id"]): e
            for e in self._all_indexable_entries()
            if e.get("id")
        }
        edges = RelationStore(self.root).all_edges()
        self._audit.log("read", "knowledge/decision_thread", detail=str(seed_id))
        return build_thread(seed_id, edges, entries=entries)

    def get_decision_history(self, question: str, threshold: float = 0.6) -> dict:
        """Retrieve the full revision history of a decision question.

        Finds all decisions whose question text matches ``question`` (bigram
        similarity ≥ ``threshold``), enriches each with supersedes relations,
        and returns them in chronological order (oldest first). The ``current``
        field points to the active, non-superseded decision — the one that
        "won" the latest round.

        Unlike ``get_decision_thread`` (which starts from an ID and follows
        ALL edge types), this method starts from **question text** and focuses
        specifically on the revision history of a single topic.

        Returns::

            {
              "found": bool,
              "query": str,
              "revisions": [ {id, question, choice, reasoning, timestamp,
                              status, superseded_by?}, ... ],
              "current": {id, question, choice, ...} | None,
              "revision_count": int,
            }
        """
        from .governance_store import RelationStore

        path = self._knowledge_dir / "decisions.json"
        decisions = self._read_entries(path, "decision")

        # Find all decisions matching the question text.
        matches: list[dict] = []
        for d in decisions:
            d = self._ensure_fields(d, "decision")
            q_text = self._entry_identity_text(d, "decision")
            sim = self._bigram_similarity(question, q_text)
            if sim >= threshold:
                matches.append(d)

        if not matches:
            self._audit.log("read", "knowledge/decision_history",
                            detail=f"query={question[:60]} found=0")
            return {"found": False, "query": question, "revisions": [],
                    "current": None, "revision_count": 0}

        # Load supersedes edges to determine which decisions are obsolete.
        edges = RelationStore(self.root).all_edges()
        match_ids = {str(d["id"]) for d in matches if d.get("id")}

        # Build superseded_by map: dst → src (the old decision → its replacement).
        superseded_by: dict[str, str] = {}
        superseded_set: set[str] = set()
        for e in edges:
            if e["rel"] == "supersedes" and e["dst"] in match_ids:
                superseded_by[e["dst"]] = e["src"]
                superseded_set.add(e["dst"])

        # Sort chronologically (oldest first).
        matches.sort(key=lambda d: d.get("timestamp", ""))

        revisions: list[dict] = []
        for d in matches:
            did = str(d.get("id", ""))
            row: dict = {
                "id": did,
                "question": d.get("question") or d.get("title") or "",
                "choice": d.get("choice", ""),
                "reasoning": d.get("reasoning", ""),
                "timestamp": d.get("timestamp", ""),
                "status": "superseded" if did in superseded_set else "active",
            }
            if did in superseded_by:
                row["superseded_by"] = superseded_by[did]
            revisions.append(row)

        # Current = the most recent non-superseded decision.
        active = [r for r in revisions if r["status"] == "active"]
        current = active[-1] if active else None

        self._audit.log("read", "knowledge/decision_history",
                        detail=f"query={question[:60]} found={len(revisions)}")
        return {
            "found": True,
            "query": question,
            "revisions": revisions,
            "current": current,
            "revision_count": len(revisions),
        }

    # ------------------------------------------------------------------
    # Permission Profile (a0)
    # ------------------------------------------------------------------

    def get_permission_profile(self) -> dict:
        """Return a readable overview of the caller permission landscape.

        Shows all explicitly granted callers, auto-classification rules,
        what each trust level can access, and revoked callers. This is
        the user-facing view of the governance layer's GrantStore.

        Returns::

            {
              "governance_enabled": bool,
              "grants": {agent_id: trust_level, ...},
              "revoked": [agent_id, ...],
              "auto_rules": {client_type: trust_level, ...},
              "trust_levels": {level: {max_sensitivity, read, write}, ...},
            }
        """
        from . import governance as gov
        from .governance_runtime import governance_enabled
        from .governance_store import GrantStore

        store = GrantStore(self.root)
        data = store.list_grants()

        # Build auto-classification map for known client types.
        auto_rules: dict[str, str] = {}
        for ct in sorted(gov._SELF_CLIENTS):
            auto_rules[ct] = gov.classify_agent(ct)
        for ct in sorted(gov._KNOWN_LOCAL_CLIENTS):
            auto_rules[ct] = gov.classify_agent(ct)
        auto_rules["(unknown)"] = gov.DEFAULT_TRUST_LEVEL

        self._audit.log("read", "governance/permission_profile")
        return {
            "governance_enabled": governance_enabled(),
            "grants": data["grants"],
            "revoked": data["revoked"],
            "auto_rules": auto_rules,
            "trust_levels": {
                name: {
                    "max_sensitivity": level["max_sensitivity"],
                    "read": level["read"],
                    "write": level["write"],
                }
                for name, level in gov.TRUST_LEVELS.items()
            },
        }

    def set_caller_trust(self, agent_id: str, trust_level: str) -> dict:
        """Set or update a caller's trust level in the GrantStore.

        Returns ``{success, agent_id, trust_level}`` or ``{success: False,
        error}`` if the trust level is invalid.
        """
        from . import governance as gov
        from .governance_store import GrantStore

        agent_id = str(agent_id).strip()
        trust_level = str(trust_level).strip()

        if not agent_id:
            return {"success": False, "error": "agent_id cannot be empty"}
        if trust_level not in gov.TRUST_LEVELS:
            return {
                "success": False,
                "error": f"unknown trust level {trust_level!r}",
                "valid_levels": list(gov.TRUST_LEVELS.keys()),
            }

        GrantStore(self.root).set_grant(agent_id, trust_level)
        self._audit.log("write", "governance/grants",
                        detail=f"set {agent_id} → {trust_level}")
        return {"success": True, "agent_id": agent_id, "trust_level": trust_level}

    def revoke_caller(self, agent_id: str) -> dict:
        """Revoke a caller's future access (forward-only — cannot recall
        context already returned). Returns ``{success, agent_id}``."""
        from .governance_store import GrantStore

        agent_id = str(agent_id).strip()
        if not agent_id:
            return {"success": False, "error": "agent_id cannot be empty"}

        GrantStore(self.root).revoke(agent_id)
        self._audit.log("write", "governance/grants",
                        detail=f"revoked {agent_id}")
        return {"success": True, "agent_id": agent_id, "revoked": True}

    # ------------------------------------------------------------------
    # Search API
    # ------------------------------------------------------------------

    def search_knowledge(self, query: str, scope: str = "all", limit: int = 10,
                          filters: dict | None = None,
                          allow_hybrid_index: bool = True,
                          project_folder: str | None = None) -> dict:
        """Search lessons, decisions, and playbooks by weighted multi-term relevance.

        Args:
            query: Search keywords (space-separated).
            scope: 'all', 'lessons', 'decisions', or 'playbooks'.
            limit: Max results per category.
            filters: Optional dict with keys:
                - domain: str — only items whose domain contains this value
                - tier: str — only items matching this tier ('staging' or 'verified')
                - date_after: str — ISO date, only items with timestamp >= this
            allow_hybrid_index: when False, NEVER touch the persisted hybrid FTS/
                vector index — fall back to the in-memory keyword path. The MCP
                layer passes False for a non-owner under ENGRAM_GOVERNANCE so a
                low-trust search cannot materialise the full (unfiltered) corpus
                into ``<root>/search_index.db`` before the return is governed
                (Codex round-19 P1 file-side-effect leak). Default True keeps the
                governance-OFF / owner path byte-identical.
        """
        terms = [term for term in (query or "").lower().split() if term]
        results: dict = {"lessons": [], "decisions": [], "playbooks": []}
        limit = max(0, int(limit))
        if not terms or limit == 0:
            return results

        filters = filters or {}

        def _matches_filters(item: dict) -> bool:
            if "domain" in filters:
                item_domain = (item.get("domain") or "").lower()
                if filters["domain"].lower() not in item_domain:
                    return False
            if "tier" in filters:
                if item.get("tier", "verified") != filters["tier"]:
                    return False
            if "date_after" in filters:
                ts = item.get("timestamp") or item.get("created") or ""
                if ts:
                    try:
                        item_dt = datetime.fromisoformat(ts)
                        cutoff_dt = datetime.fromisoformat(filters["date_after"])
                        if item_dt < cutoff_dt:
                            return False
                    except (TypeError, ValueError):
                        return False
            return True

        # Hybrid search (opt-in): build/refresh the index once for this call.
        # ``allow_hybrid_index=False`` (set by the MCP governance layer for a
        # non-owner) suppresses this entirely: _ensure_index_fresh rebuilds the
        # FTS/vector tables from the FULL active corpus (unfiltered by trust
        # tier) and persists them to <root>/search_index.db, so running it
        # before the return is governed would leave the secret bodies readable
        # in the index file (Codex round-19). Falling back to the keyword path
        # (hybrid_idx stays None) writes nothing.
        hybrid_idx = None
        # Corpus encryption enabled → suppress persistent hybrid index to
        # prevent decrypted content from being materialised into search_index.db
        # (Codex a5 audit finding #2). Fall back to keyword-only path.
        corpus_encrypted = self._corpus_encrypted()
        if allow_hybrid_index and self._hybrid_enabled() and not corpus_encrypted:
            hybrid_idx = self._ensure_index_fresh(self._all_indexable_entries())

        if scope in ("all", "lessons"):
            candidates = [
                lesson for lesson in self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
                if lesson.get("status") == "active" and _matches_filters(lesson)
            ]
            results["lessons"] = self._rank_scope(candidates, terms, query, limit, hybrid_idx)

        if scope in ("all", "decisions"):
            candidates = [
                decision for decision in self._read_entries(self._knowledge_dir / "decisions.json", "decision")
                if decision.get("status") == "active" and _matches_filters(decision)
            ]
            results["decisions"] = self._rank_scope(candidates, terms, query, limit, hybrid_idx)

        if scope in ("all", "playbooks"):
            index_entries = [
                e for e in self._read_playbook_index()
                if e.get("status") == "active"
            ]
            if terms:
                lower_terms = [t.lower() for t in terms]
                def _index_matches(entry):
                    title = (entry.get("title") or "").lower()
                    domain = (entry.get("domain") or "").lower()
                    text = f"{title} {domain}"
                    return any(t in text for t in lower_terms)
                matched = [e for e in index_entries if _index_matches(e)]
                unmatched = [e for e in index_entries if not _index_matches(e)]
                index_entries = matched + unmatched[: max(0, (limit or 10) * 2 - len(matched))]

            candidates = []
            for entry in index_entries:
                pb = self._read_playbook_by_id(entry.get("id", ""))
                if (
                    pb
                    and self._playbook_visible_for_project(pb, project_folder)
                    and _matches_filters(pb)
                ):
                    candidates.append(pb)
            results["playbooks"] = self._rank_scope(candidates, terms, query, limit, hybrid_idx)

        # Model-facing read: the ranked views are fresh copies (never written
        # back), so substitute a placeholder for any content field whose
        # decryption silently failed instead of surfacing raw ciphertext.
        if self._corpus_key:
            results["lessons"] = self._display_sanitize(results["lessons"], "lesson")
            results["decisions"] = self._display_sanitize(results["decisions"], "decision")
            results["playbooks"] = self._display_sanitize(results["playbooks"], "playbook")
        return results

    def _rank_scope(self, candidates: list[dict], terms: list[str], query: str,
                    limit: int, hybrid_idx: "SearchIndex | None") -> list[dict]:
        """Rank one scope's filtered candidates.

        Keyword path (default): keep items scoring >= SEARCH_RELEVANCE_THRESHOLD,
        sort by token score — identical to the pre-hybrid behavior.

        Hybrid path (ENGRAM_SEARCH=hybrid): fuse the keyword ranking with FTS
        and vector rankings (both restricted to this scope's candidates) via
        RRF. Since any keyword-passing item has score > 0 it always enters the
        keyword ranking, so the fused set is a superset of the keyword set —
        hybrid recall >= keyword recall, with vector/FTS surfacing extra
        semantically/lexically related items and reordering by agreement.
        """
        scored = [(item, self._score_item(item, terms)) for item in candidates]

        if hybrid_idx is None:
            out = []
            for item, score in scored:
                if score >= SEARCH_RELEVANCE_THRESHOLD:
                    view = dict(item)
                    view["_score"] = round(score, 3)
                    out.append(view)
            out.sort(key=lambda v: v["_score"], reverse=True)
            return out[:limit]

        by_id = {str(item["id"]): item for item, _ in scored if item.get("id")}
        kw_score = {str(item["id"]): s for item, s in scored if item.get("id")}
        cand_ids = set(by_id)
        kw_rank = [
            eid for eid, _ in sorted(kw_score.items(), key=lambda kv: kv[1], reverse=True)
            if kw_score[eid] > 0
        ]
        fts_rank = [i for i in hybrid_idx.fts_search(query, limit=max(limit, 50)) if i in cand_ids]
        vec_rank = [i for i in hybrid_idx.vector_search(query, limit=max(limit, 50)) if i in cand_ids]

        fused = [
            (eid, rrf)
            for eid, rrf in reciprocal_rank_fusion([kw_rank, fts_rank, vec_rank])
            if rrf >= HYBRID_RELEVANCE_THRESHOLD and eid in by_id
        ]
        rrf_by_id = dict(fused)
        fused_order = [eid for eid, _ in fused]

        # Recall guarantee: the keyword path returns items scoring
        # >= SEARCH_RELEVANCE_THRESHOLD (top `limit`). Those MUST survive into
        # the hybrid result — RRF reordering must not let truncation at
        # `limit` evict a keyword hit (hybrid recall >= keyword recall). So
        # pin the keyword keepers first, then fill remaining slots by RRF.
        kw_keep = [eid for eid in kw_rank if kw_score[eid] >= SEARCH_RELEVANCE_THRESHOLD][:limit]
        selected = list(dict.fromkeys(kw_keep + fused_order))[:limit]
        selected.sort(key=lambda eid: rrf_by_id.get(eid, 0.0), reverse=True)

        out = []
        for eid in selected:
            view = dict(by_id[eid])
            view["_score"] = round(rrf_by_id.get(eid, 0.0), 4)
            view["_keyword_score"] = round(kw_score.get(eid, 0.0), 3)
            out.append(view)
        return out

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
        # Display-only result (never written back); ensure no leaked ciphertext
        # is surfaced even on the non-owner path where get_lessons was called
        # with _update_access=False and therefore did not sanitize. Idempotent
        # if get_lessons already sanitized (placeholder has no enc: prefix).
        return self._display_sanitize(result[:limit], "lesson")

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
        """Find actionable decision pairs with similar topics but different choices."""
        from .conflict_governance import detect_active_decision_conflicts

        return detect_active_decision_conflicts(
            decisions,
            similarity=self._bigram_similarity,
            identity_text=lambda entry: self._entry_identity_text(entry, "decision"),
            q_threshold=CONFLICT_Q_THRESHOLD,
            c_ceiling=CONFLICT_C_CEILING,
        )

    def detect_active_decision_conflicts(
        self,
        decisions: list[dict] | None = None,
        *,
        relations: list[dict] | None = None,
        resolutions: dict | None = None,
        include_suppressed: bool = False,
    ) -> list[dict]:
        """Detect decision conflicts using stored supersedes edges and resolutions."""
        from .conflict_governance import detect_active_decision_conflicts
        from .governance_store import RelationStore, ResolutionStore

        if decisions is None:
            decisions = self.get_decisions(limit=None, _update_access=False)
        if relations is None:
            relations = RelationStore(self.root).all_edges()
        if resolutions is None:
            resolutions = ResolutionStore(self.root).all_records()
        return detect_active_decision_conflicts(
            decisions,
            relations=relations,
            resolutions=resolutions,
            similarity=self._bigram_similarity,
            identity_text=lambda entry: self._entry_identity_text(entry, "decision"),
            q_threshold=CONFLICT_Q_THRESHOLD,
            c_ceiling=CONFLICT_C_CEILING,
            include_suppressed=include_suppressed,
        )

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
