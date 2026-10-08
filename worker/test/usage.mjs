/**
 * Node harness for worker/src/usage.js — no Cloudflare, no network, mock D1.
 * Run: node worker/test/usage.mjs   (exit code 1 on any failure)
 */
import { handlePing, validatePing, getUsageStats, renderUsage, purgeOldPings, dayOffset, pypiDownloads30 }
  from '../src/usage.js';
import worker, { fetchPypiStats, PYPI_TIMEOUT_MS } from '../src/index.js';

// No network in this harness: any fetch (the PyPI numbers) fails like an outage would.
let fetchCalls = 0;
globalThis.fetch = async () => { fetchCalls++; throw new Error('offline'); };

let failures = 0;
function check(name, cond, extra = '') {
  if (!cond) failures++;
  console.log(`${cond ? 'ok  ' : 'FAIL'}  ${name}${extra ? '  — ' + extra : ''}`);
}

function mockDB(firstRows = {}, allRows = {}) {
  const calls = [];
  const batches = [];
  const direct = [];
  const firstOf = (sql) => { for (const k of Object.keys(firstRows)) if (sql.includes(k)) return firstRows[k]; return null; };
  const allOf = (sql) => { for (const k of Object.keys(allRows)) if (sql.includes(k)) return allRows[k]; return null; };
  return {
    calls, batches, direct,
    prepare(sql) {
      return {
        bind(...vals) {
          calls.push({ sql, vals });
          return {
            sql, vals,
            async run() { direct.push(sql); return { success: true }; },
            async first() { direct.push(sql); return firstOf(sql); },
            async all() { direct.push(sql); return { results: allOf(sql) || [] }; },
          };
        },
      };
    },
    // D1 batch: one round trip, results in statement order.
    async batch(statements) {
      batches.push(statements.map((st) => ({ sql: st.sql, vals: st.vals })));
      return statements.map((st) => {
        const many = allOf(st.sql);
        if (many) return { results: many };
        const row = firstOf(st.sql);
        return { results: row ? [row] : [] };
      });
    },
  };
}

const today = new Date().toISOString().slice(0, 10);
const good = () => ({ schema: 'ping/1', install_id: 'a'.repeat(32), version: '4.22.0', os: 'windows',
  python: '3.12', client: 'claude_code', date: today });

async function post(db, body) {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  const req = new Request('https://t.local/v1/ping', { method: 'POST', body: text,
    headers: { 'content-type': 'application/json', 'content-length': String(text.length) } });
  return handlePing(req, { DB: db });
}

{
  const db = mockDB();
  const res = await post(db, good());
  check('valid ping → 204', res.status === 204, `status=${res.status}`);
  const ins = db.calls.find((c) => /INSERT OR IGNORE INTO pings/.test(c.sql));
  check('valid ping inserted once with the six columns', !!ins && ins.vals.length === 6
    && ins.vals[0] === 'a'.repeat(32) && ins.vals[1] === today && ins.vals[5] === 'claude_code');
}
{
  const db = mockDB();
  const res = await post(db, { ...good(), notes: 'free text' });
  check('extra field → 422', res.status === 422);
  check('extra field never stored', db.calls.length === 0);
}
for (const [field, value] of [['install_id', 'XYZ'], ['os', 'plan9'], ['client', 'codex -> claude'],
  ['python', '2.7'], ['version', '4.2 /x'], ['schema', 'ping/2'], ['date', '2020-01-01']]) {
  const db = mockDB();
  const res = await post(db, { ...good(), [field]: value });
  check(`bad ${field} → 422`, res.status === 422 && db.calls.length === 0);
}
{
  const res = await post(mockDB(), 'not json');
  check('invalid JSON → 400', res.status === 400);
  const big = JSON.stringify({ ...good(), pad: 'x'.repeat(2000) });
  const res2 = await post(mockDB(), big);
  check('oversize → 413', res2.status === 413);
}
check('validatePing accepts a good body', validatePing(good(), today) === '');
check('dayOffset', dayOffset('2026-03-01', -1) === '2026-02-28');

const FIRST = { 'COUNT(*) AS n FROM pings WHERE day = ?': { n: 3 }, 'day >= ?': { n: 7 },
  'COUNT(DISTINCT install_id) AS n FROM pings': { n: 9 } };
