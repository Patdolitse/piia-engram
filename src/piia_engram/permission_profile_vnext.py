"""Permission Profile vNext — pure resolver (rollout phase 1).

Implements the ``resolve_effective_profile`` sketch from
``docs/specs/permission-profile-vnext-design.md`` §5 as a **pure, side-effect
free** function. This is rollout phase 1 only: *no production wiring*. The read
gates in ``governance_runtime`` are untouched and keep their current behavior, so
governance-OFF stays byte-identical and governance-ON behaves exactly as today.
This module exists so the never-widen composition can be proven by property
tests (§8 T5) before any gate calls it (phase 2, separately reviewed + gated).

Composition rule (design §5):

    effective_ceiling = min(trust, role, stage)   # then depth downgrade
    effective_write   = most_restrictive(trust, role)

Every output ceiling is ``<= the trust-level ceiling`` and every output write
policy is ``<= the trust-level write`` — the resolver can only *narrow*, never
widen, what the existing gate already allows. Unknown role/stage/depth fail
closed (most restrictive). A "restore" grant only undoes the depth downgrade and
is still clamped by ``min(trust, role, stage)`` — it can never widen the ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .governance import (
    DEFAULT_TRUST_LEVEL,
    SENSITIVITY_ORDER,
    TRUST_LEVELS,
    _sens_rank,
)

# Sensitivity ladder, low → high, derived from the canonical order so the two
# never drift apart.
_LADDER = [name for name, _ in sorted(SENSITIVITY_ORDER.items(), key=lambda kv: kv[1])]
_MIN_SENS = _LADDER[0]  # "public"

# Write policies ordered least → most permissive (restrictiveness rank).
_WRITE_RANK = {"no": 0, "proposed_only": 1, "verified": 2}
_RANK_WRITE = {v: k for k, v in _WRITE_RANK.items()}


# Role table — each role narrows (never widens) the trust ceiling/write.
ROLE_PROFILES: dict[str, dict[str, str]] = {
    "owner": {"max_sensitivity": "secret", "write": "verified"},
    "assistant": {"max_sensitivity": "work", "write": "proposed_only"},
    "reviewer": {"max_sensitivity": "work", "write": "no"},
    "automation": {"max_sensitivity": "public", "write": "no"},
}
# Unknown role → most restrictive (fail closed).
_DEFAULT_ROLE_PROFILE = {"max_sensitivity": _MIN_SENS, "write": "no"}

# Stage table — ceiling-only tightening for high-blast-radius stages.
STAGE_CEILINGS: dict[str, str] = {
    "explore": "secret",     # no extra tightening
    "implement": "secret",   # no extra tightening (the absent-stage default)
    "review": "work",        # no secrets while reviewing
    "publish": "public",     # publish context is owner-only; non-owners → public
}
_DEFAULT_STAGE = "implement"
# Stages that exclude unverified (staging-tier) knowledge regardless of caller.
_STAGING_EXCLUDING_STAGES = frozenset({"review", "publish"})


@dataclass(frozen=True)
class CallerContext:
    agent_id: str = ""
    client_type: str = ""
    caller_role: str = ""        # "" => derive from trust level (no extra narrowing)
    workflow_stage: str = ""     # "" => "implement" (no extra tightening)
    caller_depth: int = 0        # 0 => top-level; >=1 => sub-agent downgrade


@dataclass(frozen=True)
class EffectiveProfile:
    trust_level: str
    effective_ceiling: str
    effective_write: str
    downgraded_by_depth: bool = False
    staging_excluded: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _trust_profile(trust_level: str) -> dict:
    return TRUST_LEVELS.get(trust_level, TRUST_LEVELS[DEFAULT_TRUST_LEVEL])


def _normalized_depth(raw) -> int:
    """Coerce a self-reported depth. Malformed/negative → 1 (fail closed: treat
    as a sub-agent so the downgrade applies). bool excluded (int subclass)."""
    if isinstance(raw, bool):
        return 1
    if isinstance(raw, int) and raw >= 0:
        return raw
    return 1


def unrestricted_profile(trust_level: str = "private-self") -> EffectiveProfile:
    """The governance-OFF / owner profile: today's unrestricted behavior."""
    prof = _trust_profile(trust_level)
    return EffectiveProfile(
        trust_level=trust_level if trust_level in TRUST_LEVELS else DEFAULT_TRUST_LEVEL,
        effective_ceiling=prof["max_sensitivity"],
        effective_write=prof["write"],
        downgraded_by_depth=False,
        staging_excluded=False,
        reasons=("governance_off_or_unrestricted",),
    )


