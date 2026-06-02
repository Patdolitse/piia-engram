# Permission Profile vNext — design (planned long task)

Status: **design only.** No production code in this pass. This builds on the
governance substrate that already exists; it does not replace it. Implementation
is gated on (a) the adversarial leakage review in §8 passing, and (b) explicit
user sign-off, because it changes what an agent can see.

## 1. What exists today (build on, don't reinvent)

From `governance.py`, `governance_runtime.py`, `governance_store.py`:

- **Three trust levels**, auto-assigned by self-reported client type
  (`classify_agent`): `private-self` (user/CLI), `trusted-local`
  (Claude Code / Codex / Cursor / Windsurf), `read-only-external` (unknown →
  fail-closed). Each has a `max_sensitivity` ceiling and a write policy.
- **Sensitivity gate** (`gate()`): every governed read drops items above the
  caller's ceiling; unlabeled items default to `work` (fail toward not-leaking).
- **Per-agent grants** (`GrantStore`) + forward-only `revoke`.
- **Disclosure receipts** (metadata-only, hash-chained `GovernanceLedger`): one
  receipt per governed read with counts of returned/excluded.
- **Opt-in flag** `ENGRAM_GOVERNANCE` (OFF by default; when off the read path is
  byte-identical to ungoverned).
- **`describe_caller_permissions`** for cold-start so an agent learns its own
  ceiling on the first message.

**Honest limit (already documented in `governance.py`):** agent identity over
MCP stdio is *self-reported*. This is a local-first governance boundary, **not a
hardened sandbox**. vNext does not change that; it makes the boundary richer and
more auditable, not cryptographically enforced.

## 2. vNext additions (the four new dimensions)

vNext adds three orthogonal inputs to caller resolution and one default-safe
behavior. All are **optional** and only take effect when `ENGRAM_GOVERNANCE` is
on; default-off keeps today's behavior.

### 2.1 `caller_role`

A coarse role label layered over the trust level, e.g. `owner`, `assistant`,
`reviewer`, `automation`. The role narrows (never widens) what the trust level
allows. Resolution order:

```text
effective_ceiling = min(trust_level.max_sensitivity, role.max_sensitivity)
effective_write    = most_restrictive(trust_level.write, role.write)
```

Roles are defined in a small static table (like trust levels), with optional
per-agent override via the existing GrantStore. Unknown role → most restrictive.

### 2.2 `workflow_stage`

The stage of the calling workflow, e.g. `explore`, `implement`, `review`,
`publish`. Stage tightens disclosure for high-blast-radius stages:

```text
explore / implement  → normal (role+trust ceiling)
review               → may read more verified knowledge, still no secrets
publish              → owner-only context; staging + sensitive fully excluded
```

Stage can only *lower* the ceiling, never raise it. Absent stage → treated as
`implement` (no extra tightening, no extra loosening).

### 2.3 `caller_depth`

How deep in a sub-agent chain the caller is (0 = top-level tool call,
1 = sub-agent, 2 = sub-sub-agent…). Depth drives the **default downgrade** in
§2.4. It is self-reported like everything else; it is a *defense-in-depth hint*,
not a trust anchor.

### 2.4 Sub-agent default downgrade

When `caller_depth > 0` (or the caller declares itself a sub-agent), the default
ceiling is downgraded one sensitivity step unless an explicit grant restores it:

```text
depth 0   ceiling = role+trust ceiling
depth >=1 ceiling = one step below, floor at "public"
```

