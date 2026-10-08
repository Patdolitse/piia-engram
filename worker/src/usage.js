/**
 * Daily usage ping: POST /v1/ping, the usage page (the dashboard home), retention.
 * Stores install_id, day, version, os, python, client. Reads only the request
 * body and its length; never reads or stores IP addresses or other request headers.
 */

const JSON_HEADERS = { 'Content-Type': 'application/json' };
const MAX_PING_BYTES = 1024;
const FIELDS = ['schema', 'install_id', 'version', 'os', 'python', 'client', 'date'];
const OS_VALUES = new Set(['windows', 'macos', 'linux', 'other']);
const CLIENTS = new Set(['claude_code', 'claude_desktop', 'codex', 'cursor', 'windsurf', 'vscode',
  'cline', 'zed', 'gemini', 'opencode', 'cli', 'other', 'unknown']);
const RETENTION_DAYS = 400;

export function dayOffset(day, delta) {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + delta)).toISOString().slice(0, 10);
}

function utcToday() {
  return new Date().toISOString().slice(0, 10);
}

export function validatePing(data, today = utcToday()) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return 'not an object';
  const keys = Object.keys(data).sort();
  if (keys.join(',') !== [...FIELDS].sort().join(',')) return 'unexpected fields';
  if (!FIELDS.every((k) => typeof data[k] === 'string')) return 'non-string field';
  if (data.schema !== 'ping/1') return 'schema';
  if (!/^[0-9a-f]{32}$/.test(data.install_id)) return 'install_id';
  if (!/^\d+\.\d+\.\d+[0-9A-Za-z.+-]{0,16}$/.test(data.version)) return 'version';
  if (!OS_VALUES.has(data.os)) return 'os';
  if (!/^3\.\d{1,2}$/.test(data.python)) return 'python';
  if (!CLIENTS.has(data.client)) return 'client';
  if (!/^\d{4}-\d{2}-\d{2}$/.test(data.date)) return 'date';
  if (data.date < dayOffset(today, -2) || data.date > dayOffset(today, 2)) return 'date range';
  return '';
}

function reply(status, body) {
  return new Response(body === null ? null : JSON.stringify(body), { status, headers: JSON_HEADERS });
}

export async function handlePing(request, env) {
  const length = parseInt(request.headers.get('content-length') || '0', 10);
  if (length > MAX_PING_BYTES) return reply(413, { error: 'too large' });
  const text = await request.text();
  if (new TextEncoder().encode(text).length > MAX_PING_BYTES) return reply(413, { error: 'too large' });
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return reply(400, { error: 'invalid JSON' });
  }
  const problem = validatePing(data);
  if (problem) return reply(422, { error: problem });
  await env.DB.prepare(
    'INSERT OR IGNORE INTO pings (install_id, day, version, os, python, client) VALUES (?, ?, ?, ?, ?, ?)',
  ).bind(data.install_id, data.date, data.version, data.os, data.python, data.client).run();
  return reply(204, null);
}

function monthStart(day, deltaMonths) {
  const [y, m] = day.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1 + deltaMonths, 1)).toISOString().slice(0, 10);
}

const n = (res) => Number(res?.results?.[0]?.n || 0);
const list = (res) => res?.results || [];

const RANGES = [7, 30, 90];

/** The page's time range: 7, 30 or 90 (a number or a query-string value); anything else is 30. */
export function usageRange(days) {
  return RANGES.find((r) => r === days || String(r) === days) || 30;
}

/**
 * PyPI downloads (without mirrors) over the 30 days before today, from the
 * fetchPypiStats() shape { daily: [{ category, date, downloads }], recent }.
 * null when the numbers could not be fetched.
 */
export function pypiDownloads30(pypi, today = utcToday()) {
  const rows = (pypi?.daily || []).filter((r) => !r.category || r.category === 'without_mirrors');
  if (!rows.length) return null;
  const from = dayOffset(today, -30);
  return rows.filter((r) => r.date >= from && r.date < today)
    .reduce((sum, r) => sum + (Number(r.downloads) || 0), 0);
}

/**
 * Numbers for the usage page. The headline numbers (dau/wau/mau/total/retention)
 * have fixed windows; daily and the three distributions follow `days`.
 * fetchPypi (optional) returns the fetchPypiStats() shape; if it fails only
 * pypi_30d is empty.
 */
