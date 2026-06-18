"""Cross-type knowledge operations: update/archive/lifecycle/merge/link (KnowledgeOpsMixin)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import freshness_anchors as _freshness_anchors
from . import provenance as _provenance
from .storage import _now_iso


class KnowledgeOpsMixin:
    """Operations that span lessons, decisions, and playbooks."""

    # ------------------------------------------------------------------
    # Knowledge update / archive / lifecycle / merge / linking
    # ------------------------------------------------------------------

    def update_knowledge(self, item_id: str, updates: dict) -> dict:
        """Update a lesson, decision, or playbook by ID (auto-detects type)."""
        item_type, _ = self._find_item_by_id(item_id)
        if item_type is None:
            return {"error": f"Item not found: {item_id}"}
        if item_type == "lesson":
            return self.update_lesson(item_id, updates)
        if item_type == "playbook":
            return self.update_playbook(item_id, updates)
        return self.update_decision(item_id, updates)

    def create_onboard_candidate(
        self,
        claim_text: str,
        *,
        anchor_ref: str,
        anchor_detail: dict | None = None,
        anchor_project_id: str | None = None,
        extractor: str = "onboard-repo",
    ) -> dict:
        """Create a STAGING repo-fact candidate from an enumerated anchor.

        Agent-proposed and trust-stripped: tier is forced to "staging" (not
        auto-verified) and NO confirmation_source/anchor_status is set here. The
        owner grants trust later via accept_onboard_candidate. Uses the internal
        provenance path only to retain the anchor binding (anchor_project_id is
        otherwise stripped on ordinary writes) — never to self-attest trust.
        """
        provenance: dict[str, Any] = {"extractor": extractor, "anchor_ref": anchor_ref}
        if anchor_detail is not None:
            provenance["anchor_detail"] = anchor_detail
        if anchor_project_id is not None:
            provenance["anchor_project_id"] = anchor_project_id
        lesson = {
            "summary": claim_text,
            "domain": "repo-fact",
            "tier": "staging",
            "provenance": provenance,
        }
        return self.add_lesson(lesson, _allow_internal_provenance=True)

    @staticmethod
    def _onboard_claim_text(kind: str, ref: str, detail: dict | None) -> str:
        detail = detail if isinstance(detail, dict) else {}
        if kind == "dep":
            version = detail.get("version")
            if version:
                return f"This project depends on `{ref}` ({version})."
            return f"This project depends on `{ref}`."
        return f"This project includes the file `{ref}`."

    def create_onboard_candidates(
        self,
        anchors: list[dict],
        *,
        repo_id: str | None = None,
        extractor: str = "onboard-repo",
    ) -> dict:
        """Turn enumerate_anchors() output into STAGING candidate facts.

        Idempotent per repo, keyed on (repo_id, anchor_ref): a new anchor is
        created; an unchanged one is left alone (existing); a changed one (e.g. a
        version bump) updates the STAGING candidate in place. Already-accepted
        (verified) facts are never silently rewritten -- they count as existing.
        kind="unsupported" markers are skipped + counted, not dropped.
        """
        # Index current repo-facts for this repo by anchor_ref (for upsert).
        index: dict[str, dict] = {}
        for entry in self.get_lessons(limit=None, _update_access=False):
            prov = entry.get("provenance")
            if isinstance(prov, dict) and prov.get("anchor_project_id") == repo_id:
                ref_str = prov.get("anchor_ref")
                if isinstance(ref_str, str) and ref_str:
                    index[ref_str] = entry

        created: list[dict] = []
        existing = 0
        updated = 0
        skipped = 0
        for anchor in anchors:
            if not isinstance(anchor, dict):
                skipped += 1
                continue
            kind = anchor.get("kind")
            ref = str(anchor.get("ref", ""))
            if kind not in {"dep", "file"} or not ref:
                skipped += 1
                continue
            detail = anchor.get("detail") if isinstance(anchor.get("detail"), dict) else {}
            anchor_ref = anchor.get("anchor_ref") or _freshness_anchors.format_anchor_ref(kind, ref)
            claim = self._onboard_claim_text(kind, ref, detail)

            prior = index.get(anchor_ref)
            if prior is not None:
                prior_prov = prior.get("provenance") if isinstance(prior.get("provenance"), dict) else {}
                prior_detail = prior_prov.get("anchor_detail")
                prior_detail = prior_detail if isinstance(prior_detail, dict) else {}
                if prior_detail == detail:
                    existing += 1
                    continue
                if prior.get("tier") == "staging":
                    def _mark(entry: dict, _claim: str = claim, _detail: dict = detail) -> dict:
                        entry["summary"] = _claim
                        prov2 = entry.get("provenance") if isinstance(entry.get("provenance"), dict) else {}
                        prov2["anchor_detail"] = _detail
                        entry["provenance"] = prov2
                        return self._ensure_fields(entry, "lesson")
                    self._update_knowledge_item("lesson", prior.get("id", ""), _mark)
                    updated += 1
                else:
                    existing += 1  # owner-accepted (verified) -- leave it intact
                continue

            created.append(self.create_onboard_candidate(
                claim,
                anchor_ref=anchor_ref,
                anchor_detail=detail,
                anchor_project_id=repo_id,
                extractor=extractor,
            ))
        self._record_fv_candidates(created, existing, updated, skipped)
        return {
            "created": len(created),
            "existing": existing,
            "updated": updated,
            "skipped": skipped,
            "candidates": created,
        }

    def onboard_repo(
        self,
        project_root: str,
        *,
        repo_id: str | None = None,
        extractor: str = "onboard-repo",
    ) -> dict:
        """Scan a repo's anchors and create STAGING candidate facts.

        Orchestrates M1 enumeration + M2 candidate creation: resolve the repo
        identity (git remote, unless repo_id is given), enumerate npm/Python/file
        anchors, and create/upsert staging repo-fact candidates. The owner accepts
        each later via accept_onboard_candidate; nothing is auto-verified here.
        """
        if repo_id is None:
            repo_id = _freshness_anchors.read_project_id(project_root)
        anchors = _freshness_anchors.enumerate_anchors(project_root)
        self._record_fv_scan(anchors, repo_id)
        summary = self.create_onboard_candidates(
            anchors, repo_id=repo_id, extractor=extractor
        )
        summary["repo_id"] = repo_id
        summary["anchors_scanned"] = len(anchors)
        return summary

    def _stamp_validated_entry(
        self,
        entry: dict,
        entry_type: str,
        *,
        source_agent: str = "owner",
        validated_at: str | None = None,
        confirmation_source: str | None = None,
    ) -> dict:
        ts = validated_at or _now_iso()
        stamp = _provenance.normalize_provenance_fields({
            "source_agent": source_agent or "owner",
            "last_validated_at": ts,
        })
        if "last_validated_at" not in stamp:
            stamp["last_validated_at"] = _now_iso()
        if "source_agent" not in stamp:
            stamp["source_agent"] = "owner"
        try:
            reviewed_at = datetime.fromisoformat(
                stamp["last_validated_at"].replace("Z", "+00:00")
            ).replace(tzinfo=None, microsecond=0).isoformat()
        except (TypeError, ValueError):
            reviewed_at = _now_iso()

        provenance = entry.get("provenance")
        if not isinstance(provenance, dict):
            provenance = {}
        provenance.update(stamp)
        if confirmation_source in {"human", "test_signal", "anchor"}:
            provenance["confirmation_source"] = confirmation_source
        entry["provenance"] = provenance
        entry["last_reviewed"] = reviewed_at
        entry["last_updated"] = reviewed_at
        return self._ensure_fields(entry, entry_type)

    def confirm_knowledge(
        self,
        item_id: str,
        *,
        by: str,
        anchor_ref: str | None = None,
        anchor_project_id: str | None = None,
    ) -> dict:
        """Owner-confirm a knowledge item with an explicit freshness source."""
        mode = str(by or "").strip().lower()
        source_by_mode = {
            "human": "human",
            "test": "test_signal",
            "anchor": "anchor",
        }
        confirmation_source = source_by_mode.get(mode)
        if confirmation_source is None:
            return {"error": f"Invalid confirmation mode: {by}"}
        if mode == "anchor":
            if not isinstance(anchor_ref, str) or not anchor_ref.strip():
                return {"error": "anchor_ref is required when by=anchor"}

        item_type, item = self._find_item_by_id(item_id)
        if item is None or item_type not in {"lesson", "decision", "playbook"}:
            return {"error": f"Item not found: {item_id}"}

        ts = _now_iso()

        def _mark(entry: dict) -> dict:
            updated = self._stamp_validated_entry(
                entry,
                item_type,
                source_agent="owner",
                validated_at=ts,
                confirmation_source=confirmation_source,
            )
            if mode == "anchor":
                provenance = updated.get("provenance")
                if not isinstance(provenance, dict):
                    provenance = {}
                provenance["anchor_status"] = "valid"
                provenance["anchor_ref"] = anchor_ref
                if isinstance(anchor_project_id, str) and anchor_project_id.strip():
                    provenance["anchor_project_id"] = anchor_project_id.strip()
                updated["provenance"] = provenance
            return updated

        updated = self._update_knowledge_item(item_type, item_id, _mark)
        if updated is None:
            return {"error": f"Item not found: {item_id}"}
        self._audit.log("write", "knowledge/confirm", detail=item_id)
        return updated

    def accept_onboard_candidate(
        self,
        item_id: str,
        *,
        project_root: str | None = None,
    ) -> dict:
        """Owner-accept an onboard candidate: atomically promote it to verified
        and stamp the anchor confirmation.

        Owner is the trust authority, so this DOES promote tier staging->verified
        (unlike confirm_knowledge, which is stamp-only), and it stamps
        confirmation_source="anchor" (not "human" like promote_knowledge).
        anchor_status is derived by checking the anchor against project_root when
        given, else "unknown" — never fabricated. (A later read-time check_anchors
        pass demotes the fact if the anchor is INVALID.)
        """
        item_type, item = self._find_item_by_id(item_id)
        if item is None or item_type not in {"lesson", "decision", "playbook"}:
            return {"error": f"Item not found: {item_id}"}
        provenance = item.get("provenance")
        anchor_ref = provenance.get("anchor_ref") if isinstance(provenance, dict) else None
        if not isinstance(anchor_ref, str) or not anchor_ref.strip():
            return {"error": "Item has no anchor_ref to accept"}

        status = _freshness_anchors.UNKNOWN
        if project_root:
            candidate_project_id = provenance.get("anchor_project_id") if isinstance(provenance, dict) else None
            if isinstance(candidate_project_id, str) and candidate_project_id.strip():
                root_project_id = _freshness_anchors.read_project_id(project_root)
                if root_project_id is not None and root_project_id != candidate_project_id.strip():
                    return {
                        "error": (
                            "candidate anchor_project_id "
                            f"{candidate_project_id.strip()!r} does not match project "
                            f"{root_project_id!r}; not accepting"
                        ),
                    }
            parsed = _freshness_anchors.parse_anchor_ref(anchor_ref)
            status = _freshness_anchors.check_anchor(parsed, project_root)
        if status == _freshness_anchors.INVALID:
            # Refuse to verify a fact bound to a broken anchor; don't rely on a
            # later check_anchors pass to demote it. (UNKNOWN stays acceptable.)
            return {
                "error": f"anchor {anchor_ref!r} is INVALID in the given project; not accepting",
                "anchor_status": status,
            }

        ts = _now_iso()

        def _mark(entry: dict) -> dict:
            entry["tier"] = "verified"
            updated = self._stamp_validated_entry(
                entry,
                item_type,
                source_agent="owner",
                validated_at=ts,
                confirmation_source="anchor",
            )
            prov = updated.get("provenance")
            if not isinstance(prov, dict):
                prov = {}
            prov["anchor_status"] = status
            prov["anchor_ref"] = anchor_ref
            updated["provenance"] = prov
            return updated

        updated = self._update_knowledge_item(item_type, item_id, _mark)
        if updated is None:
            return {"error": f"Item not found: {item_id}"}
        self._audit.log("write", "knowledge/onboard-accept", detail=item_id)
        return updated

    def accept_onboard_candidates(
        self,
        *,
        project_root: str | None = None,
        repo_id: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Owner-accept ALL staging onboard candidates in one batch.

        Per-item atomic: reuses accept_onboard_candidate for each candidate, so
        every one is independently anchor-verified, cross-repo-guarded, and
        refused if its anchor is INVALID. The batch can partially succeed — one
        bad anchor never rolls back the others, and one unexpected exception
        never aborts the rest. Candidates whose anchor_project_id differs from
        this batch's repo are SKIPPED; that cross-repo filter stays active even
        when the repo identity can't be resolved (resolved_repo_id is None), so
        an unresolved root never silently accepts an identified repo's candidate.
        dry_run is a zero-write preview that still verifies anchors read-only, so
        would_accept / would_reject are honest. The CLI runs dry by default;
        --yes commits.
        """
        resolved_repo_id = repo_id
        if resolved_repo_id is None and project_root:
            resolved_repo_id = _freshness_anchors.read_project_id(project_root)

        results: list[dict] = []
        preview: list[dict] = []
        accepted = rejected = skipped = 0
        would_accept = would_reject = 0

        for entry in self.get_lessons(limit=None, _update_access=False):
            if entry.get("domain") != "repo-fact" or entry.get("tier") != "staging":
                continue
            prov = entry.get("provenance") if isinstance(entry.get("provenance"), dict) else {}
            anchor_ref = prov.get("anchor_ref")
            item_id = entry.get("id", "")
            if not isinstance(anchor_ref, str) or not anchor_ref.strip():
                # surface (never silently drop) a staging repo-fact with no anchor
                skipped += 1
                results.append({"id": item_id, "status": "skipped", "reason": "no anchor_ref"})
                continue
            cand_pid = prov.get("anchor_project_id")
            # Cross-repo guard stays active regardless of resolved_repo_id: a
            # candidate carrying a repo identity different from this batch's
            # (including when resolved_repo_id is None) is never accepted here.
            if (
                isinstance(cand_pid, str)
                and cand_pid.strip()
                and cand_pid.strip() != resolved_repo_id
            ):
                skipped += 1
                results.append({
                    "id": item_id, "anchor_ref": anchor_ref,
                    "status": "skipped", "reason": "different repo",
                })
                continue

            # A candidate with no anchor_project_id (e.g. a repo with no git
            # remote) is not cross-repo-identifiable, so it is verified soundly
            # by the per-item anchor check below: that only accepts a fact that
            # is actually true of project_root (an absent dep -> INVALID ->
            # refused), which keeps no-remote repos usable without weakening the
            # cross-repo guard for identified candidates.
            if dry_run:
                # zero-write but honest: read-only anchor check so the owner sees
                # what would actually succeed before committing with --yes.
                status = _freshness_anchors.UNKNOWN
                if project_root:
                    parsed = _freshness_anchors.parse_anchor_ref(anchor_ref)
                    status = _freshness_anchors.check_anchor(parsed, project_root)
                if status == _freshness_anchors.INVALID:
                    would_reject += 1
                    results.append({
                        "id": item_id, "anchor_ref": anchor_ref,
                        "status": "would_reject", "reason": "anchor INVALID",
                    })
                else:
                    would_accept += 1
                    preview.append({
                        "id": item_id, "anchor_ref": anchor_ref, "summary": entry.get("summary"),
                    })
                continue

            try:
                res = self.accept_onboard_candidate(item_id, project_root=project_root)
            except Exception as exc:  # per-item isolation: never abort the batch
                rejected += 1
                results.append({
                    "id": item_id, "anchor_ref": anchor_ref,
                    "status": "rejected", "reason": f"exception: {exc}",
                })
                continue
            if isinstance(res, dict) and res.get("error"):
                rejected += 1
                results.append({
                    "id": item_id, "anchor_ref": anchor_ref,
                    "status": "rejected", "reason": res["error"],
                })
            else:
                accepted += 1
                results.append({"id": item_id, "anchor_ref": anchor_ref, "status": "accepted"})

        if dry_run:
            self._record_fv_accept_batch(
                dry_run=True, accepted=would_accept, rejected=would_reject,
                skipped=skipped, results=results,
            )
            return {
                "dry_run": True,
                "repo_id": resolved_repo_id,
                "would_accept": would_accept,
                "would_reject": would_reject,
                "skipped": skipped,
                "candidates": preview,
                "accepted": 0,
                "rejected": 0,
                "results": results,
            }
        self._record_fv_accept_batch(
            dry_run=False, accepted=accepted, rejected=rejected,
            skipped=skipped, results=results,
        )
        return {
            "dry_run": False,
            "repo_id": resolved_repo_id,
            "accepted": accepted,
            "rejected": rejected,
            "skipped": skipped,
            "results": results,
        }

    # ------------------------------------------------------------------
    # First-value funnel telemetry — result extractors (content-blind).
    # Each derives a bucketed outcome from the RESULT only (never args), is
    # gated by first_value_enabled(), and is wrapped so telemetry can never
    # break the onboarding flow. Nothing here writes content, ids, paths,
    # names, versions, or reasons — only closed bucket/enum values.
    # ------------------------------------------------------------------

    def _record_fv_scan(self, anchors: list, repo_id: object) -> None:
        from . import telemetry as _tel
        if not _tel.first_value_enabled():
            return
        try:
            kinds = [a.get("kind") for a in anchors if isinstance(a, dict)]
            _tel.record_first_value_event("onboard.scan.completed", {
                "anchors_bucket": _tel.bucket_scan(len(anchors)),
                "dep_anchors_bucket": _tel.bucket_wide(kinds.count("dep")),
                "file_anchors_bucket": _tel.bucket_wide(kinds.count("file")),
                "unsupported_bucket": _tel.bucket_med(kinds.count("unsupported")),
                "repo_identity": "resolved" if repo_id else "unresolved",
                "outcome": "success" if anchors else "empty",
                "error_category": "none",
            })
        except Exception:
            pass

    def _record_fv_candidates(
        self, created: list, existing: int, updated: int, skipped: int
    ) -> None:
        from . import telemetry as _tel
        if not _tel.first_value_enabled():
            return
        try:
            kinds: set[str] = set()
            for c in created:
                ref = (c.get("provenance") or {}).get("anchor_ref", "") if isinstance(c, dict) else ""
                if ref.startswith("dep:"):
                    kinds.add("dep")
                elif ref.startswith("file:"):
                    kinds.add("file")
            n = len(created)
            if not n:
                mix = "none"
            elif {"dep", "file"} <= kinds:
                mix = "mixed"
            elif "dep" in kinds:
                mix = "dep_only"
            elif "file" in kinds:
                mix = "file_only"
            else:
                mix = "none"
            if n and not existing and not updated:
                idem = "new_candidates"
            elif not n and updated:
                idem = "updated_existing"
            elif not n and not updated:
                idem = "all_existing"
            else:
                idem = "mixed"
            _tel.record_first_value_event("onboard.candidates.materialized", {
                "created_bucket": _tel.bucket_wide(n),
                "existing_bucket": _tel.bucket_wide(existing),
                "updated_bucket": _tel.bucket_med(updated),
                "skipped_bucket": _tel.bucket_small(skipped),
                "candidate_mix": mix,
                "idempotency": idem,
            })
        except Exception:
            pass

    def _record_fv_accept_batch(
        self, *, dry_run: bool, accepted: int, rejected: int, skipped: int, results: list
    ) -> None:
        from . import telemetry as _tel
        if not _tel.first_value_enabled():
            return
        try:
            total = accepted + rejected
            if total == 0 or accepted == 0:
                rate = "none"
            elif accepted == total:
                rate = "all"
            else:
                frac = accepted / total
                rate = "high" if frac >= 0.75 else "medium" if frac >= 0.4 else "low"
            counts: dict[str, int] = {}
            for r in results:
                if not isinstance(r, dict) or r.get("status") not in ("rejected", "would_reject"):
                    continue
                reason = str(r.get("reason") or "").lower()
                if "invalid" in reason:
                    cat = "anchor_invalid"
                elif "does not match" in reason or "anchor_project_id" in reason:
                    cat = "repo_mismatch"
                elif "no anchor" in reason or "missing" in reason:
                    cat = "missing_anchor"
                else:
                    cat = "exception"
                counts[cat] = counts.get(cat, 0) + 1
            if not counts:
                dom = "none"
            elif len(counts) == 1:
                dom = next(iter(counts))
            else:
                dom = "mixed"
            _tel.record_first_value_event("onboard.accept.batch_completed", {
                "dry_run": bool(dry_run),
                "accepted_bucket": _tel.bucket_wide(accepted),
                "rejected_bucket": _tel.bucket_med(rejected),
                "skipped_bucket": _tel.bucket_small(skipped),
                "acceptance_rate": rate,
                "dominant_reject_reason": dom,
            })
        except Exception:
            pass

    def revalidate_anchors(
        self,
        project_root: str,
        *,
        adopt_legacy: bool = False,
    ) -> dict:
        """Owner-run recheck for already-confirmed anchor provenance."""
        project_id = _freshness_anchors.read_project_id(project_root)
        report = {
            "checked": 0,
            "valid": 0,
            "invalid": 0,
            "unknown": 0,
            "demoted": 0,
            "skipped_mismatch": 0,
            "skipped_legacy": 0,
            "project_id": project_id,
        }

        def _iter_anchor_items() -> list[tuple[str, dict]]:
            rows: list[tuple[str, dict]] = []
            for item in self.get_lessons(limit=None, _update_access=False):
                rows.append(("lesson", item))
            for item in self.get_decisions(limit=None, _update_access=False):
                rows.append(("decision", item))
            for item in self.get_playbooks(limit=None, _update_access=False):
                rows.append(("playbook", item))
            return rows

        for item_type, item in _iter_anchor_items():
            provenance = item.get("provenance")
            if not isinstance(provenance, dict):
                continue
            source = str(provenance.get("confirmation_source") or "").strip().lower()
            if source != "anchor":
                continue

            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id:
                continue

            item_project_id = provenance.get("anchor_project_id")
            if not isinstance(item_project_id, str) or not item_project_id.strip():
                if adopt_legacy and project_id:
                    def _adopt(entry: dict) -> dict:
                        prov = entry.get("provenance")
                        if not isinstance(prov, dict):
                            prov = {}
                        prov["anchor_project_id"] = project_id
                        entry["provenance"] = prov
                        return entry

                    self._update_knowledge_item(item_type, item_id, _adopt)
                    self._audit.log("write", "knowledge/anchor_check", detail=item_id)
                else:
                    report["skipped_legacy"] += 1
                continue

            if project_id is None or item_project_id.strip() != project_id:
                report["skipped_mismatch"] += 1
                continue

            parsed = _freshness_anchors.parse_anchor_ref(provenance.get("anchor_ref"))
            status = _freshness_anchors.check_anchor(parsed, project_root)
            checked_at = _now_iso()

            def _mark(
                entry: dict, *, _status: str = status, _item_type: str = item_type
            ) -> dict:
                prov = entry.get("provenance")
                if not isinstance(prov, dict):
                    prov = {}
                prov["anchor_status"] = _status
                prov["anchor_checked_at"] = checked_at
                if _status == _freshness_anchors.INVALID:
                    # deanrie #13: an INVALID anchor (the dependency/config the
                    # fact was tied to is gone) is a definitive staleness EVENT,
                    # not gradual aging. Drop the fact straight back to an
                    # unconfirmed guess instead of letting it ride the 90-day
                    # clock. UNKNOWN (couldn't check) deliberately does NOT take
                    # this path — it still falls back to time decay, so an
                    # unresolvable miss never hides a real invalidation. Keep the
                    # anchor_* fields as evidence of why it was demoted; clear
                    # only the confirmation that no longer holds.
                    prov.pop("confirmation_source", None)
                    entry["provenance"] = prov
                    entry["tier"] = "staging"
                    # Re-derive memory_state / approval_status / approval_required
                    # / labeling so the demoted entry isn't left internally
                    # inconsistent (tier=staging but still approval_status=
                    # approved). _ensure_fields derives memory_state="staging"
                    # from the tier and cascades the dependent fields.
                    return self._ensure_fields(entry, _item_type)
                entry["provenance"] = prov
                return entry

            self._update_knowledge_item(item_type, item_id, _mark)
            self._audit.log("write", "knowledge/anchor_check", detail=item_id)
            report["checked"] += 1
            if status in {"valid", "invalid", "unknown"}:
                report[status] += 1
            else:
                report["unknown"] += 1
            if status == _freshness_anchors.INVALID:
                report["demoted"] += 1

        return report

    def mark_validated_knowledge(
        self,
        item_id: str,
        *,
        source_agent: str = "owner",
        validated_at: str | None = None,
        increment_access: bool = False,
    ) -> dict:
        """Stamp an item as explicitly validated without changing its content."""
        item_type, item = self._find_item_by_id(item_id)
        if item is None or item_type not in {"lesson", "decision", "playbook"}:
            return {"error": f"Item not found: {item_id}"}

        def _mark(entry: dict) -> dict:
            if increment_access:
                entry["access_count"] = entry.get("access_count", 0) + 1
            return self._stamp_validated_entry(
                entry,
                item_type,
                source_agent=source_agent,
                validated_at=validated_at,
            )

        updated = self._update_knowledge_item(item_type, item_id, _mark)
        if updated is None:
            return {"error": f"Item not found: {item_id}"}
        self._audit.log("write", "knowledge/validate", detail=item_id)
        return updated

    def archive_knowledge(self, item_id: str) -> dict:
        """Archive a lesson, decision, or playbook by ID (auto-detects type)."""
        item_type, _ = self._find_item_by_id(item_id)
        if item_type is None:
            return {"error": f"Item not found: {item_id}"}
        if item_type == "lesson":
            return self.archive_lesson(item_id)
        if item_type == "playbook":
            return self.archive_playbook(item_id)
        return self.archive_decision(item_id)

    def soft_archive_knowledge_tier(
        self,
        item_id: str,
        *,
        now: str | None = None,
        allow_verified: bool = False,
    ) -> dict:
        """Reversibly soft-archive a lesson/decision into the ``archived`` tier.

        This is the tier-transition primitive behind the owner-confirmed
        lifecycle archive apply path. It sets ``tier="archived"`` plus an
        ``archived_at`` timestamp and records the prior tier in
        ``archived_from_tier`` so the move can be undone. It never deletes and
        never changes ``status``.

        Fail-closed protections:
        - a ``verified`` entry is refused (``error="protected_verified"``) unless
          ``allow_verified=True`` (owner-confirmed manual archive from the dock);
        - an entry already in the ``archived`` tier is an idempotent no-op
          (``changed=False``), leaving its existing ``archived_at`` intact.

        Returns a metadata-only dict ``{id, type, changed, from_tier, to_tier,
        archived_at}`` (no bodies), or ``{"error": ...}`` if not found.
        """
        ts = now or _now_iso()
        for kind, fname in (("lesson", "lessons.json"), ("decision", "decisions.json")):
            path = self._knowledge_dir / fname
            result_box: dict[str, Any] = {}

            def _mutate_entries(entries: list[dict]) -> list[dict]:
                for entry in entries:
                    if entry.get("id") != item_id:
                        continue
                    current = entry.get("tier") if isinstance(entry.get("tier"), str) else ""
                    if current == "verified" and not allow_verified:
                        result_box["result"] = {
                            "id": item_id, "type": kind, "changed": False,
                            "from_tier": current, "to_tier": current,
                            "error": "protected_verified",
                        }
                        return entries
                    if current == "archived":
                        result_box["result"] = {
                            "id": item_id, "type": kind, "changed": False,
                            "from_tier": "archived", "to_tier": "archived",
                            "archived_at": entry.get("archived_at"),
                        }
                        return entries
                    entry["archived_from_tier"] = current
                    entry["tier"] = "archived"
                    entry["archived_at"] = ts
                    entry["last_updated"] = ts
                    self._ensure_fields(entry, kind)
                    result_box["result"] = {
                        "id": item_id, "type": kind, "changed": True,
                        "from_tier": current, "to_tier": "archived",
                        "archived_at": ts,
                    }
                    return entries
                result_box["result"] = None
                return entries

            self._update_entries(path, kind, _mutate_entries)
            result = result_box.get("result")
            if result is None:
                continue
            if result.get("changed"):
                self._audit.log(
                    "write",
                    "knowledge/lifecycle_archive",
                    detail=f"{kind} {item_id}: {result.get('from_tier') or 'none'} -> archived",
                )
            return result
        return {"error": f"Item not found: {item_id}"}

    def restore_lifecycle_archive(
        self,
        item_id: str,
        *,
        now: str | None = None,
    ) -> dict:
        """Undo a lifecycle soft archive: move an ``archived`` entry back.

        Restores the entry to its recorded prior tier (``archived_from_tier``,
        falling back to ``staging``) and clears the archive markers. A
        non-archived entry is an idempotent no-op (``changed=False``). Returns a
        metadata-only dict, or ``{"error": ...}`` if not found.
        """
        ts = now or _now_iso()
        for kind, fname in (("lesson", "lessons.json"), ("decision", "decisions.json")):
            path = self._knowledge_dir / fname
            result_box: dict[str, Any] = {}

            def _mutate_entries(entries: list[dict]) -> list[dict]:
                for entry in entries:
                    if entry.get("id") != item_id:
                        continue
                    current = entry.get("tier") if isinstance(entry.get("tier"), str) else ""
                    if current != "archived":
                        result_box["result"] = {
                            "id": item_id, "type": kind, "changed": False,
                            "from_tier": current, "to_tier": current,
                        }
                        return entries
                    prior = entry.get("archived_from_tier")
                    to_tier = prior if prior in {"staging", "verified"} else "staging"
                    entry["tier"] = to_tier
                    entry.pop("archived_at", None)
                    entry.pop("archived_from_tier", None)
                    entry["last_updated"] = ts
                    self._ensure_fields(entry, kind)
                    result_box["result"] = {
                        "id": item_id, "type": kind, "changed": True,
                        "from_tier": "archived", "to_tier": to_tier,
                    }
                    return entries
                result_box["result"] = None
                return entries

            self._update_entries(path, kind, _mutate_entries)
            result = result_box.get("result")
            if result is None:
                continue
            if result.get("changed"):
                self._audit.log(
                    "write",
                    "knowledge/lifecycle_restore",
                    detail=f"{kind} {item_id}: archived -> {result.get('to_tier')}",
                )
            return result
        return {"error": f"Item not found: {item_id}"}

    def review_knowledge(self, knowledge_id: str) -> dict:
        """Mark a lesson, decision, or playbook as reviewed without changing its content."""
        item_type, item = self._find_item_by_id(knowledge_id)
        if item is None or item_type not in {"lesson", "decision", "playbook"}:
            return {"error": f"Item not found: {knowledge_id}"}

        item = self.mark_validated_knowledge(
            knowledge_id,
            source_agent="owner",
            validated_at=_now_iso(),
            increment_access=True,
        )
        if item.get("error"):
            return item
        self._audit.log("write", "knowledge/review", detail=knowledge_id)
        return item

    def merge_knowledge(self, primary_id: str, secondary_id: str) -> dict:
        """Merge secondary into primary, then archive the secondary item."""
        if primary_id == secondary_id:
            return {"error": "Cannot merge an item with itself"}

        lessons, decisions, playbooks = self._read_link_collections()
        primary_type, primary = self._find_item_in_collections(primary_id, lessons, decisions, playbooks)
        secondary_type, secondary = self._find_item_in_collections(secondary_id, lessons, decisions, playbooks)

        if primary is None:
            return {"error": f"Primary item not found: {primary_id}"}
        if secondary is None:
            return {"error": f"Secondary item not found: {secondary_id}"}
        if primary.get("status") != "active":
            return {"error": f"Primary item is not active (status={primary.get('status')})"}
        if secondary.get("status") != "active":
            return {"error": f"Secondary item is not active (status={secondary.get('status')})"}

        primary_related = set(primary.get("related_ids", []))
        transferred = []
        secondary_related = list(secondary.get("related_ids", []))

        for related_id in secondary_related:
            if related_id in (primary_id, secondary_id):
                continue
            if related_id not in primary_related:
                primary_related.add(related_id)
                transferred.append(related_id)

        primary_related.discard(primary_id)
        primary_related.discard(secondary_id)
        merged_primary_related = sorted(primary_related)

        related_types: dict[str, str] = {}
        for related_id in secondary_related:
            if related_id in (primary_id, secondary_id):
                continue
            related_type, related_item = self._find_item_in_collections(
                related_id, lessons, decisions, playbooks
            )
            if related_item is None or related_type is None:
                continue
            related_types[related_id] = related_type

        def _merge_primary(entry: dict) -> dict:
            current_related = set(entry.get("related_ids", []))
            current_related.update(merged_primary_related)
            current_related.discard(primary_id)
            current_related.discard(secondary_id)
            entry["related_ids"] = sorted(current_related)
            return entry

        updated_primary = self._update_knowledge_item(primary_type, primary_id, _merge_primary)
        if updated_primary is None:
            return {"error": f"Primary item not found: {primary_id}"}

        # Preserve bidirectional link semantics for migrated related items.
        for related_id, related_type in related_types.items():
            def _retarget_related(entry: dict, *, _related_id: str = related_id) -> dict:
                related_ids = set(entry.get("related_ids", []))
                related_ids.discard(secondary_id)
                related_ids.discard(_related_id)
                related_ids.add(primary_id)
                entry["related_ids"] = sorted(related_ids)
                return entry

            self._update_knowledge_item(related_type, related_id, _retarget_related)

        ts = _now_iso()

        def _archive_secondary(entry: dict) -> dict:
            entry["status"] = "outdated"
            entry["merged_into"] = primary_id
            entry["last_updated"] = ts
            return entry

        updated_secondary = self._update_knowledge_item(
            secondary_type, secondary_id, _archive_secondary
        )
        if updated_secondary is None:
            return {"error": f"Secondary item not found: {secondary_id}"}
        self._audit.log(
            "write",
            "knowledge/merge",
            detail=f"{secondary_type}:{secondary_id} -> {primary_type}:{primary_id}",
        )

        return {
            "success": True,
            "primary_id": primary_id,
            "secondary_id": secondary_id,
            "secondary_archived": True,
            "related_ids_transferred": len(transferred),
            "primary_title": self._knowledge_title(primary_type, updated_primary),
            "secondary_title": self._knowledge_title(secondary_type, updated_secondary),
        }

    def _read_link_collections(self) -> tuple[list[dict], list[dict], list[dict]]:
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")
        playbooks = self._export_playbooks()
        return lessons, decisions, playbooks

    def _find_item_in_collections(
        self,
        item_id: str,
        lessons: list[dict],
        decisions: list[dict],
        playbooks: list[dict] | None = None,
    ) -> tuple[str | None, dict | None]:
        for item in lessons:
            if item.get("id") == item_id:
                return "lesson", item
        for item in decisions:
            if item.get("id") == item_id:
                return "decision", item
        if playbooks is not None:
            for item in playbooks:
                if item.get("id") == item_id:
                    return "playbook", item
        return None, None

    def _find_item_by_id(self, item_id: str) -> tuple[str | None, dict | None]:
        """Find a lesson, decision, playbook, or tool by id without updating access metadata."""
        lessons, decisions, playbooks = self._read_link_collections()
        item_type, item = self._find_item_in_collections(item_id, lessons, decisions, playbooks)
        if item_type is not None:
            return item_type, item
        # Fall through to tools registry
        for tool in self._read_tools():
            if tool.get("id") == item_id:
                return "tool", tool
        return None, None

    def _update_knowledge_item(self, item_type: str, item_id: str, mutator) -> dict | None:
        """Update one lesson, decision, or playbook without stale whole-list writes."""
        if item_type == "playbook":
            return self._update_playbook_file_by_id(item_id, mutator)
        if item_type not in {"lesson", "decision"}:
            return None

        filename = "lessons.json" if item_type == "lesson" else "decisions.json"
        path = self._knowledge_dir / filename
        result_box: dict[str, dict | None] = {}

        def _mutate(entries: list[dict]) -> list[dict]:
            for idx, entry in enumerate(entries):
                if entry.get("id") != item_id:
                    continue
                updated = mutator(entry)
                if updated is None:
                    updated = entry
                entries[idx] = updated
                result_box["result"] = updated
                return entries
            result_box["result"] = None
            return entries

        self._update_entries(path, item_type, _mutate)
        return result_box.get("result")

    def _knowledge_title(self, item_type: str | None, item: dict | None) -> str:
        if not item:
            return ""
        if item_type == "decision":
            return self._entry_identity_text(item, "decision")
        if item_type == "playbook":
            return item.get("title", "")
        return item.get("summary", "")

    def _knowledge_view(self, item_type: str, item: dict) -> dict:
        # Display-only view: substitute a placeholder for any content field
        # whose decryption silently failed, so raw ciphertext is never surfaced
        # as content. Operates on a sanitized copy; the source object (and any
        # write-back of it) is untouched.
        item = self._display_sanitize_one(item, item_type)
        if item_type == "decision":
            return {
                "id": item.get("id", ""),
                "type": "decision",
                "title": self._entry_identity_text(item, "decision"),
                "choice": item.get("choice", ""),
                "rationale": item.get("reasoning", ""),
            }
        if item_type == "playbook":
            return {
                "id": item.get("id", ""),
                "type": "playbook",
                "title": item.get("title", ""),
                "triggers": item.get("triggers", []),
                "description": item.get("description", ""),
                "domain": item.get("domain", ""),
            }
        return {
            "id": item.get("id", ""),
            "type": "lesson",
            "title": item.get("summary", ""),
            "content": item.get("detail") or item.get("summary", ""),
            "domain": item.get("domain", ""),
        }

    def link_knowledge(self, id_a: str, id_b: str) -> dict:
        """Create a bidirectional link between two knowledge items (lessons, decisions, or playbooks)."""
        type_a, item_a = self._find_item_by_id(id_a)
        type_b, item_b = self._find_item_by_id(id_b)

        if item_a is None or type_a not in {"lesson", "decision", "playbook"}:
            return {"error": f"Item not found: {id_a}"}
        if item_b is None or type_b not in {"lesson", "decision", "playbook"}:
            return {"error": f"Item not found: {id_b}"}

        def _add_related(target_id: str):
            def _mutate(entry: dict) -> dict:
                related_ids = list(entry.get("related_ids") or [])
                if target_id not in related_ids:
                    related_ids.append(target_id)
                entry["related_ids"] = related_ids
                return entry
            return _mutate

        updated_a = self._update_knowledge_item(type_a, id_a, _add_related(id_b))
        updated_b = self._update_knowledge_item(type_b, id_b, _add_related(id_a))
        if updated_a is None:
            return {"error": f"Item not found: {id_a}"}
        if updated_b is None:
            return {"error": f"Item not found: {id_b}"}

        title_a = self._knowledge_title(type_a, item_a)
        title_b = self._knowledge_title(type_b, item_b)
        return {"success": True, "message": f"Linked: {title_a} ↔ {title_b}"}

    def unlink_knowledge(self, id_a: str, id_b: str) -> dict:
        """Remove the bidirectional link between two knowledge items."""
        type_a, item_a = self._find_item_by_id(id_a)
        type_b, item_b = self._find_item_by_id(id_b)

        if item_a is None or type_a not in {"lesson", "decision", "playbook"}:
            return {"error": f"Item not found: {id_a}"}
        if item_b is None or type_b not in {"lesson", "decision", "playbook"}:
            return {"error": f"Item not found: {id_b}"}

        def _remove_related(target_id: str):
            def _mutate(entry: dict) -> dict:
                entry["related_ids"] = [
                    item_id for item_id in (entry.get("related_ids") or [])
                    if item_id != target_id
                ]
                return entry
            return _mutate

        updated_a = self._update_knowledge_item(type_a, id_a, _remove_related(id_b))
        updated_b = self._update_knowledge_item(type_b, id_b, _remove_related(id_a))
        if updated_a is None:
            return {"error": f"Item not found: {id_a}"}
        if updated_b is None:
            return {"error": f"Item not found: {id_b}"}

        title_a = self._knowledge_title(type_a, item_a)
        title_b = self._knowledge_title(type_b, item_b)
        return {"success": True, "message": f"Unlinked: {title_a} ↔ {title_b}"}

    def get_related_knowledge(self, item_id: str) -> dict:
        """Return all knowledge items linked to a lesson, decision, or playbook id."""
        lessons, decisions, playbooks = self._read_link_collections()
        item_type, item = self._find_item_in_collections(item_id, lessons, decisions, playbooks)
        if item is None or item_type is None:
            return {"error": f"Item not found: {item_id}"}

        related = []
        for related_id in item.get("related_ids", []):
            related_type, related_item = self._find_item_in_collections(
                related_id,
                lessons,
                decisions,
                playbooks,
            )
            if related_item is not None and related_type is not None:
                related.append(self._knowledge_view(related_type, related_item))

        return {
            "source": self._knowledge_view(item_type, item),
            "related": related,
            "total": len(related),
        }
