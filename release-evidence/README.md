# Release evidence index

Every release since v3.55.0 ships with a tracked, marker-only evidence file
in this directory. Each file declares which release checks passed (or were
explicitly n/a) for that version. The release gate
(`scripts/check_release_gate.py`) runs inside the publish workflow and
refuses to publish any release whose evidence file is missing or incomplete.

These markers are factual summaries of public-safe release checks. Detailed
working notes are kept locally and are not part of the repository.

## What marker-only means

The tracked evidence files are not raw logs. They contain only public-safe
marker lines such as check name, `passed` / `n/a`, and a release-gate fact that
can be verified from a fresh clone or a public workflow run. They must include
no local paths, private review notes, reviewer transcripts, API output dumps, or
unredacted scan logs. Detailed working notes are kept locally; detailed counts
remain local unless a maintainer intentionally promotes a public-safe summary.

## Verify a release yourself

- Run the gate check against a version's evidence file from a fresh clone:

  ```bash
  python scripts/check_release_gate.py --version 4.0.0
  ```

- Open the publish workflow run linked below for any release. The pipeline
  re-runs the public checks (fact sync, claim drift, trust claims, export
  sanitize, memory evals, release gate, sanitize scan, build, artifact scan)
  on a clean checkout before anything reaches PyPI.
- All check scripts live in [`scripts/`](../scripts/) and run offline.

## Index

| Version | Evidence | Release | Publish run |
|---------|----------|---------|-------------|
| v4.2.0 | [v4.2.0.md](v4.2.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.2.0) | [27548329982](https://github.com/Patdolitse/piia-engram/actions/runs/27548329982) |
| v4.1.0 | [v4.1.0.md](v4.1.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.1.0) | [27395714245](https://github.com/Patdolitse/piia-engram/actions/runs/27395714245) |
| v4.0.0 | [v4.0.0.md](v4.0.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.0.0) | [27333402009](https://github.com/Patdolitse/piia-engram/actions/runs/27333402009) |
| v3.56.0 | [v3.56.0.md](v3.56.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v3.56.0) | [27292409357](https://github.com/Patdolitse/piia-engram/actions/runs/27292409357) |
| v3.55.0 | [v3.55.0.md](v3.55.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v3.55.0) | [27264857478](https://github.com/Patdolitse/piia-engram/actions/runs/27264857478) |

Notes:

- **v3.54.0 was never published to PyPI.** Its publish run
  ([27256422328](https://github.com/Patdolitse/piia-engram/actions/runs/27256422328))
  failed the release gate, as disclosed in the v3.55.0 release notes. No
  evidence marker exists for it.
- Versions before v3.55.0 predate the per-release evidence mechanism; those
  releases were verified by the same CI pipeline without marker files.
