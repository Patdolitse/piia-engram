/**
 * Node harness for worker/src/counts.js — no Cloudflare, no network, mock D1.
 * Run: node worker/test/counts.mjs   (exit code 1 on any failure)
 */
import { handleCounts, validateCounts, purgeOldCounts, TOOL_NAMES, MAX_COUNTS_BYTES, RETENTION_DAYS }
  from '../src/counts.js';
import { dayOffset } from '../src/usage.js';
import worker from '../src/index.js';

// No network in this harness.
globalThis.fetch = async () => { throw new Error('offline'); };

let failures = 0;
function check(name, cond, extra = '') {
  if (!cond) failures++;
  console.log(`${cond ? 'ok  ' : 'FAIL'}  ${name}${extra ? '  — ' + extra : ''}`);
}

function mockDB() {
  const calls = [];
  return {
    calls,
    prepare(sql) {
      return {
        bind(...vals) {
          calls.push({ sql, vals });
          return { async run() { return { success: true }; } };
        },
      };
    },
  };
}

const today = new Date().toISOString().slice(0, 10);
const good = () => ({
  schema: 'stats/2', daily_id: 'a'.repeat(16), date: dayOffset(today, -1), version: '4.30.0',
  os: 'windows', python: '3.12', tier: 'core', install_age: '8_30_days', adoption: 'same',
  tools: { get_resume_brief: { ok: '1_4', err: '0' }, other: { ok: '5_9', err: '1_4' } },
  errors: { timeout: '1_4' }, knowledge: { lessons: '10_49', decisions: '1_9', domains: '1_9' },
});

function request(body, headers = {}) {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  return new Request('https://t.local/v1/counts', { method: 'POST', body: text,
    headers: { 'content-type': 'application/json', 'content-length': String(text.length), ...headers } });
}
const post = (db, body, env = {}) => handleCounts(request(body), { DB: db, ...env });

{
  const db = mockDB();
  const res = await post(db, good());
  check('valid row → 204', res.status === 204, `status=${res.status}`);
  const ins = db.calls.find((c) => /INSERT OR IGNORE INTO counts/.test(c.sql));
  check('valid row inserted once with the eleven columns', db.calls.length === 1 && !!ins
    && ins.vals.length === 11 && ins.vals[0] === 'a'.repeat(16) && ins.vals[1] === dayOffset(today, -1));
  check('the three maps are stored as JSON text', JSON.parse(ins.vals[8]).other.ok === '5_9'
    && JSON.parse(ins.vals[9]).timeout === '1_4' && JSON.parse(ins.vals[10]).lessons === '10_49');
  check('a repeat is left to the primary key', /INSERT OR IGNORE/.test(ins.sql));
}
{
  const db = mockDB();
  const res = await post(db, { ...good(), notes: 'free text' });
  check('extra field → 422', res.status === 422 && db.calls.length === 0);
  const { knowledge, ...missing } = good();
  check('missing field → 422', (await post(mockDB(), missing)).status === 422);
}
const BAD = [
  ['schema', 'stats/1'], ['daily_id', 'a'.repeat(32)], ['daily_id', 'not hex not hex!'],
  ['date', '2020-01-01'], ['date', dayOffset(today, 2)], ['version', '4.2 /home/me'],
  ['os', 'win32'], ['python', '2.7'], ['tier', 'C:\\tools'], ['install_age', 'yesterday'],
  ['adoption', 'maybe'], ['tools', {}], ['tools', []], ['tools', 'get_recall'],
  ['tools', { 'my private tool': { ok: '1_4', err: '0' } }],
  ['tools', { get_recall: { ok: 3, err: '0' } }],
  ['tools', { get_recall: { ok: '3', err: '0' } }],
  ['tools', { get_recall: { ok: '1_4' } }],
  ['tools', { get_recall: { ok: '1_4', err: '0', note: 'x' } }],
  ['errors', { 'Traceback (most recent call last)': '1_4' }], ['errors', { timeout: 2 }],
  ['errors', null], ['knowledge', { lessons: 47 }], ['knowledge', { notes: '0' }],
  ['knowledge', { lessons: 'a few' }], ['version', 4],
];
for (const [field, value] of BAD) {
  const db = mockDB();
  const res = await post(db, { ...good(), [field]: value });
  check(`bad ${field} ${JSON.stringify(value).slice(0, 40)} → 422`, res.status === 422 && db.calls.length === 0,
    `status=${res.status}`);
}
{
  check('invalid JSON → 400', (await post(mockDB(), 'not json')).status === 400);
  check('an array body → 422', (await post(mockDB(), '[]')).status === 422);
  const big = JSON.stringify({ ...good(), pad: 'x'.repeat(MAX_COUNTS_BYTES) });
  check('oversize → 413', (await post(mockDB(), big)).status === 413);
  // A lying content-length does not get a large body past the limit.
  const lying = new Request('https://t.local/v1/counts', { method: 'POST', body: big,
    headers: { 'content-length': '10' } });
  check('oversize with a small content-length → 413',
    (await handleCounts(lying, { DB: mockDB() })).status === 413);
}
{
  check('validateCounts accepts a good row', validateCounts(good(), today) === '');
  check('empty errors and knowledge are fine',
    validateCounts({ ...good(), errors: {}, knowledge: {} }, today) === '');
  check('the date window is 8 days back to 1 day ahead',
    validateCounts({ ...good(), date: dayOffset(today, -8) }, today) === ''
    && validateCounts({ ...good(), date: dayOffset(today, 1) }, today) === ''
    && validateCounts({ ...good(), date: dayOffset(today, -9) }, today) === 'date range');
  check('the tool list has the registered tools plus other', TOOL_NAMES.has('other')
    && TOOL_NAMES.has('get_resume_brief') && TOOL_NAMES.size >= 60);
}
{
  // The switch answers before the body is read.
  const db = mockDB();
  let bodyRead = false;
  const req = { headers: { get() { bodyRead = true; return '10'; } },
    async text() { bodyRead = true; return JSON.stringify(good()); } };
  for (const mode of ['off', 'OFF']) {
    const res = await handleCounts(req, { DB: db, COUNTS_MODE: mode });
    check(`COUNTS_MODE=${mode} → 410`, res.status === 410);
  }
  check('410 reads nothing and stores nothing', bodyRead === false && db.calls.length === 0);
  check('any other COUNTS_MODE keeps accepting',
    (await post(mockDB(), good(), { COUNTS_MODE: 'on' })).status === 204);
}
{
  // The handler never looks at who is asking.
  const db = mockDB();
  const res = await handleCounts(request(good(), { 'cf-connecting-ip': '203.0.113.9',
    'x-forwarded-for': '203.0.113.9', 'user-agent': 'someone' }), { DB: db });
  const stored = JSON.stringify(db.calls);
  check('IP and user agent are never stored', res.status === 204 && !stored.includes('203.0.113.9')
    && !stored.includes('someone'));
}
{
  const db = mockDB();
  const res = await worker.fetch(request(good()), { DB: db });
  check('router: POST /v1/counts is public and stores the row', res.status === 204 && db.calls.length === 1,
    `status=${res.status}`);
  const gone = await worker.fetch(request(good()), { DB: mockDB(), COUNTS_MODE: 'off' });
  check('router: COUNTS_MODE=off → 410', gone.status === 410);
  const ping = await worker.fetch(new Request('https://t.local/v1/ping', { method: 'POST', body: '{}',
    headers: { 'content-length': '2' } }), { DB: mockDB(), COUNTS_MODE: 'off' });
  check('router: the switch leaves /v1/ping alone', ping.status === 422, `status=${ping.status}`);
}
{
  const db = mockDB();
  await purgeOldCounts({ DB: db }, '2026-11-26');
  const cutoff = dayOffset('2026-11-26', -395);
  check('retention is 395 days', RETENTION_DAYS === 395);
  check('retention deletes old counts', db.calls.some((c) => /DELETE FROM counts WHERE day < \?/.test(c.sql)
    && c.vals[0] === cutoff));
  check('retention also clears the two older detail tables',
    db.calls.some((c) => /DELETE FROM events WHERE received < \?/.test(c.sql) && c.vals[0] === cutoff)
    && db.calls.some((c) => /DELETE FROM feedback WHERE received < \?/.test(c.sql) && c.vals[0] === cutoff));
  const waited = [];
  const sched = mockDB();
  await worker.scheduled({}, { DB: sched }, { waitUntil(p) { waited.push(p); } });
  await Promise.all(waited);
  check('the daily cron runs the counts retention', sched.calls.some((c) => /DELETE FROM counts/.test(c.sql))
    && sched.calls.some((c) => /DELETE FROM pings/.test(c.sql)));
}

