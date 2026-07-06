# Memory Eval Suite v1

Memory Eval Suite v1 is a compact, offline, public-safe check for Engram's
memory quality. It runs the frozen recall baseline, the held-out recall set, the
admission baseline, the held-out admission set, and the synthetic agent-context-pack handoff eval in one command.

It is not a live-agent benchmark, not a competitor comparison, and not a claim
that a downstream model will always use the recalled knowledge correctly. It is
a regression and evidence gate for the local memory layer.

## How to Run

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
python scripts/run_memory_evals.py
```

For machine-readable output:

```powershell
python scripts/run_memory_evals.py --json
```

The default suite uses temporary isolated stores for recall and agent-context
evaluation. Admission evaluation uses synthetic candidate fixtures. The suite
does not read or write the user's live Engram store.

## Resume Pack Eval

The resume-pack eval checks whether `project_resume_pack.v1` can recover a
synthetic next action, keep verified project context in trusted context, keep
session-derived candidates in review-needed context, and avoid forbidden raw
fields.

```powershell
python scripts/eval_resume_pack.py --json
```

The eval uses synthetic fixtures and a temporary isolated Engram store. It does
not read or write the user's live Engram store, and it is not a live-agent
benchmark.

## Agent Context Pack Eval

The agent-context eval checks whether `agent_context_pack.v1` can provide
bounded, role-specific context for synthetic sub-agent handoffs without leaking
forbidden strings or treating memory as user approval.

```powershell
python scripts/eval_agent_context_pack.py --json
```

This standalone command is also covered by the default
`python scripts/run_memory_evals.py` gate. The eval uses synthetic fixtures and
temporary isolated Engram stores. It does not read or write the user's live
Engram store, and it is not a live-agent benchmark or autonomous-agent claim.

## Coverage

| Set | Fixture | Scope |
|---|---|---|
| Recall baseline | `tests/fixtures/recall_eval_v1.json` | Exact recall, paraphrase, decisions, identity preference, Chinese query, negative absent, version HEAD, project-scoped playbook |
| Recall held-out | `tests/fixtures/recall_eval_heldout_v1.json` | Cross-tool source, stale/superseded chains, project isolation, Chinese aliases, negative near-miss |
| Admission baseline | `tests/fixtures/admission_guard_v1.json` | Accept, duplicate, reject, review update, stage |
| Admission held-out | `tests/fixtures/admission_guard_heldout_v1.json` | Near-miss conflict, duplicate, transient marker, unclassified stage, good decision, good playbook, `without downtime` non-conflict |
| Agent context | `tests/fixtures/agent_context_pack_eval_cases.json` | Synthetic agent-context-pack handoff quality, forbidden-string checks, approval-boundary checks |

## Baseline

Recall and admission baselines verified on 2026-06-05:

| Recall set | Cases | Recall@k | MRR | Forbidden leak | Negative FP |
|---|---:|---:|---:|---:|---:|
| recall_eval_v1 | 8/8 | 1.000 | 1.000 | 0.000 | 0.000 |
| recall_eval_heldout_v1 | 10/10 | 1.000 | 1.000 | 0.000 | 0.000 |

| Admission set | Candidates | Actions | Failed expectations |
|---|---:|---|---:|
| admission_guard_v1 | 7 | accept=1, duplicate=1, reject=2, review_update=2, stage=1 | 0 |
| admission_guard_heldout_v1 | 8 | accept=3, duplicate=1, reject=1, review_update=2, stage=1 | 0 |

Agent-context baseline verified on 2026-06-29:

| Agent context eval | Cases | Forbidden leaks | Store isolated |
|---|---:|---:|---|
| agent_context_pack_eval.v1 | 2/2 | 0 | true |

## What This Caught

The held-out admission fixture exposed that English phrases such as `without
user confirmation` were not treated as negation markers in lesson conflict
detection. The fix added specific `without ... confirmation/approval` phrases to
the shared negation marker list and kept broad `without` out of the marker set so
phrases like `without downtime` do not become spurious conflicts.

The CLI path check also exposed that scripts must prioritize the worktree
`src/` path, otherwise a direct script run can accidentally import an installed
package instead of the local source tree.

## Limitations

- The corpus is still synthetic and small. It is a regression floor, not a
  broad benchmark claim.
- Recall precision is tracked but not a hard gate yet. Ranking/search changes
  should wait until held-out cases expose a measured gap.
- Admission guard mirrors conservative conflict heuristics. If production
  admission logic changes, this fixture should be reviewed for drift.
- The agent-context-pack eval is bounded synthetic handoff quality. It does not prove that a downstream agent will correctly use the pack, and it is not evidence of autonomous orchestration.