const ALL = () => ({
  'GROUP BY day': [{ day: dayOffset(today, -2), n: 5 }, { day: today, n: 3 }],
  'GROUP BY p.day': [{ day: today, n: 1 }],
  'GROUP BY version': [{ k: '4.22.0<b>', n: 3 }],
  'GROUP BY os': [{ k: 'windows', n: 3 }],
  'GROUP BY client': [{ k: 'claude_code', n: 3 }],
});
const statsFor = async (days, fetchPypi) => {
  const db = mockDB(FIRST, ALL());
  return { db, stats: await getUsageStats({ DB: db }, today, days, fetchPypi) };
};
{
  const { db, stats } = await statsFor(7);
  check('stats has the headline numbers', stats.dau === 3 && typeof stats.wau === 'number'
    && typeof stats.mau === 'number' && typeof stats.retention.kept === 'number');
  check('stats have the page shape', Object.keys(stats).join(',')
    === 'today,days,dau,wau,mau,total,retention,daily,versions,os,clients,pypi_30d'
    && Object.keys(stats.retention).join(',') === 'last_month,kept', Object.keys(stats).join(','));
  check('new installs live in daily.fresh only', !('new_installs' in stats));
  check('stats sent in one batch round trip', db.batches.length === 1 && db.batches[0].length === 11
    && db.direct.length === 0, `batches=${db.batches.length} direct=${db.direct.length}`);
  const newSql = db.calls.map((c) => c.sql).find((q) => /GROUP BY p\.day/.test(q)) || '';
  check('new installs scan only the window', /day >= \?/.test(newSql) && /NOT EXISTS/.test(newSql)
    && !/GROUP BY install_id/.test(newSql));
  const days = stats.daily.map((d) => d.day);
  check('daily covers every day of the range, oldest first', stats.days === 7 && days.length === 7
    && days[0] === dayOffset(today, -6) && days[6] === today
    && days.every((d, i) => i === 0 || d > days[i - 1]), days.join(','));
  check('daily fills missing days with zeros', stats.daily[1].active === 0 && stats.daily[1].fresh === 0
    && stats.daily[4].active === 5 && stats.daily[4].fresh === 0);
  check('daily carries active and fresh', stats.daily[6].active === 3 && stats.daily[6].fresh === 1
    && Object.keys(stats.daily[6]).join(',') === 'day,active,fresh');
  check('without a PyPI source the download number is empty', stats.pypi_30d === null);
  const bounded = (re) => db.batches[0].filter((st) => re.test(st.sql));
  const windowed = [...bounded(/GROUP BY day/), ...bounded(/GROUP BY version/), ...bounded(/GROUP BY os/), ...bounded(/GROUP BY client/)];
  check('daily and the three distributions stop at today', windowed.length === 4
    && windowed.every((st) => /day >= \? AND day <= \?/.test(st.sql) && st.vals[1] === today && st.vals.length === 2),
  JSON.stringify(windowed.map((st) => st.vals)));
}
{
  const db = mockDB(FIRST, ALL());
  const stats = await getUsageStats({ DB: db }, '2026-03-02', 7);
  const days = stats.daily.map((d) => d.day);
  check('a range that crosses a month starts in the previous month', days.length === 7
    && days[0] === '2026-02-24' && days[6] === '2026-03-02', days.join(','));
}
{
  const rangeStart = (db, re) => db.batches[0].find((st) => re.test(st.sql)).vals[0];
  const kpiVals = (db) => JSON.stringify(db.batches[0].slice(0, 6).map((st) => st.vals));
  const cases = [[7, 7], [30, 30], [90, 90], ['7', 7], ['90', 90], [undefined, 30], [null, 30], ['', 30],
    [14, 30], ['abc', 30], [0, 30], [-7, 30], ['7.5', 30], [365, 30]];
  let kpiRef = null;
  for (const [given, want] of cases) {
    const { db, stats } = await statsFor(given);
    kpiRef = kpiRef || kpiVals(db);
    const start = dayOffset(today, -(want - 1));
    check(`days=${JSON.stringify(given)} → ${want}`, stats.days === want && stats.daily.length === want
      && rangeStart(db, /GROUP BY day/) === start && rangeStart(db, /GROUP BY p\.day/) === start
      && rangeStart(db, /GROUP BY version/) === start && rangeStart(db, /GROUP BY client/) === start,
    `days=${stats.days} len=${stats.daily.length}`);
    check(`days=${JSON.stringify(given)} leaves the headline numbers alone`, kpiVals(db) === kpiRef);
  }
}
{
  const pypi = { daily: [
    { category: 'without_mirrors', date: dayOffset(today, -31), downloads: 1000 },
    { category: 'without_mirrors', date: dayOffset(today, -30), downloads: 10 },
    { category: 'without_mirrors', date: dayOffset(today, -1), downloads: 20 },
    { category: 'with_mirrors', date: dayOffset(today, -1), downloads: 500 },
    { date: dayOffset(today, -2), downloads: '5' },
  ], recent: { last_week: 25 } };
  check('PyPI 30-day sum covers the 30 days before today', pypiDownloads30(pypi, today) === 35,
    String(pypiDownloads30(pypi, today)));
  check('PyPI outage → no number', pypiDownloads30({ daily: [], recent: {} }, today) === null
    && pypiDownloads30(null, today) === null);
  const { stats } = await statsFor(30, async () => pypi);
  check('stats carry the PyPI 30-day downloads', stats.pypi_30d === 35);
  const broken = await statsFor(30, async () => { throw new Error('down'); });
  check('a failing PyPI source does not fail the stats', broken.stats.pypi_30d === null && broken.stats.dau === 3);
}
{
  const { stats } = await statsFor(30, async () => ({ daily: [{ date: dayOffset(today, -1), downloads: 9000 }], recent: {} }));
  const html = renderUsage(stats);
  check('page says installs seen in the last 400 days', html.includes('近 400 天安装') && !html.includes('累计'));
  check('page notes the partial current month', html.includes('月初'));
  check('page says active installs', html.includes('活跃安装'));
  check('page escapes values', html.includes('4.22.0&lt;b&gt;') && !html.includes('4.22.0<b>'));
  check('page makes no unique-people claim', !html.includes('用户数') && !html.includes('独立用户'));
  check('page shows PyPI downloads formatted', html.includes('PyPI 近 30 天下载') && html.includes('9,000'));
  check('page links to details, JSON for the range and logout', html.includes('href="/details"')
    && html.includes('href="/v1/usage?days=30"') && html.includes('href="/logout"') && !html.includes('原看板'));
  check('page offers 7/30/90 day ranges', html.includes('href="?days=7"') && html.includes('href="?days=90"')
    && /<span class="seg on" aria-current="true">30 天<\/span>/.test(html) && !html.includes('href="?days=30"'));
  check('page keeps the checked colours for light and dark',
    ['#2a78d6', '#eb6834', '#3987e5', '#d95926'].every((c) => html.includes(c)));
  check('page has the stacked daily chart', html.includes('class="s-ret"') && html.includes('class="s-new"')
    && html.includes('老安装') && html.includes('新安装'));
  const empty = renderUsage({ ...stats, dau: 0, wau: 0, mau: 0, total: 0, retention: { last_month: 0, kept: 0 },
    daily: stats.daily.map((d) => ({ ...d, active: 0, fresh: 0 })), versions: [], os: [], clients: [], pypi_30d: null });
  check('retention meter renders as markup, not as escaped text', html.includes('<div class="meter" aria-hidden="true">')
    && !html.includes('&lt;div') && /月留存<\/div><div class="t-value">\d+%</.test(html));
  check('empty page renders with notes and dashes', empty.includes('这段时间还没有收到使用信号')
    && empty.includes('暂无数据') && empty.includes('—'));
}
{
  // Through the router: "/" is the usage page, "/details" the detailed dashboard, all behind login.
  const env = { DB: mockDB(FIRST, ALL()) };
  const get = (path, e = env) => worker.fetch(new Request(`https://t.local${path}`), e);
  const home = await get('/');
  const homeHtml = await home.text();
  check('router: / is the usage page', home.status === 200
    && /text\/html/.test(home.headers.get('content-type') || '') && homeHtml.includes('Engram 使用统计')
    && homeHtml.includes('活跃安装'));
  check('router: a PyPI outage still renders the page with a dash', fetchCalls > 0
    && /PyPI 近 30 天下载<\/div><div class="t-value">—</.test(homeHtml));
  const usage = await get('/usage?days=7');
  const usageHtml = await usage.text();
  check('router: /usage renders the same page and reads ?days', usage.status === 200
    && /<span class="seg on" aria-current="true">7 天<\/span>/.test(usageHtml));
  check('router: / reads ?days=90',
    /<span class="seg on" aria-current="true">90 天<\/span>/.test(await (await get('/?days=90')).text()));
  const json = await (await get('/v1/usage?days=90')).json();
  check('router: /v1/usage returns the stats as JSON', json.days === 90 && json.daily.length === 90
    && json.pypi_30d === null && !('new_installs' in json));
  check('router: /v1/usage falls back to 30 days', (await (await get('/v1/usage?days=5')).json()).days === 30);
  const locked = { DB: mockDB(), DASH_PASSWORD: 'secret' };
  for (const path of ['/', '/usage', '/details', '/v1/usage', '/v1/stats']) {
    const res = await get(path, locked);
    check(`router: ${path} needs a login`, res.status === 302
      && (res.headers.get('location') || '').endsWith('/login'));
  }
  check('router: /v1/health stays public', (await get('/v1/health', locked)).status === 200);
  const ping = await worker.fetch(new Request('https://t.local/v1/ping', { method: 'POST',
    body: JSON.stringify(good()), headers: { 'content-type': 'application/json' } }), locked);
  check('router: /v1/ping stays public', ping.status === 204);
}
{
  // PyPI lookups: bounded by a timeout, cached at the edge, and the home page asks for the overall series only.
  const realFetch = globalThis.fetch;
  const realTimeout = AbortSignal.timeout;
  const seen = [];
  const hang = (url, init) => {
    seen.push({ url: String(url), init });
    return new Promise((_, reject) => {
      // Never answers; only the abort signal ends it.
      init.signal.addEventListener('abort', () => reject(init.signal.reason));
    });
  };
  // Node unrefs AbortSignal.timeout timers; keep the loop alive while the hung lookups wait for them.
  const keepAlive = setInterval(() => {}, 20);
  try {
    globalThis.fetch = hang;
    const t0 = Date.now();
    const slow = await fetchPypiStats({ recent: false, timeoutMs: 50 });
    check('a hung PyPI lookup gives up after the timeout', Date.now() - t0 < 2000
      && Array.isArray(slow.daily) && slow.daily.length === 0, `${Date.now() - t0} ms`);
    check('the default timeout is short', PYPI_TIMEOUT_MS > 0 && PYPI_TIMEOUT_MS <= 5000, String(PYPI_TIMEOUT_MS));

    // Through the router, with the timeout shortened so the harness stays fast.
    AbortSignal.timeout = (ms) => realTimeout.call(AbortSignal, Math.min(ms, 50));
    seen.length = 0;
    const env = { DB: mockDB(FIRST, ALL()) };
    const get = (path) => worker.fetch(new Request(`https://t.local${path}`), env);
    const home = await get('/');
    const html = await home.text();
    check('router: a hung PyPI lookup still renders the home page with a dash', home.status === 200
      && /PyPI 近 30 天下载<\/div><div class="t-value">—</.test(html));
    check('router: the home page sends one PyPI request (overall only)', seen.length === 1
      && /\/overall\?/.test(seen[0].url) && !/\/recent/.test(seen[0].url), seen.map((s) => s.url).join(' '));
    check('PyPI requests carry an abort signal and an edge cache TTL', seen.every((s) => s.init.signal instanceof AbortSignal
      && s.init.cf && s.init.cf.cacheTtl > 0 && s.init.cf.cacheEverything === true));

    seen.length = 0;
    // The original dashboard queries without bind(); an empty store is enough here.
    const emptyStore = { prepare() {
      const q = { async all() { return { results: [] }; }, async first() { return {}; }, bind() { return q; } };
      return q;
    } };
    const details = await worker.fetch(new Request('https://t.local/details'), { DB: emptyStore });
    const detailsHtml = await details.text();
    check('router: /details still asks for both PyPI series', seen.length === 2
      && seen.some((s) => /\/overall\?/.test(s.url)) && seen.some((s) => /\/recent\?/.test(s.url)),
    seen.map((s) => s.url).join(' '));
    check('router: /details renders the original dashboard without a password', details.status === 200
      && detailsHtml.includes('Engram 遥测仪表盘') && !detailsHtml.includes('Engram 使用统计'),
    `status=${details.status}`);
  } finally {
    clearInterval(keepAlive);
    globalThis.fetch = realFetch;
    AbortSignal.timeout = realTimeout;
  }
}
{
  const db = mockDB();
  await purgeOldPings({ DB: db }, '2026-10-06');
  const del = db.calls.find((c) => /DELETE FROM pings WHERE day < \?/.test(c.sql));
  check('retention deletes pings older than 400 days', !!del && del.vals[0] === dayOffset('2026-10-06', -400));
}

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log('\nall usage checks passed');
