# Runbook: Post-Push Closeout Dry Run

This runbook covers the local dry-run helper for the workspace closeout required
after a future owner-approved `git push`.

```bash
python scripts/post_push_closeout.py
python scripts/post_push_closeout.py --json
python scripts/post_push_closeout.py --run-collect
```

The helper computes the values needed for the parent workspace
`PROJECT_REGISTRY.md` auto-status block:

- package version from `pyproject.toml`
- latest local tag
- test count from `docs/public-facts.json` or optional collect-only pytest
- current date
- optional GitHub stars when `--query-stars` is explicitly supplied

It is dry-run only. It does not edit `PROJECT_REGISTRY.md`, create commits,
push, tag, publish, write registry entries, deploy Workers, or post public
comments. Use it before asking the owner to approve a public push, and again
after the owner-approved push before doing the actual workspace registry edit.

Default test count comes from `docs/public-facts.json` so the dry-run stays
fast. `--run-collect` runs pytest collect-only and prints the raw pytest tail;
use that when you need a freshly computed count instead of the manifest value.
