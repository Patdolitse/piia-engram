# Release evidence

Each release must have a `v<version>.md` file here recording that the
mandatory pre-release gates passed. The publish workflow runs
`scripts/check_release_gate.py`, which **fails the publish job** unless the
matching file exists and is complete — so the gate cannot be skipped, even
if someone forgets the process.

This enforces, deterministically, the process playbook
"新功能发布前置门槛（三关全过才发）": self-review → Codex independent
review + test → eval gate → release.

## Format

Create `release-evidence/v<version>.md` with these lines (each `marker: value`):

```
# Release evidence — v3.34.0

- self-review: passed
- codex-review: passed     # independent external (Codex) review + tests
- tests: pass              # full pytest suite green (note the count)
- eval-gate: pass          # or n/a if no retrieval/quality-affecting change
```

Required markers: `self-review`, `codex-review`, `tests` (must be a passing
value). `eval-gate` must be present (use `n/a` when not applicable).

Keep these files factual and minimal — they are public (tracked in the
repo). Put detailed review notes in the gitignored internal changelog, not
here.