A grant that "restores" depth-based access only restores the **depth step** — it
is still clamped by `min(trust, role, stage)`. A grant is never an override of
the trust/role/stage ceiling; it can only undo the depth downgrade up to that
ceiling. (Implementation must enforce this so a restore-grant can never widen
beyond today's gate. Covered by the T5 never-widen property test in §8.)

Rationale: a sub-agent spawned mid-task is the most likely place for prompt
injection to try to exfiltrate memory. Defaulting sub-agents to *less* access
limits blast radius, and the owner can still grant more explicitly.

## 3. Sensitive / staging filtering

Two filters compose with the sensitivity gate:

- **Sensitive filtering** — already enforced by `gate()` via the sensitivity
  ceiling. vNext does not weaken it. `secret` is never disclosed below
  `private-self`.
- **Staging filtering** — *new*: governed reads exclude `tier == "staging"`
  items for any caller below `private-self`, OR in `review`/`publish` stages,
  unless explicitly opted in. Today staging is surfaced based on sensitivity
  only; vNext makes "unverified knowledge stays with the owner" explicit. This
  also fixes the export gap noted in Task 6 (export currently includes staging).

Filtering is **subtractive only**: vNext can remove items a caller would have
seen; it can never add items the current gate would have excluded. This is the
core invariant that keeps it safe to layer on.

## 4. Audit receipts (extended)

Extend the existing disclosure receipt with the new dimensions, still
metadata-only (no content):

```json
{
  "receipt_id": "...", "ts": "...", "tool": "...",
  "agent_id": "...", "client_type": "...",
  "trust_level": "trusted-local",
  "caller_role": "assistant",
  "workflow_stage": "implement",
  "caller_depth": 1,
  "effective_ceiling": "work",
  "downgraded_by_depth": true,
  "returned_count": 12,
  "excluded_by_sensitivity": 3,
  "excluded_by_staging": 4
}
```

These append to the same hash-chained ledger, so every narrowing decision is
explainable after the fact.

## 5. Data model / API sketch (not implemented)

```python
@dataclass(frozen=True)
class CallerContext:
    agent_id: str
    client_type: str
    caller_role: str = ""        # "" => derive from trust level
    workflow_stage: str = ""     # "" => "implement"
    caller_depth: int = 0

def resolve_effective_profile(root, ctx: CallerContext) -> EffectiveProfile:
    """Pure function: (trust, role, stage, depth) -> ceiling + write + flags.

    Only ever returns a ceiling <= the current gate ceiling. Never widens."""
```

`resolve_caller` stays the trust-level anchor; `resolve_effective_profile` wraps
it. All read gates call the wrapper; with governance off it returns the
unrestricted profile (today's behavior).

## 6. Backward compatibility

- Default OFF (`ENGRAM_GOVERNANCE` unset) → no change at all.
- New `CallerContext` fields all default to the "no extra restriction" value, so
  an existing caller that sends none behaves exactly as today (modulo staging
  filtering, which is itself opt-in per §3).
- Receipts gain fields; readers that ignore unknown keys are unaffected.

## 7. Rollout phases

1. Spec + pure `resolve_effective_profile` with property tests proving
   *never-widen* (no production wiring).
2. Wire into read gates behind `ENGRAM_GOVERNANCE`, default-equivalent.
3. Add staging filter + sub-agent downgrade, each behind its own sub-flag.
4. Extend receipts; add an audit-log read tool (separate task).

## 8. Adversarial leakage review (required before any wiring)

A security subagent must attempt to defeat the design on paper. Threats to probe:

```text
T1  Role/stage spoofing — a hostile local process claims caller_role="owner",
    workflow_stage="explore", caller_depth=0 to maximize disclosure.
    Expectation: vNext cannot exceed the trust-level ceiling, which itself caps
    self-reported clients at "work". So spoofing role/stage can only *narrow*,
    never exceed, the existing ceiling. Confirm no path widens.

T2  Depth underreporting — a sub-agent claims depth=0 to dodge the downgrade.
    Expectation: downgrade is defense-in-depth, not the primary control; even at
    depth 0 the sensitivity gate still applies. Confirm the gate, not depth, is
    the hard guarantee.

T3  Staging opt-in abuse — caller sets the staging opt-in to pull unverified
    knowledge. Expectation: opt-in is only honored for private-self; confirm.

T4  Receipt as side channel — does any new receipt field echo entry content?
    Expectation: counts and labels only; confirm no content, no path, no id of
    excluded items.

T5  Compose-to-widen — can any combination of role+stage+depth produce a ceiling
    HIGHER than today's gate? Expectation: impossible by construction (min/most-
    restrictive composition). This is the property test to write first.

T6  Fail-open on bad input — malformed role/stage/depth must fail closed
    (most restrictive), never default to owner.
```

The design is acceptable to implement only if T1–T6 all resolve to "narrows or
no-op, never widens, never leaks content." Record the subagent's verdict here
before phase 2.

## 9. Non-goals

- Not a cryptographic identity / capability-token system (separate, later).
- Not a replacement for the OS-level trust boundary; a hostile local process
  with disk access can still read the store directly. This governs *agent-facing
  recall*, which is the in-scope threat surface.
