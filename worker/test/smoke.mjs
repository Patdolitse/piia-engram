/**
 * Local worker smoke harness — node-only, NO Cloudflare / wrangler / network.
 *
 * Actually executes the worker's default fetch() against a mock D1 to prove the
 * remote-rollout-critical paths without standing up a real database:
 *   1. v1.1 full insert path (all P0 + P1 columns present)
 *   2. v1 fallback (P1 columns missing → P0 insert)
 *   3. legacy fallback (P0 + P1 missing → base insert)
 *   4. rejected content field (an unexpected/content field → 422, never stored)
 *
 * This is intentionally NOT part of the pytest suite (so CI stays dependency
 * light). Run manually:  node worker/test/smoke.mjs
 *
 * The always-on guard is tests/test_telemetry_worker_smoke.py (static).
 */

import worker from '../src/index.js';

let failures = 0;
function check(name, cond, extra = '') {
  const ok = !!cond;
  if (!ok) failures++;
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name}${extra ? '  — ' + extra : ''}`);
}

// --- Mock D1 ----------------------------------------------------------------
// Emulates SQLite column-missing errors: an INSERT naming a column that isn't in
// `present` throws `... has no column named <col>`, which is exactly what the
// worker's tiered fallback inspects.
function mockDB(present) {
  const stored = [];
  const presentSet = new Set(present);
  return {
    stored,
    prepare(sql) {
      const insertMatch = sql.match(/INSERT\s+INTO\s+events\s*\(([^)]*)\)/i);
      const cols = insertMatch
        ? insertMatch[1].split(',').map((c) => c.trim()).filter(Boolean)
        : null;
      return {
        bind(...vals) {
          return {
            async run() {
              if (cols) {
                const missing = cols.find((c) => !presentSet.has(c));
                if (missing) {
                  throw new Error(`table events has no column named ${missing}`);
                }
                const row = {};
                cols.forEach((c, i) => { row[c] = vals[i]; });
                stored.push(row);
              }
              return { success: true };
            },
          };
        },
      };
    },
  };
}

const BASE = ['daily_id', 'version', 'tool_calls', 'knowledge', 'os', 'py', 'tier', 'schema_v'];
const P0 = ['prev_version', 'session_type', 'install_age_bucket', 'error_categories'];
const P1 = ['contract_version', 'version_adoption', 'activation_state', 'returning_bucket', 'error_trend'];

function eventBody(extra = {}) {
  return JSON.stringify({
    schema: 1, daily_id: 'smoke_000', engram_version: '3.47.0',
    prev_version: '3.45.2', session_type: 'regular', install_age_bucket: '31_plus_days',
    error_categories: { timeout: 1 }, contract_version: '1.1', version_adoption: 'upgrade',
    activation_state: 'activated', returning_bucket: 'returning', error_trend: 'up',
    os_platform: 'linux', python_version: '3.12', tools_tier: 'core', ...extra,
  });
}

async function postEvent(db, body) {
  const req = new Request('https://smoke.local/v1/events', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'content-length': String(body.length) },
    body,
  });
  const res = await worker.fetch(req, { DB: db });
  return res;
}

// 1. Full v1.1 path
{
  const db = mockDB([...BASE, ...P0, ...P1]);
  const res = await postEvent(db, eventBody());
  const row = db.stored[0] || {};
  check('v1.1 full insert → 201', res.status === 201, `status=${res.status}`);
  check('v1.1 full insert stored P1 columns',
    row.contract_version === '1.1' && row.version_adoption === 'upgrade' && row.error_trend === 'up');
}

// 2. v1 fallback (P1 columns missing)
{
  const db = mockDB([...BASE, ...P0]);
  const res = await postEvent(db, eventBody());
  const row = db.stored[0] || {};
  check('v1 fallback → 201', res.status === 201, `status=${res.status}`);
  check('v1 fallback stored P0 but not P1',
    row.prev_version === '3.45.2' && !('contract_version' in row));
}

// 3. legacy fallback (P0 + P1 missing)
{
  const db = mockDB([...BASE]);
  const res = await postEvent(db, eventBody());
  const row = db.stored[0] || {};
  check('legacy fallback → 201', res.status === 201, `status=${res.status}`);
  check('legacy fallback stored base only',
    row.daily_id === 'smoke_000' && !('prev_version' in row) && !('contract_version' in row));
}

// 4. rejected content field (unexpected field → 422, nothing stored)
{
  const db = mockDB([...BASE, ...P0, ...P1]);
  const res = await postEvent(db, eventBody({ summary: 'a leaked lesson body' }));
  check('content field rejected → 422', res.status === 422, `status=${res.status}`);
  check('content field never stored', db.stored.length === 0);
}

console.log(failures === 0 ? '\nALL SMOKE CHECKS PASSED' : `\n${failures} CHECK(S) FAILED`);
process.exit(failures === 0 ? 0 : 1);