{
  // No Content-Length: refused before the body is touched.
  let touched = false;
  const noLength = { headers: { get() { return null; } },
    get body() { touched = true; return null; }, async text() { touched = true; return '{}'; } };
  const res = await handleCounts(noLength, { DB: mockDB() });
  check('no content-length → 413 without reading the body', res.status === 413 && touched === false,
    `status=${res.status} touched=${touched}`);
  for (const value of ['abc', '-1', '1e3', '', ' 12']) {
    const odd = { headers: { get() { return value; } }, get body() { touched = true; return null; },
      async text() { touched = true; return '{}'; } };
    const r = await handleCounts(odd, { DB: mockDB() });
    check(`content-length ${JSON.stringify(value)} → 413`, r.status === 413 && touched === false);
  }
  const tooBig = { headers: { get() { return String(MAX_COUNTS_BYTES + 1); } },
    get body() { touched = true; return null; }, async text() { touched = true; return '{}'; } };
  check('content-length over the limit → 413 without reading the body',
    (await handleCounts(tooBig, { DB: mockDB() })).status === 413 && touched === false);
}
{
  // A small Content-Length in front of an endless body: reading stops at the limit.
  let pulls = 0;
  let cancelled = false;
  const chunk = new TextEncoder().encode('x'.repeat(1024));
  const body = new ReadableStream({
    pull(controller) { pulls++; if (pulls > 500) controller.close(); else controller.enqueue(chunk); },
    cancel() { cancelled = true; },
  }, { highWaterMark: 0 });
  const db = mockDB();
  const res = await handleCounts({ headers: { get() { return '10'; } }, body }, { DB: db });
  check('a body longer than announced stops being read at the limit', res.status === 413
    && pulls <= 12 && cancelled && db.calls.length === 0, `status=${res.status} pulls=${pulls} cancelled=${cancelled}`);
}
{
  // A body delivered in several chunks is put together again.
  const text = JSON.stringify(good());
  const bytes = new TextEncoder().encode(text);
  const parts = [bytes.slice(0, 7), bytes.slice(7, 60), bytes.slice(60)];
  const body = new ReadableStream({ pull(c) { const p = parts.shift(); if (p) c.enqueue(p); else c.close(); } });
  const db = mockDB();
  const res = await handleCounts({ headers: { get() { return String(bytes.length); } }, body }, { DB: db });
  check('a chunked body within the limit is accepted', res.status === 204 && db.calls.length === 1,
    `status=${res.status}`);
}

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log('\nall counts checks passed');
