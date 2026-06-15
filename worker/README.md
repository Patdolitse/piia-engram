# Telemetry Worker Configuration

This directory contains the Cloudflare Worker dashboard used for optional,
anonymous telemetry reporting.

`wrangler.toml` is intentionally public and uses placeholder infrastructure
identifiers. Do not commit real Cloudflare account, zone, D1 database, KV
namespace, route, or token values here.

Maintainers should keep concrete deployment configuration outside git, for
example:

```bash
cp worker/wrangler.toml worker/wrangler.private.toml
# edit worker/wrangler.private.toml with the real D1 database_id
npx wrangler deploy --config worker/wrangler.private.toml
```

`worker/wrangler.private.toml` and `worker/wrangler.local.toml` are gitignored.
Applying D1 migrations or deploying the Worker is an explicit owner action; it
is not part of the Python package release flow.