export async function getUsageStats(env, today = utcToday(), days = 30, fetchPypi = null) {
  const range = usageRange(days);
  const from = dayOffset(today, -(range - 1));
  const d30 = dayOffset(today, -29);
  const thisMonth = monthStart(today, 0);
  const lastMonth = monthStart(today, -1);
  const lastMonthEnd = dayOffset(thisMonth, -1);
  const q = (sql, ...vals) => env.DB.prepare(sql).bind(...vals);
  const distinctSince = 'SELECT COUNT(DISTINCT install_id) AS n FROM pings WHERE day >= ?';
  const dist = (col) => q(
    `SELECT ${col} AS k, COUNT(DISTINCT install_id) AS n FROM pings WHERE day >= ? AND day <= ? GROUP BY ${col} ORDER BY n DESC LIMIT 20`, from, today);
  const pypi = fetchPypi ? Promise.resolve().then(fetchPypi).catch(() => null) : Promise.resolve(null);
  // One round trip; results come back in statement order.
  const [rows, pypiStats] = await Promise.all([env.DB.batch([
    q('SELECT COUNT(*) AS n FROM pings WHERE day = ?', today),
    q(distinctSince, dayOffset(today, -6)),
    q(distinctSince, d30),
    q(distinctSince, '0000-00-00'),
    q('SELECT COUNT(DISTINCT install_id) AS n FROM pings WHERE day BETWEEN ? AND ?', lastMonth, lastMonthEnd),
    q('SELECT COUNT(*) AS n FROM (SELECT install_id FROM pings WHERE day BETWEEN ? AND ? GROUP BY install_id) p '
      + 'WHERE EXISTS (SELECT 1 FROM pings c WHERE c.install_id = p.install_id AND c.day >= ?)',
    lastMonth, lastMonthEnd, thisMonth),
    q('SELECT day, COUNT(*) AS n FROM pings WHERE day >= ? AND day <= ? GROUP BY day ORDER BY day', from, today),
    // New installs: scan the window only; an earlier row for the same install is a primary-key probe.
    q('SELECT p.day AS day, COUNT(*) AS n FROM pings p WHERE p.day >= ? '
      + 'AND NOT EXISTS (SELECT 1 FROM pings e WHERE e.install_id = p.install_id AND e.day < p.day) '
      + 'GROUP BY p.day ORDER BY p.day', from),
    dist('version'),
    dist('os'),
    dist('client'),
  ]), pypi]);
  const [dau, wau, mau, total, prevMonth, kept, active, fresh, versions, os, clients] = rows;
  const byDay = (res) => new Map(list(res).map((r) => [r.day, Number(r.n || 0)]));
  const activeByDay = byDay(active);
  const freshByDay = byDay(fresh);
  // Every day of the range, oldest first; days without pings are zero.
  const daily = Array.from({ length: range }, (_, i) => {
    const day = dayOffset(from, i);
    const a = activeByDay.get(day) || 0;
    return { day, active: a, fresh: Math.min(freshByDay.get(day) || 0, a) };
  });
  return {
    today,
    days: range,
    dau: n(dau),
    wau: n(wau),
    mau: n(mau),
    total: n(total),
    retention: { last_month: n(prevMonth), kept: n(kept) },
    daily,
    versions: list(versions),
    os: list(os),
    clients: list(clients),
    pypi_30d: pypiDownloads30(pypiStats, today),
  };
}

function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function fmt(n) {
  return Number(n || 0).toLocaleString('en-US');
}

function niceScale(v) {
  // round tick step (1, 2, 2.5, 5 x 10^k) with about 5 intervals; max is a whole number of steps
  const raw = Math.max(1, v) / 5;
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw && Number.isInteger(s)) || Math.ceil(raw);
  const max = Math.max(step, Math.ceil(Math.max(1, v) / step) * step);
  return { max, step };
}

const LABELS = {
  clients: { claude_code: 'Claude Code', claude_desktop: 'Claude Desktop', codex: 'Codex', cursor: 'Cursor',
    windsurf: 'Windsurf', vscode: 'VS Code', cline: 'Cline', zed: 'Zed', gemini: 'Gemini CLI', opencode: 'OpenCode',
    cli: '命令行', other: '其他', unknown: '未知' },
  os: { windows: 'Windows', macos: 'macOS', linux: 'Linux', other: '其他' },
};

