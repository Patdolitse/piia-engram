# v3.14.3 Milestone Evaluation

Closes the loop on the v3.13.2 milestone review by asking DeepSeek to verify
whether the v3.14.1/2/3 fixes actually address what v3.13.2 flagged.

## What's here

| File | Purpose |
|------|---------|
| `evidence_pack.md` | Curated facts + the v3.13.2 → v3.14.3 checklist. The evaluator reads this **plus** the source files listed below. |
| `run_evaluation.py` | Self-contained runner. Reads `.env`, calls DeepSeek 3 times, writes JSON + Markdown report. |
| `REPORT.md` | (generated) Human-readable consolidated report. |
| `results_<timestamp>.json` | (generated) Full per-pass response + averaged scores. |
| `raw_log_<timestamp>.jsonl` | (generated) One line per API call for audit. |

## How to run (give this to codex)

```bash
cd "E:/Personal Intelligence Identity Asset/engram"
PYTHONIOENCODING=utf-8 python experiments/evaluations/v3.14.3/run_evaluation.py
```

Default is 3 passes. To run 1 pass for a quick smoke test:

```bash
... run_evaluation.py 1
```

The script auto-loads `experiments/benchmarks/round3/.env` for the DeepSeek
API key (reused from the v3.13.2-era benchmarks).

## What evaluator sees

The runner concatenates these files into one big prompt:

- `evidence_pack.md` (this directory)
- `CHANGELOG.md`
- `docs/architecture.md`, `docs/comparison.md`, `docs/coverage_baseline_v3.14.2.md`
- `README.md`
- All 7 new/refactored core modules: `core.py`, `storage.py`, `crypto.py`, `retrieval.py`, `context.py`, `reconcile.py`, `reports.py`, `compat.py`, `mcp_server.py`
- New tests: `tests/test_crypto.py`, `tests/test_mcp_tools.py`, `tests/test_review_page_xss.py`

The evaluator is then asked to:
1. Score 6 dimensions (architecture / testing / security / documentation / positioning / overall) on 0-10
2. Verify each of the 21 issues from v3.13.2 — `fixed` / `partial` / `regression` / `unverified`
3. Answer 7 specific Qs about correctness (PBKDF2 implementation, path validation, doc honesty…)
4. List 3 new findings not raised in v3.13.2
5. Suggest 3 priorities for v3.15.0 / v3.14.4

The full prompt schema is in `run_evaluation.py` (`USER_PROMPT_TEMPLATE`).

## Cost / latency expectation

DeepSeek-chat:
- Prompt size ≈ 80–100 K characters (≈ 30 K tokens)
- 3 passes × ≈ 4 K output tokens each
- Wall clock ≈ 2–4 minutes total
- API cost ≈ $0.20–0.40 total (at deepseek-chat list prices)

If you see retries in stderr, that's normal — the runner backs off and retries
3 times before giving up.

## After it runs

`REPORT.md` is the thing to read. It shows:
- Side-by-side scores (v3.13.2 5-evaluator average vs this run)
- Per-pass detail with key Q&A + new findings + suggested next priorities
- Pointer to the raw JSON for audit

If you want to compare against v3.13.2 in narrative form, also re-read
[`docs/milestone_review_v3.13.2.md`](../../../docs/milestone_review_v3.13.2.md).
