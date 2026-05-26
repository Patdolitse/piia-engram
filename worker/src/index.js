/**
 * Engram 匿名遥测 Worker
 *
 * POST /v1/events  — 接收匿名使用数据（公开，无需认证）
 * GET  /           — 可视化仪表盘（密码保护）
 * GET  /v1/stats   — JSON API（浏览器需登录）
 * GET  /v1/health  — 健康检查（公开）
 */

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

const COOKIE_NAME = 'engram_session';
const SESSION_MAX_AGE = 86400 * 7; // 7 天

// --- 认证 ---

async function hashPassword(password, salt) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', enc.encode(password), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(salt));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, '0')).join('');
}

function getSessionFromCookie(request) {
  const cookie = request.headers.get('cookie') || '';
  const match = cookie.match(new RegExp(`${COOKIE_NAME}=([^;]+)`));
  return match ? match[1] : null;
}

async function isAuthenticated(request, env) {
  if (!env.DASH_PASSWORD) return true;
  const session = getSessionFromCookie(request);
  if (!session) return false;
  const expected = await hashPassword(env.DASH_PASSWORD, 'engram-session');
  return session === expected;
}

function renderLogin(error = '') {
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Engram 遥测 - 登录</title>
<style>
  :root { --bg: #0f1117; --card: #1a1d27; --border: #2a2d3a; --text: #e4e4e7; --muted: #71717a; --accent: #6366f1; --accent2: #8b5cf6; --red: #ef4444; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; display: flex; align-items: center; justify-content: center; }
  .login-card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 2.5rem; width: 100%; max-width: 380px; }
  .login-card h1 { font-size: 1.5rem; font-weight: 700; background: linear-gradient(135deg, var(--accent), var(--accent2)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; text-align: center; margin-bottom: 0.5rem; }
  .login-card p { color: var(--muted); font-size: 0.85rem; text-align: center; margin-bottom: 1.5rem; }
  .field { margin-bottom: 1.25rem; }
  .field label { display: block; font-size: 0.8rem; color: var(--muted); margin-bottom: 0.4rem; letter-spacing: 0.05em; }
  .field input { width: 100%; padding: 0.7rem 1rem; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 0.95rem; outline: none; transition: border-color 0.2s; }
  .field input:focus { border-color: var(--accent); }
  .btn { width: 100%; padding: 0.75rem; background: linear-gradient(135deg, var(--accent), var(--accent2)); border: none; border-radius: 8px; color: #fff; font-size: 0.95rem; font-weight: 600; cursor: pointer; transition: opacity 0.2s; }
  .btn:hover { opacity: 0.9; }
  .error { color: var(--red); font-size: 0.85rem; text-align: center; margin-bottom: 1rem; }
</style>
</head>
<body>
  <div class="login-card">
    <h1>Engram 遥测系统</h1>
    <p>请输入密码查看仪表盘</p>
    ${error ? `<div class="error">${error}</div>` : ''}
    <form method="POST" action="/login">
      <div class="field">
        <label>密码</label>
        <input type="password" name="password" placeholder="请输入访问密码" autofocus required>
      </div>
      <button type="submit" class="btn">登 录</button>
    </form>
  </div>
</body>
</html>`;
}

// --- 校验 ---

const MAX_PAYLOAD_SIZE = 8192;
const ALLOWED_FIELDS = new Set([
  'schema', 'daily_id', 'engram_version', 'timestamp',
  'tool_calls', 'knowledge_counts', 'os_platform', 'python_version', 'tools_tier',
]);

function validatePayload(data) {
  if (!data || typeof data !== 'object') return 'invalid JSON';
  if (!data.daily_id || typeof data.daily_id !== 'string') return 'missing daily_id';
  if (data.daily_id.length > 64) return 'daily_id too long';
  if (data.engram_version && data.engram_version.length > 20) return 'version too long';
  for (const key of Object.keys(data)) {
    if (!ALLOWED_FIELDS.has(key)) return `unexpected field: ${key}`;
  }
  if (data.tool_calls) {
    if (typeof data.tool_calls !== 'object') return 'tool_calls must be object';
    for (const [name, counts] of Object.entries(data.tool_calls)) {
      if (name.length > 80) return 'tool name too long';
      if (typeof counts !== 'object') return 'tool counts must be object';
    }
  }
  if (data.knowledge_counts) {
    if (typeof data.knowledge_counts !== 'object') return 'knowledge_counts must be object';
    for (const [key, val] of Object.entries(data.knowledge_counts)) {
      if (typeof val !== 'number') return `knowledge_counts.${key} must be number`;
    }
  }
  return null;
}

// --- Feedback 校验 ---

const MAX_FEEDBACK_SIZE = 16384;
const FEEDBACK_ALLOWED_FIELDS = new Set([
  'report_type', 'report_version', 'generated_at', 'daily_id',
  'engram_version', 'os', 'python',
  'knowledge', 'top_domains', 'source_tools',
  'first_knowledge_date', 'days_with_knowledge', 'avg_staging_age_days',
  'session_count', 'top_mcp_tools', 'configured_tools', 'beta_events',
]);

function validateFeedback(data) {
  if (!data || typeof data !== 'object') return 'invalid JSON';
  if (!data.daily_id || typeof data.daily_id !== 'string') return 'missing daily_id';
  if (data.daily_id.length > 64) return 'daily_id too long';
  // Relaxed field check — allow unknown fields but store them in raw_json
  return null;
}

// --- Feedback 接收 ---

async function handleFeedback(request, env) {
  const contentLength = parseInt(request.headers.get('content-length') || '0');
  if (contentLength > MAX_FEEDBACK_SIZE) {
    return new Response(JSON.stringify({ error: 'payload too large' }), {
      status: 413, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
    });
  }

  let data;
  try {
    data = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: 'invalid JSON' }), {
      status: 400, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
    });
  }

  const err = validateFeedback(data);
  if (err) {
    return new Response(JSON.stringify({ error: err }), {
      status: 422, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
    });
  }

  const k = data.knowledge || {};
  await env.DB.prepare(
    `INSERT INTO feedback (daily_id, version, os, py,
       knowledge_total, staging_count, verified_count, promotion_rate, avg_staging_age,
       session_count, days_active, source_tools, top_domains, top_mcp_tools, beta_events, raw_json)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
  ).bind(
    data.daily_id,
    data.engram_version || '',
    data.os || '',
    data.python || '',
    k.total || 0,
    k.staging || 0,
    k.verified || 0,
    k.promotion_rate ?? null,
    data.avg_staging_age_days ?? null,
    data.session_count || 0,
    data.days_with_knowledge || 0,
    JSON.stringify(data.source_tools || {}),
    JSON.stringify(data.top_domains || {}),
    JSON.stringify(data.top_mcp_tools || {}),
    JSON.stringify(data.beta_events || {}),
    JSON.stringify(data),
  ).run();

  return new Response(JSON.stringify({ ok: true }), {
    status: 201, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
  });
}

// --- 事件接收 ---

async function handleEvent(request, env) {
  const contentLength = parseInt(request.headers.get('content-length') || '0');
  if (contentLength > MAX_PAYLOAD_SIZE) {
    return new Response(JSON.stringify({ error: 'payload too large' }), {
      status: 413, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
    });
  }

  let data;
  try {
    data = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: 'invalid JSON' }), {
      status: 400, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
    });
  }

  const err = validatePayload(data);
  if (err) {
    return new Response(JSON.stringify({ error: err }), {
      status: 422, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
    });
  }

  await env.DB.prepare(
    `INSERT INTO events (daily_id, version, tool_calls, knowledge, os, py, tier, schema_v)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
  ).bind(
    data.daily_id,
    data.engram_version || '',
    JSON.stringify(data.tool_calls || {}),
    JSON.stringify(data.knowledge_counts || {}),
    data.os_platform || '',
    data.python_version || '',
    data.tools_tier || 'core',
    data.schema || 1,
  ).run();

  return new Response(JSON.stringify({ ok: true }), {
    status: 201, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
  });
}

// --- PyPI 下载统计 ---

async function fetchPypiStats() {
  try {
    const [overallResp, recentResp] = await Promise.all([
      fetch('https://pypistats.org/api/packages/piia-engram/overall?mirrors=false', {
        headers: { 'User-Agent': 'engram-telemetry-worker/1.0' },
      }),
      fetch('https://pypistats.org/api/packages/piia-engram/recent?period=week', {
        headers: { 'User-Agent': 'engram-telemetry-worker/1.0' },
      }),
    ]);
    const overall = overallResp.ok ? await overallResp.json() : null;
    const recent = recentResp.ok ? await recentResp.json() : null;
    return {
      daily: overall?.data || [],
      recent: recent?.data || {},
    };
  } catch {
    return { daily: [], recent: {} };
  }
}

// --- 数据查询 ---

async function getStatsData(env) {
  // 全量统计
  const totals = await env.DB.prepare(`
    SELECT COUNT(*) AS total_events, COUNT(DISTINCT daily_id) AS unique_ids,
           COUNT(DISTINCT date(received)) AS active_days,
           MIN(received) AS first_event, MAX(received) AS last_event
    FROM events
  `).first();

  // 今日统计
  const today = await env.DB.prepare(`
    SELECT COUNT(*) AS events, COUNT(DISTINCT daily_id) AS users
    FROM events WHERE date(received) = date('now')
  `).first();

  // 7天统计
  const week = await env.DB.prepare(`
    SELECT COUNT(*) AS events, COUNT(DISTINCT daily_id) AS users,
           COUNT(DISTINCT date(received)) AS active_days
    FROM events WHERE received >= datetime('now', '-7 days')
  `).first();

  // 30天统计
  const month = await env.DB.prepare(`
    SELECT COUNT(*) AS events, COUNT(DISTINCT daily_id) AS users,
           COUNT(DISTINCT date(received)) AS active_days
    FROM events WHERE received >= datetime('now', '-30 days')
  `).first();

  // 版本分布
  const versions = await env.DB.prepare(`
    SELECT version, COUNT(*) AS count FROM events
    WHERE version != '' GROUP BY version ORDER BY count DESC LIMIT 10
  `).all();

  // 每日活跃（14天）
  const daily = await env.DB.prepare(`
    SELECT date(received) AS day, COUNT(DISTINCT daily_id) AS users, COUNT(*) AS events
    FROM events WHERE received >= datetime('now', '-14 days')
    GROUP BY day ORDER BY day DESC
  `).all();

  // 每月汇总
  const monthly = await env.DB.prepare(`
    SELECT strftime('%Y-%m', received) AS month, COUNT(*) AS events,
           COUNT(DISTINCT daily_id) AS users
    FROM events GROUP BY month ORDER BY month DESC LIMIT 12
  `).all();

  // 今日工具使用
  const todayToolRows = await env.DB.prepare(`
    SELECT tool_calls FROM events WHERE date(received) = date('now')
  `).all();

  // 7天工具使用
  const weekToolRows = await env.DB.prepare(`
    SELECT tool_calls FROM events WHERE received >= datetime('now', '-7 days')
  `).all();

  // 全量工具使用
  const allToolRows = await env.DB.prepare(`
    SELECT tool_calls FROM events
  `).all();

  function aggregateTools(rows) {
    const agg = {};
    for (const row of rows.results) {
      try {
        const calls = JSON.parse(row.tool_calls);
        for (const [name, counts] of Object.entries(calls)) {
          if (!agg[name]) agg[name] = { success: 0, error: 0 };
          agg[name].success += counts.success || 0;
          agg[name].error += counts.error || 0;
        }
      } catch { /* skip */ }
    }
    return Object.entries(agg)
      .map(([name, c]) => ({ name, total: c.success + c.error, ...c }))
      .sort((a, b) => b.total - a.total)
      .slice(0, 20);
  }

  const todayTools = aggregateTools(todayToolRows);
  const weekTools = aggregateTools(weekToolRows);
  const allTools = aggregateTools(allToolRows);

  // 操作系统分布
  const osDist = await env.DB.prepare(`
    SELECT os, COUNT(*) AS count FROM events WHERE os != '' GROUP BY os ORDER BY count DESC
  `).all();

  // Python 版本分布
  const pyDist = await env.DB.prepare(`
    SELECT py, COUNT(*) AS count FROM events WHERE py != '' GROUP BY py ORDER BY count DESC
  `).all();

  // 知识库统计（最新一条事件的 knowledge_counts）
  const knowledgeRow = await env.DB.prepare(`
    SELECT knowledge FROM events WHERE knowledge != '{}' ORDER BY received DESC LIMIT 1
  `).first();

  let knowledgeCounts = null;
  if (knowledgeRow) {
    try { knowledgeCounts = JSON.parse(knowledgeRow.knowledge); } catch {}
  }

  // 最近事件（最新10条）
  const recentEvents = await env.DB.prepare(`
    SELECT received, daily_id, version, os, py, tier,
           tool_calls, knowledge
    FROM events ORDER BY received DESC LIMIT 10
  `).all();

  // PyPI 下载统计
  const pypi = await fetchPypiStats();

  // Feedback 报告汇总
  const feedbackTotals = await env.DB.prepare(`
    SELECT COUNT(*) AS total, COUNT(DISTINCT daily_id) AS unique_users,
           AVG(knowledge_total) AS avg_knowledge, AVG(session_count) AS avg_sessions,
           AVG(promotion_rate) AS avg_promotion_rate, AVG(avg_staging_age) AS avg_staging_age,
           MAX(received) AS last_feedback
    FROM feedback
  `).first();

  const feedbackRecent = await env.DB.prepare(`
    SELECT received, daily_id, version, os, knowledge_total, staging_count,
           verified_count, promotion_rate, avg_staging_age, session_count, days_active,
           source_tools
    FROM feedback ORDER BY received DESC LIMIT 10
  `).all();

  // Feedback 来源工具聚合
  const feedbackToolAgg = {};
  for (const row of feedbackRecent.results) {
    try {
      const tools = JSON.parse(row.source_tools);
      for (const [name, count] of Object.entries(tools)) {
        feedbackToolAgg[name] = (feedbackToolAgg[name] || 0) + count;
      }
    } catch {}
  }

  return {
    totals, today, week, month,
    versions: versions.results,
    daily_active: daily.results,
    monthly_summary: monthly.results,
    today_tools: todayTools,
    week_tools: weekTools,
    all_tools: allTools,
    os_distribution: osDist.results,
    py_distribution: pyDist.results,
    knowledge_counts: knowledgeCounts,
    recent_events: recentEvents.results,
    pypi,
    feedback: {
      totals: feedbackTotals,
      recent: feedbackRecent.results,
      tool_aggregate: feedbackToolAgg,
    },
  };
}

// --- 仪表盘 HTML ---

function renderDashboard(stats) {
  const t = stats.totals;
  const uptime = t.first_event ? Math.ceil((new Date(t.last_event) - new Date(t.first_event)) / 86400000) || 1 : 0;

  // PyPI 下载统计
  const pypiDaily = stats.pypi?.daily || [];
  const pypiRecent = stats.pypi?.recent || {};
  const lastDays = pypiDaily.slice(-14);
  const maxDl = Math.max(...lastDays.map(d => d.downloads), 1);
  const totalDl = lastDays.reduce((s, d) => s + d.downloads, 0);
  const pypiBarChart = lastDays.length > 0 ? `
    <div class="download-bar">
      ${lastDays.map(d => {
        const h = Math.max(2, (d.downloads / maxDl) * 70);
        const label = d.date.slice(5); // MM-DD
        return `<div class="bar-item"><div class="bar-val">${d.downloads}</div><div class="bar" style="height:${h}px"></div><div class="bar-label">${label}</div></div>`;
      }).join('')}
    </div>` : '<div class="empty">暂无下载数据</div>';

  const weekDl = pypiRecent.last_week || 0;
  const monthDl = pypiRecent.last_month || 0;

  // 概览指标卡
  const metricsHtml = `
    <div class="metrics">
      <div class="metric"><div class="value">${(t.total_events||0).toLocaleString()}</div><div class="label">总事件数</div></div>
      <div class="metric"><div class="value">${(t.unique_ids||0).toLocaleString()}</div><div class="label">独立用户</div></div>
      <div class="metric"><div class="value">${t.active_days||0}</div><div class="label">活跃天数</div></div>
      <div class="metric"><div class="value">${uptime}</div><div class="label">运行天数</div></div>
    </div>`;

  // 时段对比卡片
  const td = stats.today || {};
  const wk = stats.week || {};
  const mo = stats.month || {};
  const periodHtml = `
    <div class="metrics four">
      <div class="metric highlight">
        <div class="period-label">今日</div>
        <div class="period-row"><span class="period-val">${td.events||0}</span><span class="period-unit">事件</span></div>
        <div class="period-row"><span class="period-val">${td.users||0}</span><span class="period-unit">用户</span></div>
      </div>
      <div class="metric">
        <div class="period-label">近 7 天</div>
        <div class="period-row"><span class="period-val">${wk.events||0}</span><span class="period-unit">事件</span></div>
        <div class="period-row"><span class="period-val">${wk.users||0}</span><span class="period-unit">用户</span></div>
        <div class="period-row"><span class="period-val">${wk.active_days||0}</span><span class="period-unit">活跃天</span></div>
      </div>
      <div class="metric">
        <div class="period-label">近 30 天</div>
        <div class="period-row"><span class="period-val">${mo.events||0}</span><span class="period-unit">事件</span></div>
        <div class="period-row"><span class="period-val">${mo.users||0}</span><span class="period-unit">用户</span></div>
        <div class="period-row"><span class="period-val">${mo.active_days||0}</span><span class="period-unit">活跃天</span></div>
      </div>
      <div class="metric">
        <div class="period-label">知识库</div>
        ${stats.knowledge_counts ? `
          <div class="period-row"><span class="period-val">${stats.knowledge_counts.lessons||0}</span><span class="period-unit">经验</span></div>
          <div class="period-row"><span class="period-val">${stats.knowledge_counts.decisions||0}</span><span class="period-unit">决策</span></div>
          <div class="period-row"><span class="period-val">${stats.knowledge_counts.domains||0}</span><span class="period-unit">领域</span></div>
        ` : `<div class="period-row"><span class="period-unit" style="opacity:0.5">暂无数据</span></div>`}
      </div>
    </div>`;

  // 每日活跃表
  const dailyRows = stats.daily_active.map(d => `
    <tr><td>${d.day}</td><td>${d.users}</td><td>${d.events}</td></tr>
  `).join('') || '<tr><td colspan="3" class="empty">暂无数据</td></tr>';

  // 每月汇总表
  const monthlyRows = stats.monthly_summary.map(m => `
    <tr><td>${m.month}</td><td>${m.users}</td><td>${m.events}</td></tr>
  `).join('') || '<tr><td colspan="3" class="empty">暂无数据</td></tr>';

  // 工具使用表生成器
  function toolTable(tools, emptyMsg) {
    if (!tools.length) return `<div class="empty">${emptyMsg}</div>`;
    return `<table><thead><tr><th>工具名称</th><th>调用次数</th><th>成功率</th></tr></thead><tbody>${
      tools.map((t, i) => {
        const rate = t.total > 0 ? ((t.success / t.total) * 100).toFixed(1) : '0.0';
        return `<tr>
          <td><span class="rank">#${i+1}</span> ${t.name}</td>
          <td>${t.total.toLocaleString()}</td>
          <td><span class="rate ${rate === '100.0' ? 'perfect' : ''}">${rate}%</span></td>
        </tr>`;
      }).join('')
    }</tbody></table>`;
  }

  // 版本标签
  const versionBadges = stats.versions.map(v =>
    `<span class="badge">${v.version || '(未知)'} <small>(${v.count})</small></span>`
  ).join(' ') || '<span class="empty-inline">暂无数据</span>';

  // 操作系统标签
  const osMap = { win32: 'Windows', darwin: 'macOS', linux: 'Linux' };
  const osBadges = stats.os_distribution.map(o =>
    `<span class="badge os">${osMap[o.os] || o.os} <small>(${o.count})</small></span>`
  ).join(' ') || '<span class="empty-inline">暂无数据</span>';

  // Python 版本标签
  const pyBadges = stats.py_distribution.map(p =>
    `<span class="badge py">${p.py} <small>(${p.count})</small></span>`
  ).join(' ') || '<span class="empty-inline">暂无数据</span>';

  // 最近事件
  const recentRows = stats.recent_events.map(e => {
    let toolCount = 0;
    try {
      const tc = JSON.parse(e.tool_calls);
      toolCount = Object.values(tc).reduce((s, c) => s + (c.success||0) + (c.error||0), 0);
    } catch {}
    return `<tr>
      <td style="white-space:nowrap">${e.received}</td>
      <td title="${e.daily_id}">${e.daily_id.substring(0,8)}...</td>
      <td>${e.version || '-'}</td>
      <td>${osMap[e.os] || e.os || '-'}</td>
      <td>${toolCount}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="5" class="empty">暂无数据</td></tr>';

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Engram 遥测仪表盘</title>
<style>
  :root { --bg: #0f1117; --card: #1a1d27; --border: #2a2d3a; --text: #e4e4e7; --muted: #71717a; --accent: #6366f1; --accent2: #8b5cf6; --green: #22c55e; --blue: #3b82f6; --orange: #f59e0b; --cyan: #06b6d4; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; padding: 2rem; }

  .header { text-align: center; margin-bottom: 2rem; position: relative; }
  .header h1 { font-size: 1.75rem; font-weight: 700; background: linear-gradient(135deg, var(--accent), var(--accent2)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 0.25rem; }
  .header p { color: var(--muted); font-size: 0.875rem; }
  .header-actions { position: absolute; top: 0; right: 0; display: flex; gap: 0.5rem; }
  .header-btn { color: var(--muted); text-decoration: none; font-size: 0.8rem; border: 1px solid var(--border); padding: 4px 12px; border-radius: 6px; background: none; cursor: pointer; transition: all 0.2s; }
  .header-btn:hover { color: var(--text); border-color: var(--muted); }
  .header-btn.refresh { color: var(--accent); border-color: var(--accent); }
  .header-btn.refresh:hover { background: rgba(99,102,241,0.1); }
  .header-btn.spinning { animation: spin 1s linear infinite; }
  @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

  .section-title { font-size: 1.1rem; font-weight: 600; margin: 2rem 0 1rem; padding-bottom: 0.5rem; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 0.5rem; }

  .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
  .metrics.four { grid-template-columns: repeat(4, 1fr); }
  .metric { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 1.25rem; text-align: center; transition: transform 0.2s; }
  .metric:hover { transform: translateY(-2px); }
  .metric .value { font-size: 2rem; font-weight: 700; background: linear-gradient(135deg, var(--accent), var(--blue)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
  .metric .label { color: var(--muted); font-size: 0.8rem; margin-top: 0.25rem; }
  .metric.highlight { border-color: var(--accent); background: linear-gradient(135deg, rgba(99,102,241,0.08), rgba(139,92,246,0.05)); }

  .period-label { font-size: 0.85rem; font-weight: 600; color: var(--accent); margin-bottom: 0.75rem; }
  .period-row { display: flex; justify-content: space-between; align-items: baseline; padding: 0.2rem 0; }
  .period-val { font-size: 1.4rem; font-weight: 700; color: var(--text); }
  .period-unit { font-size: 0.75rem; color: var(--muted); }

  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 1.5rem; margin-bottom: 1.5rem; }
  .grid.three { grid-template-columns: repeat(3, 1fr); }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; overflow: hidden; }
  .card h2 { font-size: 1rem; font-weight: 600; margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem; }
  .card h2 small { color: var(--muted); font-weight: 400; }

  table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
  th { text-align: left; color: var(--muted); font-weight: 500; padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); font-size: 0.75rem; letter-spacing: 0.03em; }
  td { padding: 0.55rem 0.75rem; border-bottom: 1px solid var(--border); }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(99,102,241,0.05); }

  .rank { color: var(--muted); font-size: 0.75rem; }
  .rate { padding: 2px 8px; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; background: rgba(34,197,94,0.1); color: var(--green); }
  .rate.perfect { background: rgba(34,197,94,0.15); }

  .badge { display: inline-block; padding: 4px 12px; border-radius: 9999px; background: rgba(99,102,241,0.1); color: var(--accent); font-size: 0.825rem; font-weight: 500; margin: 0.2rem; }
  .badge.os { background: rgba(59,130,246,0.1); color: var(--blue); }
  .badge.py { background: rgba(6,182,212,0.1); color: var(--cyan); }
  .badge small { opacity: 0.7; }
  .tags { padding: 0.5rem 0; }

  .empty { color: var(--muted); text-align: center; padding: 1.5rem !important; font-style: italic; }
  .empty-inline { color: var(--muted); font-style: italic; font-size: 0.875rem; }

  .tab-group { display: flex; gap: 0; margin-bottom: 1rem; border-bottom: 2px solid var(--border); }
  .tab { padding: 0.5rem 1rem; font-size: 0.85rem; color: var(--muted); cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.2s; user-select: none; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  .footer { text-align: center; margin-top: 2.5rem; padding-top: 1.5rem; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.75rem; }
  .footer a { color: var(--accent); text-decoration: none; }
  .footer a:hover { text-decoration: underline; }

  .download-bar { display: flex; align-items: flex-end; gap: 2px; height: 80px; padding: 0.5rem 0; }
  .download-bar .bar-item { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 2px; }
  .download-bar .bar { background: linear-gradient(180deg, var(--accent), var(--accent2)); border-radius: 3px 3px 0 0; min-height: 2px; width: 100%; transition: height 0.3s; }
  .download-bar .bar-label { font-size: 0.6rem; color: var(--muted); white-space: nowrap; }
  .download-bar .bar-val { font-size: 0.65rem; color: var(--text); font-weight: 600; }

  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--green); margin-right: 0.5rem; animation: pulse 2s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

  @media (max-width: 900px) { .grid.three { grid-template-columns: 1fr; } .metrics.four { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 500px) { body { padding: 1rem; } .grid { grid-template-columns: 1fr; } .metrics { grid-template-columns: repeat(2, 1fr); } }
</style>
</head>
<body>
  <div class="header">
    <div class="header-actions">
      <button class="header-btn refresh" onclick="location.reload()" title="刷新数据">&#8635;</button>
      <a href="/logout" class="header-btn">退出登录</a>
    </div>
    <h1>Engram 遥测仪表盘</h1>
    <p><span class="dot"></span>匿名使用统计 · 隐私优先</p>
  </div>

  <!-- 总览 -->
  ${metricsHtml}

  <!-- PyPI 下载统计 -->
  <div class="section-title">&#128230; PyPI 下载统计</div>
  <div class="grid">
    <div class="card">
      <h2>每日下载量 <small>（近 14 天，数据延迟 1-2 天）</small></h2>
      ${pypiBarChart}
    </div>
    <div class="card">
      <h2>下载汇总</h2>
      <div style="padding:0.5rem 0">
        <div class="period-row"><span class="period-val">${totalDl.toLocaleString()}</span><span class="period-unit">近 14 天总下载</span></div>
        <div class="period-row"><span class="period-val">${weekDl.toLocaleString()}</span><span class="period-unit">近 7 天下载</span></div>
        <div class="period-row"><span class="period-val">${monthDl.toLocaleString()}</span><span class="period-unit">近 30 天下载</span></div>
        <div class="period-row"><span class="period-val">${lastDays.length > 0 ? lastDays[lastDays.length-1].downloads.toLocaleString() : '-'}</span><span class="period-unit">最新日下载 (${lastDays.length > 0 ? lastDays[lastDays.length-1].date : '-'})</span></div>
      </div>
    </div>
  </div>

  <!-- 时段对比 -->
  <div class="section-title">&#128202; 时段数据</div>
  ${periodHtml}

  <!-- 工具使用 -->
  <div class="section-title">&#128295; 工具使用分析</div>
  <div class="card" style="margin-bottom:1.5rem">
    <div class="tab-group">
      <div class="tab active" onclick="switchTab(this,'tools-today')">今日</div>
      <div class="tab" onclick="switchTab(this,'tools-week')">近 7 天</div>
      <div class="tab" onclick="switchTab(this,'tools-all')">全部</div>
    </div>
    <div id="tools-today" class="tab-content active">${toolTable(stats.today_tools, '今日暂无工具调用')}</div>
    <div id="tools-week" class="tab-content">${toolTable(stats.week_tools, '近 7 天暂无工具调用')}</div>
    <div id="tools-all" class="tab-content">${toolTable(stats.all_tools, '暂无工具调用数据')}</div>
  </div>

  <!-- 活跃趋势 -->
  <div class="section-title">&#128200; 活跃趋势</div>
  <div class="grid">
    <div class="card">
      <h2>每日活跃 <small>（近 14 天）</small></h2>
      <table><thead><tr><th>日期</th><th>用户数</th><th>事件数</th></tr></thead><tbody>${dailyRows}</tbody></table>
    </div>
    <div class="card">
      <h2>每月汇总</h2>
      <table><thead><tr><th>月份</th><th>用户数</th><th>事件数</th></tr></thead><tbody>${monthlyRows}</tbody></table>
    </div>
  </div>

  <!-- 环境分布 -->
  <div class="section-title">&#128187; 环境分布</div>
  <div class="grid three">
    <div class="card"><h2>版本分布</h2><div class="tags">${versionBadges}</div></div>
    <div class="card"><h2>操作系统</h2><div class="tags">${osBadges}</div></div>
    <div class="card"><h2>Python 版本</h2><div class="tags">${pyBadges}</div></div>
  </div>

  <!-- 最近事件 -->
  <div class="section-title">&#128214; 最近事件</div>
  <div class="card" style="margin-bottom:1.5rem">
    <table>
      <thead><tr><th>时间</th><th>用户 ID</th><th>版本</th><th>系统</th><th>工具调用</th></tr></thead>
      <tbody>${recentRows}</tbody>
    </table>
  </div>

  <!-- Feedback 报告 -->
  <div class="section-title">&#128203; 用户反馈报告</div>
  ${(() => {
    const fb = stats.feedback || {};
    const ft = fb.totals || {};
    if (!ft.total) return '<div class="card"><div class="empty">暂无反馈报告</div></div>';
    const avgPR = ft.avg_promotion_rate != null ? (ft.avg_promotion_rate * 100).toFixed(1) + '%' : '-';
    const avgAge = ft.avg_staging_age != null ? ft.avg_staging_age.toFixed(1) + ' 天' : '-';
    const fbRows = (fb.recent || []).map(r => {
      const pr = r.promotion_rate != null ? (r.promotion_rate * 100).toFixed(0) + '%' : '-';
      const age = r.avg_staging_age != null ? r.avg_staging_age.toFixed(1) : '-';
      let srcTools = '-';
      try { const st = JSON.parse(r.source_tools); srcTools = Object.keys(st).join(', ') || '-'; } catch {}
      return '<tr>' +
        '<td style="white-space:nowrap">' + r.received + '</td>' +
        '<td title="' + r.daily_id + '">' + r.daily_id.substring(0,8) + '...</td>' +
        '<td>' + (r.version || '-') + '</td>' +
        '<td>' + r.knowledge_total + ' (' + r.staging_count + 'S/' + r.verified_count + 'V)</td>' +
        '<td>' + pr + '</td>' +
        '<td>' + age + '</td>' +
        '<td>' + r.session_count + '</td>' +
        '<td>' + r.days_active + '</td>' +
        '<td>' + srcTools + '</td>' +
      '</tr>';
    }).join('') || '<tr><td colspan="9" class="empty">暂无</td></tr>';

    return '<div class="metrics four">' +
      '<div class="metric highlight"><div class="period-label">反馈总数</div><div class="period-row"><span class="period-val">' + ft.total + '</span><span class="period-unit">份报告</span></div><div class="period-row"><span class="period-val">' + (ft.unique_users || 0) + '</span><span class="period-unit">独立用户</span></div></div>' +
      '<div class="metric"><div class="period-label">平均知识量</div><div class="period-row"><span class="period-val">' + Math.round(ft.avg_knowledge || 0) + '</span><span class="period-unit">条知识</span></div><div class="period-row"><span class="period-val">' + Math.round(ft.avg_sessions || 0) + '</span><span class="period-unit">会话数</span></div></div>' +
      '<div class="metric"><div class="period-label">治理指标</div><div class="period-row"><span class="period-val">' + avgPR + '</span><span class="period-unit">确认率</span></div><div class="period-row"><span class="period-val">' + avgAge + '</span><span class="period-unit">staging 滞留</span></div></div>' +
      '<div class="metric"><div class="period-label">最后报告</div><div class="period-row"><span class="period-unit">' + (ft.last_feedback || '暂无') + '</span></div></div>' +
    '</div>' +
    '<div class="card" style="margin-top:1rem;margin-bottom:1.5rem"><h2>最近反馈明细</h2>' +
    '<table><thead><tr><th>时间</th><th>用户</th><th>版本</th><th>知识(S/V)</th><th>确认率</th><th>滞留天</th><th>会话</th><th>活跃天</th><th>来源工具</th></tr></thead><tbody>' + fbRows + '</tbody></table></div>';
  })()}

  <div class="footer">
    基于 <a href="https://github.com/Patdolitse/piia-engram">Engram</a> ·
    Cloudflare Workers + D1 ·
    <a href="/v1/stats">JSON API</a> ·
    最后事件: ${t.last_event || '暂无'}
  </div>

  <script>
  function switchTab(el, id) {
    const group = el.parentElement;
    const card = group.parentElement;
    group.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    card.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
    document.getElementById(id).classList.add('active');
  }
  </script>
</body>
</html>`;
}

// --- 路由 ---

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // 公开接口
    if (url.pathname === '/v1/events' && request.method === 'POST') {
      return handleEvent(request, env);
    }
    if (url.pathname === '/v1/feedback' && request.method === 'POST') {
      return handleFeedback(request, env);
    }
    if (url.pathname === '/v1/health') {
      return new Response(JSON.stringify({ status: 'ok', service: 'engram-telemetry' }), {
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      });
    }

    // 登录页
    if (url.pathname === '/login' && request.method === 'GET') {
      return new Response(renderLogin(), { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
    }

    // 登录处理
    if (url.pathname === '/login' && request.method === 'POST') {
      const formData = await request.formData();
      const password = formData.get('password') || '';
      if (!env.DASH_PASSWORD || password === env.DASH_PASSWORD) {
        const sessionToken = await hashPassword(env.DASH_PASSWORD || '', 'engram-session');
        return new Response(null, {
          status: 302,
          headers: {
            'Location': '/',
            'Set-Cookie': `${COOKIE_NAME}=${sessionToken}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${SESSION_MAX_AGE}`,
          },
        });
      }
      return new Response(renderLogin('密码错误，请重试'), {
        status: 401, headers: { 'Content-Type': 'text/html; charset=utf-8' },
      });
    }

    // 退出登录
    if (url.pathname === '/logout') {
      return new Response(null, {
        status: 302,
        headers: {
          'Location': '/login',
          'Set-Cookie': `${COOKIE_NAME}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0`,
        },
      });
    }

    // 需认证的接口
    const authed = await isAuthenticated(request, env);
    if (!authed) {
      return Response.redirect(url.origin + '/login', 302);
    }

    if (url.pathname === '/v1/stats' || url.pathname === '/') {
      const stats = await getStatsData(env);
      const accept = request.headers.get('accept') || '';
      if (url.pathname === '/' || accept.includes('text/html')) {
        return new Response(renderDashboard(stats), { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
      }
      return new Response(JSON.stringify(stats, null, 2), {
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      });
    }

    return Response.redirect(url.origin + '/', 302);
  },
};
