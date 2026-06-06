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
  'prev_version', 'session_type', 'install_age_bucket', 'error_categories',
  // Telemetry Analysis Contract v1.1 (P1 — derived buckets, anonymous)
  'contract_version', 'version_adoption', 'activation_state',
  'returning_bucket', 'error_trend',
]);
const SESSION_TYPES = new Set(['first_run', 'regular']);
const INSTALL_AGE_BUCKETS = new Set(['first_day', '2_7_days', '8_30_days', '31_plus_days', 'unknown']);
const ERROR_CATEGORIES = new Set(['timeout', 'validation', 'io', 'permission', 'network', 'unknown']);
// v1.1 P1 bucket vocabularies (mirror src/piia_engram/telemetry.py)
const VERSION_ADOPTION = new Set(['first', 'same', 'upgrade', 'downgrade', 'changed']);
const ACTIVATION_STATES = new Set(['activated', 'not_activated', 'unknown']);
const RETURNING_BUCKETS = new Set(['new', 'returning']);
const ERROR_TRENDS = new Set(['none', 'first', 'up', 'down', 'flat']);

function validatePayload(data) {
  if (!data || typeof data !== 'object') return 'invalid JSON';
  if (!data.daily_id || typeof data.daily_id !== 'string') return 'missing daily_id';
  if (data.daily_id.length > 64) return 'daily_id too long';
  if (data.engram_version && data.engram_version.length > 20) return 'version too long';
  if (data.prev_version != null && typeof data.prev_version !== 'string') return 'prev_version must be string or null';
  if (data.prev_version && data.prev_version.length > 20) return 'prev_version too long';
  if (data.session_type && !SESSION_TYPES.has(data.session_type)) return 'invalid session_type';
  if (data.install_age_bucket && !INSTALL_AGE_BUCKETS.has(data.install_age_bucket)) return 'invalid install_age_bucket';
  // v1.1 P1 buckets (all optional; reject unknown values to keep the field clean)
  if (data.contract_version != null && typeof data.contract_version !== 'string') return 'contract_version must be string';
  if (data.contract_version && data.contract_version.length > 10) return 'contract_version too long';
  if (data.version_adoption && !VERSION_ADOPTION.has(data.version_adoption)) return 'invalid version_adoption';
  if (data.activation_state && !ACTIVATION_STATES.has(data.activation_state)) return 'invalid activation_state';
  if (data.returning_bucket && !RETURNING_BUCKETS.has(data.returning_bucket)) return 'invalid returning_bucket';
  if (data.error_trend && !ERROR_TRENDS.has(data.error_trend)) return 'invalid error_trend';
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
  if (data.error_categories) {
    if (typeof data.error_categories !== 'object') return 'error_categories must be object';
    for (const [key, val] of Object.entries(data.error_categories)) {
      if (!ERROR_CATEGORIES.has(key)) return `invalid error category: ${key}`;
      if (typeof val !== 'number') return `error_categories.${key} must be number`;
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

  // Tiered INSERT with graceful column fallback so deploy/migration order stays
  // flexible (no event is ever dropped):
  //   1. full v1.1 (P0 + P1 columns)
  //   2. v1 (P0 columns) — if the v1.1 columns are missing
  //   3. legacy (base columns) — if the P0 columns are also missing
  const insertP1 = () => env.DB.prepare(
    `INSERT INTO events (
       daily_id, version, prev_version, session_type, install_age_bucket,
       tool_calls, knowledge, error_categories, os, py, tier, schema_v,
       contract_version, version_adoption, activation_state, returning_bucket, error_trend
     )
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
  ).bind(
    data.daily_id,
    data.engram_version || '',
    data.prev_version || '',
    data.session_type || '',
    data.install_age_bucket || '',
    JSON.stringify(data.tool_calls || {}),
    JSON.stringify(data.knowledge_counts || {}),
    JSON.stringify(data.error_categories || {}),
    data.os_platform || '',
    data.python_version || '',
    data.tools_tier || 'core',
    data.schema || 1,
    data.contract_version || '',
    data.version_adoption || '',
    data.activation_state || '',
    data.returning_bucket || '',
    data.error_trend || '',
  ).run();

  const insertP0 = () => env.DB.prepare(
    `INSERT INTO events (
       daily_id, version, prev_version, session_type, install_age_bucket,
       tool_calls, knowledge, error_categories, os, py, tier, schema_v
     )
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
  ).bind(
    data.daily_id,
    data.engram_version || '',
    data.prev_version || '',
    data.session_type || '',
    data.install_age_bucket || '',
    JSON.stringify(data.tool_calls || {}),
    JSON.stringify(data.knowledge_counts || {}),
    JSON.stringify(data.error_categories || {}),
    data.os_platform || '',
    data.python_version || '',
    data.tools_tier || 'core',
    data.schema || 1,
  ).run();

  const insertLegacy = () => env.DB.prepare(
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

  const P1_COLS = ['contract_version', 'version_adoption', 'activation_state',
                   'returning_bucket', 'error_trend'];
  const P0_COLS = ['prev_version', 'session_type', 'install_age_bucket', 'error_categories'];
  const mentionsAny = (msg, cols) => cols.some((c) => msg.includes(c));

  try {
    await insertP1();
  } catch (err1) {
    const msg1 = String(err1 && err1.message || err1);
    if (!mentionsAny(msg1, P1_COLS) && !mentionsAny(msg1, P0_COLS)) throw err1;
    try {
      await insertP0();
    } catch (err2) {
      const msg2 = String(err2 && err2.message || err2);
      if (!mentionsAny(msg2, P0_COLS)) throw err2;
      await insertLegacy();
    }
  }

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
  const eventColumnsResult = await env.DB.prepare(`PRAGMA table_info(events)`).all();
  const eventColumns = new Set((eventColumnsResult.results || []).map(c => c.name));
  const hasAnalysisContractV1 = [
    'prev_version',
    'session_type',
    'install_age_bucket',
    'error_categories',
  ].every(name => eventColumns.has(name));
  // Telemetry Analysis Contract v1.1 — derived anonymous buckets. Available only
  // once the v1.1 migration has been applied; until then the tiles render an
  // explicit "migration not applied" placeholder (no fabricated numbers).
  const hasAnalysisContractV1_1 = [
    'contract_version',
    'version_adoption',
    'activation_state',
    'returning_bucket',
    'error_trend',
  ].every(name => eventColumns.has(name));

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

  let versionUpgrades = { results: [] };
  let sessionTypes = { results: [] };
  let installAgeBuckets = { results: [] };
  let errorCategoryRows = { results: [] };
  if (hasAnalysisContractV1) {
    versionUpgrades = await env.DB.prepare(`
      SELECT prev_version, version, COUNT(*) AS count FROM events
      WHERE prev_version != '' AND version != '' AND prev_version != version
      GROUP BY prev_version, version ORDER BY count DESC LIMIT 10
    `).all();
    sessionTypes = await env.DB.prepare(`
      SELECT session_type, COUNT(*) AS count FROM events
      WHERE session_type != '' GROUP BY session_type ORDER BY count DESC
    `).all();
    installAgeBuckets = await env.DB.prepare(`
      SELECT install_age_bucket, COUNT(*) AS count FROM events
      WHERE install_age_bucket != '' GROUP BY install_age_bucket ORDER BY count DESC
    `).all();
    errorCategoryRows = await env.DB.prepare(`
      SELECT error_categories FROM events WHERE error_categories != '{}'
    `).all();
  }

  // Contract v1.1 — derived bucket distributions (all anonymous-daily-id based).
  let versionAdoption = { results: [] };
  let activationStates = { results: [] };
  let returningBuckets = { results: [] };
  let errorTrends = { results: [] };
  if (hasAnalysisContractV1_1) {
    versionAdoption = await env.DB.prepare(`
      SELECT version_adoption, COUNT(*) AS count FROM events
      WHERE version_adoption != '' GROUP BY version_adoption ORDER BY count DESC
    `).all();
    activationStates = await env.DB.prepare(`
      SELECT activation_state, COUNT(*) AS count FROM events
      WHERE activation_state != '' GROUP BY activation_state ORDER BY count DESC
    `).all();
    returningBuckets = await env.DB.prepare(`
      SELECT returning_bucket, COUNT(*) AS count FROM events
      WHERE returning_bucket != '' GROUP BY returning_bucket ORDER BY count DESC
    `).all();
    errorTrends = await env.DB.prepare(`
      SELECT error_trend, COUNT(*) AS count FROM events
      WHERE error_trend != '' GROUP BY error_trend ORDER BY count DESC
    `).all();
  }

  // 每日活跃（long window for dashboard range selectors）
  const daily = await env.DB.prepare(`
    SELECT date(received) AS day, COUNT(DISTINCT daily_id) AS users, COUNT(*) AS events
    FROM events GROUP BY day ORDER BY day DESC LIMIT 400
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

  function aggregateErrorCategories(rows) {
    const agg = {};
    for (const row of rows.results || []) {
      try {
        const categories = JSON.parse(row.error_categories || '{}');
        for (const [category, count] of Object.entries(categories)) {
          agg[category] = (agg[category] || 0) + (Number(count) || 0);
        }
      } catch { /* skip */ }
    }
    return Object.entries(agg)
      .map(([category, count]) => ({ category, count }))
      .filter(row => row.count > 0)
      .sort((a, b) => b.count - a.count);
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
    analysis_contract_v1: {
      available: hasAnalysisContractV1,
      version_upgrades: versionUpgrades.results,
      session_types: sessionTypes.results,
      install_age_buckets: installAgeBuckets.results,
      error_categories: aggregateErrorCategories(errorCategoryRows),
    },
    analysis_contract_v1_1: {
      available: hasAnalysisContractV1_1,
      version_adoption: versionAdoption.results,
      activation_states: activationStates.results,
      returning_buckets: returningBuckets.results,
      error_trends: errorTrends.results,
    },
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

export function renderDashboard(stats) {
  const t = stats.totals;
  const uptime = t.first_event ? Math.ceil((new Date(t.last_event) - new Date(t.first_event)) / 86400000) || 1 : 0;

  // PyPI 下载统计
  const pypiDaily = stats.pypi?.daily || [];
  const pypiRecent = stats.pypi?.recent || {};
  const rangeOptions = [
    { key: '7d', label: '近 7 天', mode: 'day', days: 7, labelStep: 1 },
    { key: '14d', label: '近 14 天', mode: 'day', days: 14, labelStep: 2 },
    { key: '30d', label: '近 30 天', mode: 'day', days: 30, labelStep: 5 },
    { key: 'month', label: '按月', mode: 'month' },
    { key: 'quarter', label: '按季度', mode: 'quarter' },
    { key: 'year', label: '按年', mode: 'year' },
  ];
  const defaultRange = '30d';
  const todayMs = Date.now();
  const dayMs = 86400000;

  function cutoffDay(days) {
    return new Date(todayMs - (days - 1) * dayMs).toISOString().slice(0, 10);
  }

  function bucketKey(dateText, mode) {
    if (!dateText) return '-';
    if (mode === 'month') return dateText.slice(0, 7);
    if (mode === 'year') return dateText.slice(0, 4);
    if (mode === 'quarter') {
      const month = Number(dateText.slice(5, 7)) || 1;
      return `${dateText.slice(0, 4)}-Q${Math.floor((month - 1) / 3) + 1}`;
    }
    return dateText.slice(0, 10);
  }

  function aggregateRange(rows, dateKey, valueKeys, option) {
    const filtered = option.days
      ? rows.filter(row => (row[dateKey] || '') >= cutoffDay(option.days))
      : rows;
    const grouped = {};
    for (const row of filtered) {
      const key = bucketKey(row[dateKey], option.mode);
      if (!grouped[key]) {
        grouped[key] = { label: key };
        for (const valueKey of valueKeys) grouped[key][valueKey] = 0;
      }
      for (const valueKey of valueKeys) {
        grouped[key][valueKey] += Number(row[valueKey] || 0);
      }
    }
    return Object.values(grouped).sort((a, b) => a.label.localeCompare(b.label));
  }

  function renderDownloadRangeButtons() {
    return `<div class="range-tabs">
      <button type="button" class="range-tab" data-download-range="7d" onclick="setDownloadRange('7d')">近 7 天</button>
      <button type="button" class="range-tab" data-download-range="14d" onclick="setDownloadRange('14d')">近 14 天</button>
      <button type="button" class="range-tab active" data-download-range="30d" onclick="setDownloadRange('30d')">近 30 天</button>
      <button type="button" class="range-tab" data-download-range="month" onclick="setDownloadRange('month')">按月</button>
      <button type="button" class="range-tab" data-download-range="quarter" onclick="setDownloadRange('quarter')">按季度</button>
      <button type="button" class="range-tab" data-download-range="year" onclick="setDownloadRange('year')">按年</button>
    </div>`;
  }

  function renderActivityRangeButtons() {
    return `<div class="range-tabs">
      <button type="button" class="range-tab" data-activity-range="7d" onclick="setActivityRange('7d')">近 7 天</button>
      <button type="button" class="range-tab" data-activity-range="14d" onclick="setActivityRange('14d')">近 14 天</button>
      <button type="button" class="range-tab active" data-activity-range="30d" onclick="setActivityRange('30d')">近 30 天</button>
      <button type="button" class="range-tab" data-activity-range="month" onclick="setActivityRange('month')">按月</button>
      <button type="button" class="range-tab" data-activity-range="quarter" onclick="setActivityRange('quarter')">按季度</button>
      <button type="button" class="range-tab" data-activity-range="year" onclick="setActivityRange('year')">按年</button>
    </div>`;
  }

  function renderBars(rows, valueKey, emptyMessage, option = {}) {
    if (!rows.length) return `<div class="empty">${emptyMessage}</div>`;
    const maxValue = Math.max(...rows.map(row => Number(row[valueKey] || 0)), 1);
    const labelStep = option.labelStep || 1;
    const rowCount = rows.length;
    return `<div class="bar-scroll"><div class="download-bar">${
      rows.map((row, index) => {
        const value = Number(row[valueKey] || 0);
        const height = Math.max(2, (value / maxValue) * 70);
        const showLabel = index === 0 || index === rowCount - 1 || index % labelStep === 0;
        const title = `${row.label}: ${value.toLocaleString()}`;
        return `<div class="bar-item" title="${title}" aria-label="${title}"><div class="bar" style="height:${height}px"></div><div class="bar-label">${showLabel ? row.label : ''}</div></div>`;
      }).join('')
    }</div></div><div class="bar-peak">峰值 ${maxValue.toLocaleString()}</div>`;
  }

  const downloadSource = pypiDaily
    .map(row => ({ date: row.date, downloads: Number(row.downloads || 0) }))
    .filter(row => row.date)
    .sort((a, b) => a.date.localeCompare(b.date));
  const downloadRangeRows = rangeOptions.map(option => {
    const rows = aggregateRange(downloadSource, 'date', ['downloads'], option);
    const total = rows.reduce((sum, row) => sum + row.downloads, 0);
    const latest = rows.length ? rows[rows.length - 1] : null;
    const avg = rows.length ? Math.round(total / rows.length) : 0;
    return `<div class="range-panel ${option.key === defaultRange ? 'active' : ''}" data-download-panel="${option.key}" data-download-total="${total.toLocaleString()}">
      ${renderBars(rows, 'downloads', '暂无下载数据', option)}
      <div class="range-summary">
        <span>总下载：<strong>${total.toLocaleString()}</strong></span>
        <span>平均每档：<strong>${avg.toLocaleString()}</strong></span>
        <span>最新：<strong>${latest ? latest.downloads.toLocaleString() : '-'}</strong>${latest ? ` (${latest.label})` : ''}</span>
      </div>
    </div>`;
  }).join('');
  const downloadRangeControls = renderDownloadRangeButtons();
  const weekDl = pypiRecent.last_week || 0;
  const monthDl = pypiRecent.last_month || 0;
  const defaultDownloadRows = aggregateRange(downloadSource, 'date', ['downloads'], rangeOptions.find(option => option.key === defaultRange));
  const defaultDownloadTotal = defaultDownloadRows.reduce((sum, row) => sum + row.downloads, 0);
  const latestDownload = downloadSource.length ? downloadSource[downloadSource.length - 1] : null;

  // 概览指标卡
  const metricsHtml = `
    <div class="metrics">
      <div class="metric"><div class="value">${(t.total_events||0).toLocaleString()}</div><div class="label">总事件数</div></div>
      <div class="metric"><div class="value">${(t.unique_ids||0).toLocaleString()}</div><div class="label">匿名日 ID（累计）</div></div>
      <div class="metric"><div class="value">${t.active_days||0}</div><div class="label">活跃天数</div></div>
      <div class="metric"><div class="value">${uptime}</div><div class="label">运行天数</div></div>
    </div>`;
  const dailyIdNotice = `
    <div class="notice">口径：daily_id 按 UTC 日期轮换；今日可近似活跃安装，近 7/30 天和月度为匿名日 ID 次数，不等同真实去重用户。</div>`;

  // 时段对比卡片
  const td = stats.today || {};
  const wk = stats.week || {};
  const mo = stats.month || {};
  const periodHtml = `
    <div class="metrics four">
      <div class="metric highlight">
        <div class="period-label">今日</div>
        <div class="period-row"><span class="period-val">${td.events||0}</span><span class="period-unit">事件</span></div>
        <div class="period-row"><span class="period-val">${td.users||0}</span><span class="period-unit">匿名日 ID</span></div>
      </div>
      <div class="metric">
        <div class="period-label">近 7 天</div>
        <div class="period-row"><span class="period-val">${wk.events||0}</span><span class="period-unit">事件</span></div>
        <div class="period-row"><span class="period-val">${wk.users||0}</span><span class="period-unit">匿名日 ID</span></div>
        <div class="period-row"><span class="period-val">${wk.active_days||0}</span><span class="period-unit">活跃天</span></div>
      </div>
      <div class="metric">
        <div class="period-label">近 30 天</div>
        <div class="period-row"><span class="period-val">${mo.events||0}</span><span class="period-unit">事件</span></div>
        <div class="period-row"><span class="period-val">${mo.users||0}</span><span class="period-unit">匿名日 ID</span></div>
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

  // 活跃趋势表
  const activitySource = (stats.daily_active || [])
    .map(row => ({
      day: row.day,
      users: Number(row.users || 0),
      events: Number(row.events || 0),
    }))
    .filter(row => row.day)
    .sort((a, b) => a.day.localeCompare(b.day));
  const activityRangeRows = rangeOptions.map(option => {
    const rows = aggregateRange(activitySource, 'day', ['users', 'events'], option);
    const bodyRows = [...rows].reverse().map(row => `
      <tr><td>${row.label}</td><td>${row.users.toLocaleString()}</td><td>${row.events.toLocaleString()}</td></tr>
    `).join('') || '<tr><td colspan="3" class="empty">暂无数据</td></tr>';
    const totalUsers = rows.reduce((sum, row) => sum + row.users, 0);
    const totalEvents = rows.reduce((sum, row) => sum + row.events, 0);
    return `<div class="range-panel ${option.key === defaultRange ? 'active' : ''}" data-activity-panel="${option.key}">
      <table><thead><tr><th>时间档</th><th>匿名日 ID 次数</th><th>事件数</th></tr></thead><tbody>${bodyRows}</tbody></table>
      <div class="range-summary">
        <span>匿名日 ID 次数：<strong>${totalUsers.toLocaleString()}</strong></span>
        <span>事件数：<strong>${totalEvents.toLocaleString()}</strong></span>
        <span>档位：<strong>${rows.length.toLocaleString()}</strong></span>
      </div>
    </div>`;
  }).join('');
  const activityRangeControls = renderActivityRangeButtons();

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

  // Contract v1 P0 分析
  const ac = stats.analysis_contract_v1 || {};
  const sessionLabels = { first_run: '首跑激活', regular: '常规会话' };
  const ageLabels = {
    first_day: '首日',
    '2_7_days': '2-7 天',
    '8_30_days': '8-30 天',
    '31_plus_days': '31 天以上',
    unknown: '未知',
  };
  function countBadges(rows, key, labels = {}) {
    if (!rows || !rows.length) return '<span class="empty-inline">暂无数据</span>';
    return rows.map(row =>
      `<span class="badge">${labels[row[key]] || row[key]} <small>(${row.count})</small></span>`
    ).join(' ');
  }
  const upgradeRows = (ac.version_upgrades || []).map(row => `
    <tr><td>${row.prev_version}</td><td>${row.version}</td><td>${row.count}</td></tr>
  `).join('') || '<tr><td colspan="3" class="empty">暂无升级迁移数据</td></tr>';
  const errorBadges = countBadges(ac.error_categories || [], 'category');
  const contractHtml = ac.available ? `
    <div class="section-title">&#128202; Contract v1 分析</div>
    <div class="grid">
      <div class="card">
        <h2>版本升级</h2>
        <table><thead><tr><th>上一版本</th><th>当前版本</th><th>事件数</th></tr></thead><tbody>${upgradeRows}</tbody></table>
      </div>
      <div class="card">
        <h2>首跑激活</h2>
        <div class="tags">${countBadges(ac.session_types || [], 'session_type', sessionLabels)}</div>
      </div>
      <div class="card">
        <h2>匿名新老分桶</h2>
        <div class="tags">${countBadges(ac.install_age_buckets || [], 'install_age_bucket', ageLabels)}</div>
      </div>
      <div class="card">
        <h2>错误类别</h2>
        <div class="tags">${errorBadges}</div>
      </div>
    </div>` : `
    <div class="section-title">&#128202; Contract v1 分析</div>
    <div class="card" style="margin-bottom:1.5rem">
      <div class="empty">D1 schema 尚未应用 Telemetry Analysis Contract v1 迁移，暂无 P0 字段分析。</div>
    </div>`;

  // Contract v1.1 分析 — 全部基于匿名日 ID 的派生分桶，不代表去重真人。
  const ac11 = stats.analysis_contract_v1_1 || {};
  const adoptionLabels = { first: '首次', same: '持平', upgrade: '升级', downgrade: '降级', changed: '变更' };
  const activationLabels = { activated: '已激活', not_activated: '未激活', unknown: '未知' };
  const returningLabels = { new: '新增', returning: '回访' };
  const trendLabels = { none: '无错误', first: '首次', up: '上升', down: '下降', flat: '持平' };
  const contractV11Html = ac11.available ? `
    <div class="section-title">&#128203; Contract v1.1 分析 <small style="font-weight:400;color:var(--muted)">（匿名日 ID 派生分桶）</small></div>
    <div class="grid">
      <div class="card">
        <h2>版本采纳</h2>
        <div class="tags">${countBadges(ac11.version_adoption || [], 'version_adoption', adoptionLabels)}</div>
      </div>
      <div class="card">
        <h2>知识激活</h2>
        <div class="tags">${countBadges(ac11.activation_states || [], 'activation_state', activationLabels)}</div>
      </div>
      <div class="card">
        <h2>匿名回访分桶</h2>
        <div class="tags">${countBadges(ac11.returning_buckets || [], 'returning_bucket', returningLabels)}</div>
        <div class="notice" style="margin:0.5rem 0 0">口径：按轮换的匿名日 ID 划分新增/回访，近似流失趋势，不等同去重真人。</div>
      </div>
      <div class="card">
        <h2>错误趋势</h2>
        <div class="tags">${countBadges(ac11.error_trends || [], 'error_trend', trendLabels)}</div>
      </div>
    </div>` : `
    <div class="section-title">&#128203; Contract v1.1 分析</div>
    <div class="card" style="margin-bottom:1.5rem">
      <div class="empty">D1 schema 尚未应用 Telemetry Analysis Contract v1.1 迁移，暂无派生分桶分析。</div>
    </div>`;

  const contractVNextLocalHtml = `
    <div class="section-title">&#128269; vNext 本地信号 <small style="font-weight:400;color:var(--muted)">（默认关闭 / 仅本地 / 未写入远程 D1）</small></div>
    <div class="card" style="margin-bottom:1.5rem">
      <h2>本地分析预览</h2>
      <div class="tags">
        <span class="badge">recall_hit_rate</span>
        <span class="badge">cross_tool_handoffs</span>
        <span class="badge">user_bucket</span>
        <span class="badge">activation_depth</span>
      </div>
      <div class="notice" style="margin:0.5rem 0 0;text-align:left">
        口径：这些信号只在客户端显式请求本地预览时计算，用于判断跨工具接续、使用强度和激活深度；默认关闭，不随远程遥测发送，也不写入当前 D1 schema。
      </div>
    </div>`;

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
  .notice { color: var(--muted); font-size: 0.78rem; text-align: center; margin: -0.75rem 0 1.5rem; }

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
  .range-tabs { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 1rem; }
  .range-tab { color: var(--muted); background: transparent; border: 1px solid var(--border); border-radius: 6px; padding: 0.4rem 0.75rem; font-size: 0.8rem; cursor: pointer; transition: all 0.2s; }
  .range-tab:hover { color: var(--text); border-color: var(--accent); }
  .range-tab.active { color: var(--text); background: rgba(99, 102, 241, 0.16); border-color: var(--accent); }
  .range-panel { display: none; }
  .range-panel.active { display: block; }
  .range-summary { display: flex; flex-wrap: wrap; gap: 0.75rem 1rem; margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.8rem; }
  .range-summary strong { color: var(--text); }
  .pypi-card { margin-bottom: 1.5rem; }
  .pypi-kpis { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin-bottom: 1rem; }
  .pypi-kpis .metric { padding: 1rem; }
  .pypi-kpis .value { font-size: 1.6rem; }

  .footer { text-align: center; margin-top: 2.5rem; padding-top: 1.5rem; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.75rem; }
  .footer a { color: var(--accent); text-decoration: none; }
  .footer a:hover { text-decoration: underline; }

  .bar-scroll { overflow-x: auto; padding-bottom: 0.25rem; }
  .download-bar { display: flex; align-items: flex-end; gap: 4px; min-width: max-content; height: 88px; padding: 0.5rem 0; }
  .download-bar .bar-item { min-width: 28px; flex: 1 0 28px; display: flex; flex-direction: column; align-items: center; gap: 3px; }
  .download-bar .bar { background: linear-gradient(180deg, var(--accent), var(--accent2)); border-radius: 3px 3px 0 0; min-height: 2px; width: 100%; transition: height 0.3s; }
  .download-bar .bar-label { min-height: 0.9rem; max-width: 54px; overflow: hidden; color: var(--muted); font-size: 0.6rem; text-overflow: ellipsis; white-space: nowrap; }
  .bar-peak { color: var(--muted); font-size: 0.7rem; text-align: right; margin-top: 0.25rem; }

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
  ${dailyIdNotice}

  <!-- PyPI 下载统计 -->
  <div class="section-title">&#128230; PyPI 下载统计</div>
  <div class="card pypi-card">
    <div class="metrics pypi-kpis">
      <div class="metric"><div class="value">${weekDl.toLocaleString()}</div><div class="label">近 7 天下载（PyPI API）</div></div>
      <div class="metric"><div class="value">${monthDl.toLocaleString()}</div><div class="label">近 30 天下载（PyPI API）</div></div>
      <div class="metric"><div class="value">${latestDownload ? latestDownload.downloads.toLocaleString() : '-'}</div><div class="label">最新日下载（${latestDownload ? latestDownload.date : '-'}）</div></div>
      <div class="metric"><div class="value" id="download-current-total">${defaultDownloadTotal.toLocaleString()}</div><div class="label">当前区间总下载</div></div>
    </div>
    <h2>下载趋势 <small>（默认近 30 天，可切换区间，PyPI 数据延迟 1-2 天）</small></h2>
    ${downloadRangeControls}
    ${downloadRangeRows}
  </div>

  <!-- 时段对比 -->
  <div class="section-title">&#128202; 时段数据</div>
  ${periodHtml}
  ${contractHtml}
  ${contractV11Html}
  ${contractVNextLocalHtml}

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
      <h2>活跃趋势 <small>（可切换区间）</small></h2>
      ${activityRangeControls}
      ${activityRangeRows}
    </div>
    <div class="card">
      <h2>每月汇总</h2>
      <table><thead><tr><th>月份</th><th>匿名日 ID</th><th>事件数</th></tr></thead><tbody>${monthlyRows}</tbody></table>
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
      <thead><tr><th>时间</th><th>匿名日 ID</th><th>版本</th><th>系统</th><th>工具调用</th></tr></thead>
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
      '<div class="metric highlight"><div class="period-label">反馈总数</div><div class="period-row"><span class="period-val">' + ft.total + '</span><span class="period-unit">份报告</span></div><div class="period-row"><span class="period-val">' + (ft.unique_users || 0) + '</span><span class="period-unit">匿名日 ID</span></div></div>' +
      '<div class="metric"><div class="period-label">平均知识量</div><div class="period-row"><span class="period-val">' + Math.round(ft.avg_knowledge || 0) + '</span><span class="period-unit">条知识</span></div><div class="period-row"><span class="period-val">' + Math.round(ft.avg_sessions || 0) + '</span><span class="period-unit">会话数</span></div></div>' +
      '<div class="metric"><div class="period-label">治理指标</div><div class="period-row"><span class="period-val">' + avgPR + '</span><span class="period-unit">确认率</span></div><div class="period-row"><span class="period-val">' + avgAge + '</span><span class="period-unit">staging 滞留</span></div></div>' +
      '<div class="metric"><div class="period-label">最后报告</div><div class="period-row"><span class="period-unit">' + (ft.last_feedback || '暂无') + '</span></div></div>' +
    '</div>' +
    '<div class="card" style="margin-top:1rem;margin-bottom:1.5rem"><h2>最近反馈明细</h2>' +
    '<table><thead><tr><th>时间</th><th>匿名日 ID</th><th>版本</th><th>知识(S/V)</th><th>确认率</th><th>滞留天</th><th>会话</th><th>活跃天</th><th>来源工具</th></tr></thead><tbody>' + fbRows + '</tbody></table></div>';
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
  function setRange(kind, key) {
    document.querySelectorAll('[data-' + kind + '-range]').forEach(button => {
      button.classList.toggle('active', button.getAttribute('data-' + kind + '-range') === key);
    });
    document.querySelectorAll('[data-' + kind + '-panel]').forEach(panel => {
      panel.classList.toggle('active', panel.getAttribute('data-' + kind + '-panel') === key);
    });
  }
  function setDownloadRange(key) {
    setRange('download', key);
    const panel = document.querySelector('[data-download-panel="' + key + '"]');
    const total = document.getElementById('download-current-total');
    if (panel && total) total.textContent = panel.getAttribute('data-download-total') || '-';
  }
  function setActivityRange(key) { setRange('activity', key); }
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
