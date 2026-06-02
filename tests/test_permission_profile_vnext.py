"""Adversarial + property tests for the vNext resolver (Phase 8, rollout phase 1).

These prove the design's never-widen guarantee (§8 T1-T6) on the *pure* resolver
before any gate is wired to it. The resolver is NOT used by production read gates
in this pass; default-off behavior is therefore unchanged (asserted separately in
test_governance_runtime.py, which this pass does not touch).
"""

from __future__ import annotations

import itertools

import pytest

from piia_engram import governance
from piia_engram.permission_profile_vnext import (
    CallerContext,
    EffectiveProfile,
    ROLE_PROFILES,
    STAGE_CEILINGS,
    resolve_effective_profile,
    unrestricted_profile,
)

_SENS = governance.SENSITIVITY_ORDER
_WRITE_RANK = {"no": 0, "proposed_only": 1, "verified": 2}

ALL_TRUST = list(governance.TRUST_LEVELS)
ALL_ROLES = [""] + list(ROLE_PROFILES) + ["bogus-role"]
ALL_STAGES = [""] + list(STAGE_CEILINGS) + ["bogus-stage"]
ALL_DEPTHS = [0, 1, 2, -1, "x", True]


def _ceiling_rank(profile: EffectiveProfile) -> int:
    return _SENS[profile.effective_ceiling]


# --- T5: compose-to-widen is impossible (the property test, written first) ---

def test_T5_never_widens_ceiling_or_write():
    for trust, role, stage, depth, restore in itertools.product(
        ALL_TRUST, ALL_ROLES, ALL_STAGES, ALL_DEPTHS, [False, True]
    ):
        ctx = CallerContext(caller_role=role, workflow_stage=stage, caller_depth=depth)
        eff = resolve_effective_profile(trust, ctx, restore_depth=restore)
        trust_ceiling = _SENS[governance.TRUST_LEVELS[trust]["max_sensitivity"]]
        trust_write = _WRITE_RANK[governance.TRUST_LEVELS[trust]["write"]]
        assert _ceiling_rank(eff) <= trust_ceiling, (trust, role, stage, depth, restore)
        assert _WRITE_RANK[eff.effective_write] <= trust_write, (trust, role, stage, depth)


# --- T1: role/stage spoofing cannot exceed the trust ceiling ---

def test_T1_owner_role_on_external_trust_cannot_widen():
    # A hostile external client claims caller_role="owner" to grab everything.
    ctx = CallerContext(client_type="web", caller_role="owner",
                        workflow_stage="explore", caller_depth=0)
    eff = resolve_effective_profile("read-only-external", ctx)
    # read-only-external ceiling is "public" — role claim cannot raise it.
    assert eff.effective_ceiling == "public"


def test_T1_assistant_role_narrows_owner_trust():
    ctx = CallerContext(caller_role="assistant")
    eff = resolve_effective_profile("private-self", ctx)
    # owner trust would allow secret, but assistant role caps at work.
    assert eff.effective_ceiling == "work"


# --- T2: depth underreporting still bounded by the gate ceiling ---

def test_T2_depth_zero_still_capped_by_trust():
    # Even claiming depth=0 (no downgrade), the ceiling is the trust ceiling.
    ctx = CallerContext(caller_role="", workflow_stage="", caller_depth=0)
    eff = resolve_effective_profile("trusted-local", ctx)
    assert eff.effective_ceiling == "work"  # trust ceiling, not higher


def test_T2_subagent_downgrades_one_step():
    ctx = CallerContext(caller_depth=1)
    eff = resolve_effective_profile("private-self", ctx)
    # secret -> one step below = private.
    assert eff.effective_ceiling == "private"
    assert eff.downgraded_by_depth is True


def test_restore_grant_only_undoes_depth_not_widens():
    ctx = CallerContext(caller_role="assistant", caller_depth=2)
    # assistant caps at work; depth downgrades to public; restore brings it back
    # up to the assistant cap (work), never above.
    eff = resolve_effective_profile("private-self", ctx, restore_depth=True)
    assert eff.effective_ceiling == "work"


# --- T3: staging opt-in only honored for the owner ---

def test_T3_staging_optin_ignored_for_non_owner():
    ctx = CallerContext(client_type="codex")
    eff = resolve_effective_profile("trusted-local", ctx, staging_optin=True)
    assert eff.staging_excluded is True
    assert "staging_optin_ignored_non_owner" in eff.reasons


def test_T3_staging_optin_honored_for_owner():
    ctx = CallerContext(client_type="cli")
    eff = resolve_effective_profile("private-self", ctx, staging_optin=True)
    assert eff.staging_excluded is False


def test_staging_excluded_in_review_publish_even_for_owner():
    for stage in ("review", "publish"):
        ctx = CallerContext(workflow_stage=stage)
        eff = resolve_effective_profile("private-self", ctx)
        assert eff.staging_excluded is True


# --- T4: profile carries no content (it only carries labels/flags) ---

def test_T4_profile_fields_are_labels_only():
    ctx = CallerContext(caller_role="assistant", workflow_stage="review", caller_depth=1)
    eff = resolve_effective_profile("trusted-local", ctx)
    blob = repr(eff)
    # Only enum-like labels / booleans / reason codes — assert known vocab.
    assert eff.effective_ceiling in _SENS
    assert eff.effective_write in _WRITE_RANK
    assert isinstance(eff.downgraded_by_depth, bool)
    assert isinstance(eff.staging_excluded, bool)


# --- T6: malformed inputs fail closed (most restrictive), never to owner ---

def test_T6_unknown_role_fails_closed_to_public():
    ctx = CallerContext(caller_role="superuser")
    eff = resolve_effective_profile("private-self", ctx)
    assert eff.effective_ceiling == "public"
    assert eff.effective_write == "no"
    assert "unknown_role_failclosed" in eff.reasons


def test_T6_unknown_stage_fails_closed():
    ctx = CallerContext(workflow_stage="exfiltrate")
    eff = resolve_effective_profile("private-self", ctx)
    assert eff.effective_ceiling == "public"
    assert "unknown_stage_failclosed" in eff.reasons


def test_T6_malformed_depth_treated_as_subagent():
    ctx = CallerContext(caller_depth="not-an-int")
    eff = resolve_effective_profile("private-self", ctx)
    # malformed depth -> treated as depth>=1 -> downgraded from secret to private.
    assert eff.effective_ceiling == "private"


def test_T6_unknown_trust_fails_closed():
    ctx = CallerContext()
    eff = resolve_effective_profile("nonsense-level", ctx)
    assert eff.effective_ceiling == "public"
    assert "unknown_trust_failclosed" in eff.reasons


# --- unrestricted (governance-off equivalent) ---

def test_unrestricted_profile_is_owner_full():
    eff = unrestricted_profile()
    assert eff.effective_ceiling == "secret"
    assert eff.effective_write == "verified"
    assert eff.staging_excluded is False
