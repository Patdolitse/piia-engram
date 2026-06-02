# Knowledge Quality Evaluation (Task 11)

Status: **scaffolding shipped** — a pure, metadata-only evaluator
(`src/piia_engram/quality_eval.py`), fixtures
(`tests/fixtures/quality/`), and tests (`tests/test_quality_eval.py`). The
semantic/truth judgement stays human + optional double-review; this layer only
removes mechanically-bad candidates so reviewers focus on real ones.

## 1. Two layers, on purpose

```text
Layer 1  metadata-only gate   (quality_eval.py — automatic, conservative)
         catches: too short, bare question, transient marker, no clear choice,
         too-few-step playbook, metadata inconsistencies.

Layer 2  human review         (engram review / HTML review page — existing)
         decides: is it true, generalizable, worth keeping → promote to verified.

Layer 2+ optional double-review (Claude + DeepSeek — prompt template below)
         used for high-stakes or ambiguous candidates before promotion.
```

Layer 1 never promotes and never deletes — it annotates. It mirrors the existing
extraction scorer's conservative stance ("create reviewable staging candidates,
not auto-convert everything").

## 2. Metadata-only rejection criteria (Layer 1)

Hard rejections (`accept = false`):

| reason | applies to | rule |
|--------|-----------|------|
| `too_short` | lesson | summary < 15 chars |
| `open_question` | lesson | summary is a bare question with no detail |
| `no_clear_choice` | decision | choice < 10 chars |
| `too_few_steps` | playbook | fewer than 2 steps |
| `transient_marker` | any | contains TODO/FIXME/tmp/scratch/placeholder/etc |
| `not_a_dict` | any | malformed candidate |

Soft warnings (do **not** reject; surfaced to reviewer):

| warning | rule |
|---------|------|
| `unclassified` | no domain AND no project (hard to retrieve later) |
| `missing_question` / `missing_reasoning` | decision lacks context |
| `verified_without_approval` | tier=verified but approval_status=pending (inconsistent) |

These are intentionally structural — no truth claims, no content mining.

## 3. Fixtures

- `tests/fixtures/quality/high_quality.json` — a lesson, a decision, a playbook
  that should pass Layer 1.
- `tests/fixtures/quality/low_quality.json` — one example per hard-rejection
  reason, each tagged with `_expected_reason` so the test asserts the *specific*
  reason fires (guards against the gate rejecting for the wrong cause).

## 4. Claude / DeepSeek double-review prompt template (Layer 2+)

Use for ambiguous/high-stakes candidates. Run the same prompt through Claude and
DeepSeek independently; promote only if both return `keep` (or escalate the
disagreement to the user).

```text
You are reviewing a candidate entry for a user's long-term, cross-tool memory.
Memory should be DURABLE, GENERALIZABLE, and CORRECT. Be skeptical: it is better
to reject a weak candidate than to pollute long-term memory.

Candidate (metadata + content):
<paste the candidate entry: type, summary/question+choice/steps, domain, source>

Answer as strict JSON:
{
  "verdict": "keep | reject | revise",
  "is_generalizable": true|false,     // useful beyond this one moment/task?
  "is_durable": true|false,           // still true next month? not transient?
  "is_self_contained": true|false,    // understandable without the chat it came from?
  "duplicate_risk": "low|medium|high",
  "suggested_domain": "...",          // if missing/weak
  "revision": "...",                  // required if verdict=revise
  "reasons": ["..."]                  // short, concrete
}

Rules:
- "keep" requires is_generalizable AND is_durable AND is_self_contained all true.
- Default to "reject" when uncertain.
- Do NOT keep transient debugging notes, open questions, or one-off task chatter.
- Do NOT invent facts; judge only what is in the candidate.
```

Aggregation:

```text
both keep            -> eligible for promotion (still a human action)
either reject        -> stays in staging / archived
either revise        -> return suggested revision to the user
keep vs reject split -> escalate to user with both rationales
```

## 5. Follow-up (when wired)

- ✅ **Implemented** — Layer-1 verdicts are surfaced in the HTML review page
  (`reports_review.generate_review_page`): hard reasons render as a red flag
  badge, soft warnings as an amber badge, and the tokens are HTML-escaped like
  every other field. It only annotates — it never auto-promotes or rejects.
  Covered by `tests/test_quality_report.py`. An aggregate, metadata-only
  `quality_eval.build_quality_report` helper is also available for review tooling.
- Optionally block one-click promotion of entries with hard reasons until edited.
  *(still future work — Layer 1 currently annotates only.)*
- Keep the double-review as an explicit, user-invoked step — never automatic.

## 6. Non-goals

- Not a truth oracle. Layer 1 is mechanical; Layer 2/2+ is judgement.
- No automatic deletion; rejection means "stays out of verified," not "erased."
