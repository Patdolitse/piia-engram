# PIIA Engram v3.10.1 — Context Quality Hotfix

## What's Fixed

### Lesson bucket allocation bug (critical)
- `get_relevant_lessons` used fixed slot allocation even when buckets were empty, causing only 2 of 9 lessons to appear in cold-start context
- Empty buckets now release their slots to non-empty buckets
- Result: lesson coverage went from 2/9 to 8/8

### Domain pollution from file paths
- `add_decision` now sanitizes the `project` field, extracting the directory name from full file paths (e.g. `E:\...\engram` -> `engram`)
- Prevents garbage entries in domain distribution and knowledge overview

### Missing profile guidance
- `generate_context` now shows a setup prompt when profile is empty instead of silently omitting the identity section
- Users immediately know they need to run `engram setup` or `update_identity`

## Impact

| Metric | Before | After |
|--------|--------|-------|
| Context length | ~600 chars | 2600+ chars |
| Lessons in context | 2/9 | 8/8 |
| Identity section | Missing | Full |
| Preferences section | Missing | Full |
| Quality standards | Missing | Full |

## Upgrade

```bash
pip install --upgrade piia-engram
```

**Full Changelog**: https://github.com/Patdolitse/engram/compare/v3.10.0...v3.10.1