def resolve_effective_profile(
    trust_level: str,
    ctx: CallerContext,
    *,
    restore_depth: bool = False,
    staging_optin: bool = False,
) -> EffectiveProfile:
    """Resolve the effective profile for a caller. Pure; never widens.

    Args:
        trust_level: the trust-level anchor (from ``resolve_caller``).
        ctx: the self-reported caller context (role / stage / depth).
        restore_depth: an explicit grant undoing the depth downgrade (still
            clamped by ``min(trust, role, stage)`` — never widens beyond it).
        staging_optin: request to include staging-tier knowledge. Honored ONLY
            for ``private-self`` (the owner); ignored for everyone else.

    Returns an :class:`EffectiveProfile` whose ceiling is ``<=`` the trust
    ceiling and whose write is ``<=`` the trust write, always.
    """
    reasons: list[str] = []
    trust = trust_level if trust_level in TRUST_LEVELS else DEFAULT_TRUST_LEVEL
    if trust != trust_level:
        reasons.append("unknown_trust_failclosed")
    tprof = _trust_profile(trust)
    trust_ceiling = _sens_rank(tprof["max_sensitivity"])
    trust_write = _WRITE_RANK.get(tprof["write"], 0)

    # --- role -----------------------------------------------------------
    role = (ctx.caller_role or "").strip().lower()
    if not role:
        role_ceiling = trust_ceiling
        role_write = trust_write
    else:
        rprof = ROLE_PROFILES.get(role)
        if rprof is None:
            rprof = _DEFAULT_ROLE_PROFILE
            reasons.append("unknown_role_failclosed")
        role_ceiling = _sens_rank(rprof["max_sensitivity"])
        role_write = _WRITE_RANK.get(rprof["write"], 0)

    # --- stage (ceiling-only) -------------------------------------------
    stage = (ctx.workflow_stage or "").strip().lower() or _DEFAULT_STAGE
    if stage in STAGE_CEILINGS:
        stage_ceiling = _sens_rank(STAGE_CEILINGS[stage])
    else:
        stage_ceiling = _sens_rank(_MIN_SENS)  # unknown stage → most restrictive
        reasons.append("unknown_stage_failclosed")

    # --- compose ceiling (min) and write (most restrictive) -------------
    base_ceiling = min(trust_ceiling, role_ceiling, stage_ceiling)
    eff_write = min(trust_write, role_write)

    # --- depth downgrade ------------------------------------------------
    depth = _normalized_depth(ctx.caller_depth)
    downgraded = False
    eff_ceiling = base_ceiling
    if depth >= 1:
        downgraded_ceiling = max(0, base_ceiling - 1)  # one step below, floor public
        if restore_depth:
            # Restore only undoes the depth step; still clamped by base_ceiling.
            eff_ceiling = base_ceiling
            # Only note a restore when there was an actual downgrade to undo.
            if downgraded_ceiling < base_ceiling:
                reasons.append("depth_downgrade_restored")
        else:
            eff_ceiling = downgraded_ceiling
            downgraded = downgraded_ceiling < base_ceiling
            if downgraded:
                reasons.append("downgraded_by_depth")

    # --- staging exclusion ----------------------------------------------
    is_owner = trust == "private-self"
    stage_excludes_staging = stage in _STAGING_EXCLUDING_STAGES
    staging_excluded = (not is_owner) or stage_excludes_staging
    if staging_optin and is_owner and not stage_excludes_staging:
        staging_excluded = False
        reasons.append("staging_optin_owner")
    elif staging_optin and stage_excludes_staging:
        reasons.append("staging_optin_ignored_stage")
    elif staging_optin and not is_owner:
        reasons.append("staging_optin_ignored_non_owner")
    if staging_excluded:
        reasons.append("staging_excluded")

    # Safety clamp (belt and suspenders): the resolver must never widen.
    eff_ceiling = min(eff_ceiling, trust_ceiling)
    eff_write = min(eff_write, trust_write)

    return EffectiveProfile(
        trust_level=trust,
        effective_ceiling=_LADDER[eff_ceiling],
        effective_write=_RANK_WRITE[eff_write],
        downgraded_by_depth=downgraded,
        staging_excluded=staging_excluded,
        reasons=tuple(reasons),
    )
