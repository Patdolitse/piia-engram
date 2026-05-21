# Engram v3.11.2 — Security Hardening

**Release date**: 2026-05-22

## Security Improvements

### Trust Boundary Enforcement
- **`export_identity_card()`** now uses `get_safe_profile()` — identity cards no longer leak fields listed in `trust_boundaries.restricted_fields`
- **`get_profile` MCP tool** default changed from `safe=False` to `safe=True` — callers must explicitly opt in to receive restricted fields
- **`engram://identity/profile` resource endpoint** now returns safe (filtered) profile

### Input Validation Whitelist
- All identity update methods (`update_profile`, `update_preferences`, `update_trust_boundaries`, `update_quality_standards`) now validate fields against a whitelist
- Unknown fields are silently dropped + logged as `warn` in audit log
- Prevents injection of arbitrary data through MCP tool calls

### Reconcile Anti-Poisoning
- `reconcile_memories`: files > 10 KB skipped + audit warning logged
- `reconcile_ai_configs`: config files > 50 KB skipped + audit warning logged
- Full reconcile summary written to audit log after each run

## Tests

- **193 passed** (up from 186 in v3.11.1)
- 7 new security-specific test cases:
  - Field whitelist rejection for profile, preferences, trust_boundaries, quality_standards
  - Identity card trust boundary enforcement
  - Reconcile large-file skip behavior
  - `_filter_allowed` static method correctness

## Upgrade Notes

`get_profile` MCP tool now defaults to `safe=True`. If your integration relies on receiving restricted fields (e.g. email, phone), pass `safe=False` explicitly. All other changes are backward-compatible.