function foldTop(list, max = 8) {
  const sorted = [...list].sort((a, b) => b.n - a.n);
  if (sorted.length <= max) return sorted;
  const head = sorted.slice(0, max - 1);
  const rest = sorted.slice(max - 1).reduce((s, r) => s + r.n, 0);
  return [...head, { k: '__other__', n: rest }];
}

function barList(title, list, labels = {}) {
  const rows = foldTop(list);
  const max = Math.max(1, ...rows.map((r) => r.n));
  const total = rows.reduce((s, r) => s + r.n, 0) || 1;
  const body = rows.length ? rows.map((r) => {
    const name = r.k === '__other__' ? '其余' : (Object.hasOwn(labels, r.k) ? labels[r.k] : r.k);
    const pct = Math.round((r.n / total) * 100);
    return `<li class="bl-row" data-tip="${esc(name)}：${fmt(r.n)} 个安装（${pct}%）">
      <span class="bl-name">${esc(name)}</span>
      <span class="bl-track"><span class="bl-bar" style="width:${((r.n / max) * 100).toFixed(1)}%"></span></span>
      <span class="bl-val">${fmt(r.n)}<span class="bl-pct">${pct}%</span></span></li>`;
  }).join('') : '<li class="empty">暂无数据</li>';
  return `<section class="card"><h2>${esc(title)}</h2><ul class="barlist">${body}</ul></section>`;
}

