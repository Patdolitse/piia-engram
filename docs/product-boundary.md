# Product Boundary Contract

This document is the human-readable companion to
`docs/public-facts.json` -> `product_boundary_contract`. The JSON field is the
machine-readable source of truth; this page explains the same boundary for
maintainers and reviewers.

## Public Core

Public core is the stable Engram product identity:

- identity and preference context;
- lessons, decisions, playbooks, and project knowledge;
- session context, resume briefs, and continuity reports;
- local-first storage, backup, import, export, and redaction paths;
- the default Tier-1 MCP surface, including owner-gated export/write tools.

Core means common and context-budget friendly. It does not mean read-only.
Export, admin, and write side effects remain governed at runtime.

## Public Advanced And Adapters

Public advanced capabilities are productized but may be narrower, owner-gated,
or optional:

- governance and permission-profile inspection;
- recall, freshness, conflict, staging, and review workflows;
- local Reader and Dock UI adapters;
- local tool registry and diagnostics;
- reports, client validation, telemetry validation, and release evidence guards.

These are public product capabilities when documented as owner/admin/export,
proposal-only, or optional local surfaces. They must not be described as
universal cloud services or automatic remote actions.

## Optional Extensions

Optional dependency groups are public extension contracts. They may add local
UI, encryption, vector search, reader, or serving dependencies, but they do not
change the default local-first boundary and do not imply that a hosted service
exists.

## Private And Internal Exclusion

Private or maintainer-local implementation details are not part of the public
product contract. They must not appear in:

- public wheel or source-distribution package modules;
- identity-card, knowledge-report, or AGENTS export output;
- README, registry, release evidence, or public capability claims;
- public release-maintenance scripts or allowlist entries.

Public docs may say that a capability is owner-gated, local-only,
proposal-only, or optional. They should not expose maintainer-local paths,
private branch names, private runtime wiring, credentials, private strategy, or
implementation mechanisms that are not shipped as a public product surface.

## Machine Check

Run:

```bash
python scripts/check_product_boundary.py
```

The check is read-only. It verifies the product-boundary contract, public package
roots and imports, MCP surface labels, public export surface names, public docs,
release surface metadata, and the publish allowlist. Failure output is
metadata-only and does not echo unsafe matched text.
