# Release Evidence

Each release must have a `v<version>.md` file here recording that the
mandatory pre-release gates passed. The publish workflow runs
`scripts/check_release_gate.py`, which fails the publish job unless the
matching file exists and is complete, so the gate cannot be skipped even if
someone forgets the process.

This enforces the release playbook deterministically:
self-review -> Codex independent review -> Claude acceptance -> tests ->
sanitize -> allowlist -> build -> artifact private scan -> twine ->
applicability gates -> release.

Local maintainer releases should also run the artifact private-term scan after
building wheel/sdist:

```text
python scripts/check_release_artifact_private_terms.py dist
```

This scans the actual publishable package files with local gitignored private
patterns, so generated metadata, README copies, tests, or packaged artifacts
cannot carry maintainer-private Playbook content unnoticed.

## Format

Create `release-evidence/v<version>.md` with these lines, one marker per line:

```text
# Release evidence - v3.34.0

- self-review: passed
- codex-review: passed      # independent Codex review + tests
- claude-review: passed     # independent Claude acceptance review
- tests: pass               # full pytest suite green; include the count
- sanitize: passed          # release_sanitize_check high=0
- publish-allowlist: passed # all tracked public files covered
- package-build: passed     # wheel + sdist built
- artifact-private-scan: passed # built artifacts scanned for private terms
- twine-check: passed       # package metadata valid
- eval-gate: pass           # or n/a if no retrieval/quality-affecting change
- negative-control: passed  # R1; or n/a if no security-sensitive change
- field-assertion-audit: passed  # R5; or n/a if no security module touched
```

Required passing markers:

- `self-review`
- `codex-review`
- `claude-review`
- `tests`
- `sanitize`
- `publish-allowlist`
- `package-build`
- `artifact-private-scan`
- `twine-check`

Applicability markers:

- `eval-gate`
- `negative-control`
- `field-assertion-audit`

Applicability markers must be present. Use `n/a` only when the release scope
does not touch that risk class.

`negative-control` and `field-assertion-audit` encode the self-test admission
ruleset learned from the a5 corpus-encryption audits, where "the tests I wrote
all pass" hid plaintext-leak bugs:

- **negative-control**: for a security-sensitive change, the new regression
  tests must be shown to fail on the pre-fix code. A green test that also
  passes on the buggy code proves nothing.
- **field-assertion-audit**: for a change to a security-sensitive module
  (encryption, redaction, permission gating), every free-text field that could
  carry secret content must have an on-disk assertion proving it is not written
  in the clear.

Keep these files factual and minimal. They are public because they are tracked
in the repo. Put detailed review notes in the gitignored internal changelog,
not here.