function dailyChart(daily) {
  const W = 960, H = 260, L = 44, R = 12, T = 16, B = 28;
  const n = Math.max(1, daily.length);
  const { max, step: tickStep } = niceScale(Math.max(...daily.map((d) => d.active), 0));
  const band = (W - L - R) / n;
  const bw = Math.min(24, Math.max(3, band * 0.68));
  const y = (v) => T + (H - T - B) * (1 - v / max);
  const ticks = Array.from({ length: Math.round(max / tickStep) + 1 }, (_, k) => k * tickStep);
  const grid = ticks.map((t) => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/>`
    + `<text class="tick" x="${L - 8}" y="${y(t) + 4}" text-anchor="end">${fmt(t)}</text>`).join('');
  const step = n > 45 ? 14 : n > 20 ? 7 : 1;
  let cols = '', xlab = '', hits = '';
  daily.forEach((d, i) => {
    const cx = L + band * i + band / 2;
    const x0 = cx - bw / 2;
    const returning = Math.max(0, d.active - d.fresh);
    const yBase = y(0), yRet = y(returning), yTop = y(d.active);
    const r = Math.min(4, bw / 2);
    if (returning > 0) {
      // bottom segment: square at baseline; rounded only if it is the top of the column
      const top = d.fresh > 0 ? yRet + 1 : yRet;
      cols += d.fresh > 0
        ? `<rect class="s-ret" x="${x0}" y="${top}" width="${bw}" height="${Math.max(0, yBase - top)}"/>`
        : roundTop('s-ret', x0, top, bw, yBase - top, r);
    }
    if (d.fresh > 0) {
      const bottom = returning > 0 ? yRet - 1 : yBase;
      cols += roundTop('s-new', x0, yTop, bw, Math.max(0, bottom - yTop), r);
    }
    const showTick = step === 1 || i === n - 1 || (i % step === 0 && n - 1 - i >= Math.max(2, step / 2));
    if (showTick) xlab += `<text class="tick" x="${cx}" y="${H - 8}" text-anchor="middle">${esc(d.day.slice(5))}</text>`;
    hits += `<rect class="hit" x="${L + band * i}" y="${T}" width="${band}" height="${H - T - B}" `
      + `data-tip="${esc(d.day)}　活跃 ${fmt(d.active)}　（老安装 ${fmt(returning)} · 新安装 ${fmt(d.fresh)}）"/>`;
  });
  const empty = daily.every((d) => !d.active)
    ? `<text class="empty-note" x="${(L + W - R) / 2}" y="${(T + H - B) / 2}" text-anchor="middle">这段时间还没有收到使用信号</text>` : '';
  return `<div class="chart-wrap"><svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="每日活跃安装">${grid}${cols}${empty}${xlab}${hits}</svg></div>`;
}

function roundTop(cls, x, y, w, h, r) {
  if (h <= 0) return '';
  const rr = Math.min(r, h);
  return `<path class="${cls}" d="M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z"/>`;
}

function tile(label, value, sub = '', cls = '', extraHtml = '') {
  return `<div class="tile${cls ? ` ${cls}` : ''}"><div class="t-label">${esc(label)}</div>`
    + `<div class="t-value">${esc(value)}</div>${sub ? `<div class="t-sub">${esc(sub)}${extraHtml}</div>` : extraHtml}</div>`;
}

export function renderUsage(stats) {
  const { retention } = stats;
  const rate = retention.last_month ? Math.round((retention.kept / retention.last_month) * 100) : null;
  const meter = rate === null ? '' : `<div class="meter" aria-hidden="true"><span style="width:${rate}%"></span></div>`;
  const totalNew = stats.daily.reduce((s, d) => s + d.fresh, 0);
  const ranges = [7, 30, 90].map((d) => d === stats.days
    ? `<span class="seg on" aria-current="true">${d} 天</span>`
    : `<a class="seg" href="?days=${d}">${d} 天</a>`).join('');
  const rows = stats.daily.slice().reverse().map((d) =>
    `<tr><td>${esc(d.day)}</td><td>${fmt(d.active)}</td><td>${fmt(d.active - d.fresh)}</td><td>${fmt(d.fresh)}</td></tr>`).join('');

  return `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Engram 使用统计</title>
<style>
:root{color-scheme:light;--bg:#f5f5f3;--surface:#fcfcfb;--border:#e6e5e1;--grid:#ecebe7;
--text:#0b0b0b;--text2:#52514e;--muted:#8a8984;--s1:#2a78d6;--s2:#eb6834;--track:#e3eefb}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#111110;--surface:#1a1a19;--border:#2c2c2a;
--grid:#2a2a28;--text:#fff;--text2:#c3c2b7;--muted:#8d8c84;--s1:#3987e5;--s2:#d95926;--track:#1f3550}}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
background:var(--bg);color:var(--text);line-height:1.5}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 48px}
header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:20px}
h1{font-size:22px;font-weight:650;letter-spacing:-.01em}
.sub{color:var(--text2);font-size:13px;margin-top:2px}
nav a{color:var(--text2);font-size:13px;text-decoration:none;margin-left:14px}
nav a:hover{color:var(--text)}
.filters{display:flex;align-items:center;gap:10px;margin-bottom:16px;font-size:13px;color:var(--text2)}
.segs{display:inline-flex;border:1px solid var(--border);border-radius:8px;overflow:hidden;background:var(--surface)}
.seg{padding:5px 12px;color:var(--text2);text-decoration:none}
.seg+.seg{border-left:1px solid var(--border)}
.seg.on{background:var(--text);color:var(--surface);font-weight:600}
.kpis{display:grid;grid-template-columns:1.4fr repeat(5,1fr);gap:12px;margin-bottom:16px}
.tile,.card{background:var(--surface);border:1px solid var(--border);border-radius:12px}
.tile{padding:16px 18px}
.t-label{font-size:12.5px;color:var(--text2)}
.t-value{font-size:28px;font-weight:650;margin-top:6px;font-variant-numeric:tabular-nums}
.tile.hero .t-value{font-size:48px;line-height:1.05}
.t-sub{font-size:12px;color:var(--muted);margin-top:6px}
.meter{height:6px;border-radius:3px;background:var(--track);margin-top:10px;overflow:hidden}
.meter span{display:block;height:100%;background:var(--s1);border-radius:3px}
.card{padding:16px 18px;margin-bottom:16px}
.card h2{font-size:14px;font-weight:600;margin-bottom:2px}
.card .note{font-size:12px;color:var(--muted);margin-bottom:10px}
.legend{display:flex;gap:16px;font-size:12.5px;color:var(--text2);margin:6px 0 4px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.chart-wrap{overflow-x:auto}.chart{width:100%;min-width:640px;height:auto;display:block}.empty-note{fill:var(--muted);font-size:14px}
.grid{stroke:var(--grid);stroke-width:1}
.tick{fill:var(--muted);font-size:11px}
.s-ret{fill:var(--s1)}.s-new{fill:var(--s2)}
.hit{fill:transparent;cursor:default}.hit:hover{fill:var(--text);fill-opacity:.05}
.cols3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.cols3 .card{margin-bottom:0}
.barlist{list-style:none}
.bl-row{display:grid;grid-template-columns:96px 1fr 72px;align-items:center;gap:10px;padding:5px 0;font-size:13px}
.bl-name{color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bl-track{height:8px;border-radius:4px;background:transparent}
.bl-bar{display:block;height:8px;border-radius:0 4px 4px 0;background:var(--s1);min-width:2px}
.bl-val{text-align:right;font-variant-numeric:tabular-nums;color:var(--text)}
.bl-pct{color:var(--muted);font-size:11.5px;margin-left:6px}
.empty{color:var(--muted);font-size:13px;padding:8px 0}
details{margin-top:16px}summary{cursor:pointer;color:var(--text2);font-size:13px}
table{border-collapse:collapse;width:100%;margin-top:10px;font-size:13px;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:6px 10px;border-bottom:1px solid var(--border)}th:first-child,td:first-child{text-align:left}
th{color:var(--text2);font-weight:500}
footer{color:var(--muted);font-size:12px;margin-top:20px}
#tip{position:fixed;pointer-events:none;background:var(--text);color:var(--surface);font-size:12px;padding:6px 9px;
border-radius:6px;white-space:nowrap;opacity:0;transition:opacity .08s;z-index:10}
@media (max-width:1000px){.kpis{grid-template-columns:repeat(3,1fr)}}
@media (max-width:820px){.kpis{grid-template-columns:repeat(2,1fr)}.tile.hero,.tile.wide{grid-column:span 2}.cols3{grid-template-columns:1fr}}
</style></head><body><div class="wrap">
<header><div><h1>Engram 使用统计</h1><div class="sub">按匿名安装计数 · 数据截至 ${esc(stats.today)}（UTC）</div></div>
<nav><a href="/details">详细统计</a><a href="/v1/usage?days=${stats.days}">JSON</a><a href="/logout">退出</a></nav></header>
<div class="filters">时间范围 <span class="segs">${ranges}</span></div>
<div class="kpis">
${tile('今日活跃安装', fmt(stats.dau), '今天（UTC）发过信号的安装', 'hero')}
${tile('近 7 天活跃', fmt(stats.wau))}
${tile('近 30 天活跃', fmt(stats.mau))}
${tile('近 400 天安装', fmt(stats.total), '数据只保留 400 天')}
${tile('PyPI 近 30 天下载', stats.pypi_30d == null ? '—' : fmt(stats.pypi_30d), '不等于安装数')}
${tile('月留存', rate === null ? '—' : `${rate}%`, `上月 ${fmt(retention.last_month)} 个，本月仍在 ${fmt(retention.kept)} 个`, 'wide', meter)}
</div>
<section class="card"><h2>每日活跃安装</h2>
<div class="note">近 ${stats.days} 天，柱高是当天活跃安装数；其中新安装 ${fmt(totalNew)} 个。</div>
<div class="legend"><span><i style="background:var(--s1)"></i>老安装</span><span><i style="background:var(--s2)"></i>新安装（当天首次出现）</span></div>
${dailyChart(stats.daily)}
<details><summary>查看数据表</summary><table><tr><th>日期</th><th>活跃</th><th>老安装</th><th>新安装</th></tr>${rows}</table></details>
</section>
<div class="cols3">
${barList(`客户端（近 ${stats.days} 天）`, stats.clients, LABELS.clients)}
${barList(`版本（近 ${stats.days} 天）`, stats.versions)}
${barList(`系统（近 ${stats.days} 天）`, stats.os, LABELS.os)}
</div>
<footer>一个人在多台电脑上使用会计为多个安装，重装会计为新安装；月初时本月留存只是部分数据。不记录 IP 地址。</footer>
</div><div id="tip" role="tooltip"></div>
<script>
(function(){var t=document.getElementById('tip');
document.addEventListener('mousemove',function(e){var el=e.target.closest('[data-tip]');
if(!el){t.style.opacity=0;return;}t.textContent=el.getAttribute('data-tip');
var x=e.clientX+14,y=e.clientY+14,w=t.offsetWidth;if(x+w>innerWidth-8)x=e.clientX-w-14;
t.style.left=x+'px';t.style.top=y+'px';t.style.opacity=1;});})();
</script></body></html>`;
}

export async function purgeOldPings(env, today = utcToday()) {
  await env.DB.prepare('DELETE FROM pings WHERE day < ?').bind(dayOffset(today, -RETENTION_DAYS)).run();
}
