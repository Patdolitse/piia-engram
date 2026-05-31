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
- claude-review: passed    # independent Claude acceptance review
- tests: pass              # full pytest suite green (note the count)
- eval-gate: pass          # or n/a if no retrieval/quality-affecting change
- negative-control: passed # R1; or n/a if no security-sensitive change
- field-assertion-audit: passed  # R5; or n/a if no security-sensitive module touched
```

Required markers: `self-review`, `codex-review`, `claude-review`, `tests`
(must be a passing value). `eval-gate`, `negative-control` and
`field-assertion-audit` must be
present (use `n/a` when not applicable).

`negative-control` (R1) and `field-assertion-audit` (R5) encode the self-test
admission ruleset learned from the a5 corpus-encryption Codex audits, where
"the tests I wrote all pass" hid four plaintext-leak bugs:

- **negative-control**: for a security-sensitive change, the new regression
  tests must be shown to FAIL on the pre-fix code — a green test that also
  passes on the buggy code proves nothing. Mark `passed` only after running
  the new tests against the old commit and seeing them red.
- **field-assertion-audit**: for a change to a security-sensitive module
  (encryption / redaction / permission gating), every free-text field that
  could carry secret content must have an on-disk assertion proving it is not
  written in the clear. "It looks safe when I read the code" is not evidence.

Keep these files factual and minimal — they are public (tracked in the
repo). Put detailed review notes in the gitignored internal changelog, not
here.
