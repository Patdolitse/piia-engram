# Recall Eval v1 Baseline

Recall Eval v1 is a small, deterministic, public-safe benchmark for Engram's
offline recall quality. It uses synthetic memory stores and ID-based labels to
answer one narrow question:

> Given a labeled query, does the real `search_knowledge` surface the expected
> knowledge ID without surfacing explicitly forbidden IDs?

It is not a live-agent benchmark, not a competitor comparison, and not a claim
that a downstream model will always use the recalled knowledge correctly.

## How to Run

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& 'C:\Users\pp3x3\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts/eval_recall.py --json
```

The fixture is stored at `tests/fixtures/recall_eval_v1.json`. The harness uses
temporary isolated stores and sets `ENGRAM_TEST=1` internally so local data
fragmentation warnings from a developer machine do not pollute the benchmark
output.

## Seed Scenarios

Recall Eval v1 currently has 8 cases:

| Scenario | Purpose |
|---|---|
| exact_lesson | Exact topic recall for a durable lesson |
| paraphrase_alias | Alias/paraphrase recall (`py` -> Python-related memory) |
| decision_rationale | Decision recall by rationale-oriented query |
| identity_preference | User preference / identity-style memory recall |
| chinese_query | Chinese token recall |
| negative_absent | Empty result when no memory should match |
| version_supersession | Current HEAD surfaces while the superseded item is forbidden |
| project_isolation | Project-scoped playbook from project A does not leak project B |

## Baseline

Verified on 2026-06-05:

| Metric | Value |
|---|---:|
| Cases | 8/8 passed |
| Mean precision@k | 0.771 |
| Mean recall@k | 1.000 |
| Mean MRR | 1.000 |
| Forbidden leak rate | 0.000 |
| Negative false-positive rate | 0.000 |

V1 thresholds are intentionally loose except for forbidden leaks:

| Threshold | Value |
|---|---:|
| Minimum mean recall@k | 0.800 |
| Minimum mean MRR | 0.700 |
| Maximum forbidden leak rate | 0.000 |
| Maximum negative false-positive rate | 0.100 |

## Limitations

- The corpus is tiny and synthetic; it catches regressions, not broad product
  quality.
- Scoring is exact ID matching. This keeps the benchmark debuggable, but it
  does not judge answer prose or model behavior.
- The version-supersession case uses a real `supersedes` relation edge and the
  same HEAD-collapse helper used by the recall surface.
- The project-isolation case currently exercises playbook project scope because
  lesson search does not define project filtering semantics.
- Precision is useful as a trend, but V1 does not fail on low precision as long
  as the expected item is ranked first and no forbidden item leaks.

## Next Ratchets

1. Keep the held-out fixture separate from V1 and do not tune V1 to fit new
   cases.
2. Add active contradiction cases, where both entries are active but only the
   current decision should rank first.
3. Record per-version baseline deltas before publishing broader MCIC evidence.
