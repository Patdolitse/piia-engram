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
| v4.17.0 | [v4.17.0.md](v4.17.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.17.0) | pending (filled after publish) |
| v4.16.0 | [v4.16.0.md](v4.16.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.16.0) | [31984998929](https://github.com/Patdolitse/piia-engram/actions/runs/31984998929) |
| v4.15.0 | [v4.15.0.md](v4.15.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.15.0) | [31957504387](https://github.com/Patdolitse/piia-engram/actions/runs/31957504387) |
| v4.14.1 | [v4.14.1.md](v4.14.1.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.14.1) | [31088273284](https://github.com/Patdolitse/piia-engram/actions/runs/31088273284) |
| v4.14.0 | [v4.14.0.md](v4.14.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.14.0) | [29096890037](https://github.com/Patdolitse/piia-engram/actions/runs/29096890037) |
| v4.13.0 | [v4.13.0.md](v4.13.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.13.0) | [28778987915](https://github.com/Patdolitse/piia-engram/actions/runs/28778987915) |
| v4.12.0 | [v4.12.0.md](v4.12.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.12.0) | [28015677433](https://github.com/Patdolitse/piia-engram/actions/runs/28015677433) |
| v4.11.0 | [v4.11.0.md](v4.11.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.11.0) | [27952067764](https://github.com/Patdolitse/piia-engram/actions/runs/27952067764) |
| v4.9.1 | [v4.9.1.md](v4.9.1.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.9.1) | [27874862098](https://github.com/Patdolitse/piia-engram/actions/runs/27874862098) |
| v4.9.0 | [v4.9.0.md](v4.9.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.9.0) | [27867545863](https://github.com/Patdolitse/piia-engram/actions/runs/27867545863) |
| v4.8.0 | [v4.8.0.md](v4.8.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.8.0) | [27861585966](https://github.com/Patdolitse/piia-engram/actions/runs/27861585966) |
| v4.7.0 | [v4.7.0.md](v4.7.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.7.0) | [27832776137](https://github.com/Patdolitse/piia-engram/actions/runs/27832776137) |
| v4.6.2 | [v4.6.2.md](v4.6.2.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.6.2) | [27813496013](https://github.com/Patdolitse/piia-engram/actions/runs/27813496013) |
| v4.6.1 | [v4.6.1.md](v4.6.1.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.6.1) | [27780979827](https://github.com/Patdolitse/piia-engram/actions/runs/27780979827) |
| v4.6.0 | [v4.6.0.md](v4.6.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.6.0) | [27771504169](https://github.com/Patdolitse/piia-engram/actions/runs/27771504169) |
| v4.5.1 | [v4.5.1.md](v4.5.1.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.5.1) | [27761811691](https://github.com/Patdolitse/piia-engram/actions/runs/27761811691) |
| v4.5.0 | [v4.5.0.md](v4.5.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.5.0) | [27750340841](https://github.com/Patdolitse/piia-engram/actions/runs/27750340841) |
| v4.4.0 | [v4.4.0.md](v4.4.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.4.0) | [27731479598](https://github.com/Patdolitse/piia-engram/actions/runs/27731479598) |
| v4.3.0 | [v4.3.0.md](v4.3.0.md) | [release notes](https://github.com/Patdolitse/piia-engram/releases/tag/v4.3.0) | [27675938146](https://github.com/Patdolitse/piia-engram/actions/runs/27675938146) |
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
