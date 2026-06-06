# Context Governance Eval v1

This local benchmark checks that the context-governance preview surface remains
proposal-only, redacts synthetic secrets, and preserves owner-confirmation
boundaries.

Run:

```bash
python scripts/eval_context_governance.py --json
```

Current scope:

- synthetic fixtures only
- no real Engram store reads
- no writes to memory
- no publication, archive, merge, replay apply, registry update, or deploy

The eval is intentionally modest. It is a regression guard for preview
invariants, not a claim that context governance is stable or production-grade.
