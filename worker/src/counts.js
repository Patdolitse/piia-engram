/**
 * Daily usage counts: POST /v1/counts and retention.
 * One row per daily rotating ID and UTC day. Every value is a closed-set label or
 * a count bucket; the row carries no install ID. Reads only the request body and
 * its length; never reads or stores IP addresses or other request headers.
 * The announced length is required and checked first, and the body is read in
 * pieces that stop at the size limit.
 */

import { dayOffset } from './usage.js';

const JSON_HEADERS = { 'Content-Type': 'application/json' };
export const MAX_COUNTS_BYTES = 8192;
export const RETENTION_DAYS = 395; // under 13 months
const FIELDS = ['schema', 'daily_id', 'date', 'version', 'os', 'python', 'tier',
  'install_age', 'adoption', 'tools', 'errors', 'knowledge'];
const STRING_FIELDS = FIELDS.slice(0, 9);
const OS_VALUES = new Set(['windows', 'macos', 'linux', 'other']);
const TIERS = new Set(['core', 'all', 'other']);
const INSTALL_AGES = new Set(['first_day', '2_7_days', '8_30_days', '31_plus_days', 'unknown']);
const ADOPTIONS = new Set(['first', 'same', 'upgrade', 'downgrade', 'changed']);
const COUNT_BUCKETS = new Set(['0', '1_4', '5_9', '10_24', '25_99', '100_plus']);
const SIZE_BUCKETS = new Set(['0', '1_9', '10_49', '50_199', '200_999', '1000_plus']);
const ERROR_CATEGORIES = new Set(['timeout', 'validation', 'io', 'permission', 'network', 'unknown']);
const KNOWLEDGE_KEYS = new Set(['lessons', 'decisions', 'domains']);
// The registered MCP tool names (kept in step with the client by a test), plus "other".
export const TOOL_NAMES = new Set([
  'add_decision', 'add_lesson', 'add_playbook', 'archive_knowledge', 'check_anchors',
  'confirm_knowledge', 'doctor', 'explore_knowledge', 'export_engram', 'export_feedback_report',
  'export_knowledge_report', 'extract_session_insights', 'find_tool', 'get_audit_log',
  'get_daily_log', 'get_decisions', 'get_identity_card', 'get_identity_facets',
  'get_knowledge_history', 'get_knowledge_inheritance', 'get_knowledge_overview', 'get_lessons',
  'get_permission_profile', 'get_playbooks', 'get_project_context', 'get_recall',
  'get_recent_context', 'get_relevant_knowledge', 'get_resume_brief', 'get_stale_knowledge',
  'get_user_context', 'get_wrap_up_session_status', 'import_engram', 'ingest_notes',
  'list_agent_sessions', 'list_projects', 'list_tools', 'manage_caller_trust', 'manage_playbook',
  'manage_relation', 'memory_store', 'merge_knowledge', 'onboard_accept', 'onboard_repo',
  'playbook_execution', 'preview_context_governance', 'read_web_content', 'refresh_quick_context',
  'register_tool', 'request_outline_review', 'review_staging', 'save_agent_context',
  'save_project_snapshot', 'search_knowledge', 'start_project', 'update_identity', 'update_knowledge',
  'user_portrait', 'wrap_up_session',
  'other',
]);

function utcToday() {
  return new Date().toISOString().slice(0, 10);
}

const plainObject = (v) => !!v && typeof v === 'object' && !Array.isArray(v);

function bucketMap(value, keys, buckets) {
  return plainObject(value)
    && Object.entries(value).every(([k, v]) => keys.has(k) && typeof v === 'string' && buckets.has(v));
}

export function validateCounts(data, today = utcToday()) {
  if (!plainObject(data)) return 'not an object';
  if (Object.keys(data).sort().join(',') !== [...FIELDS].sort().join(',')) return 'unexpected fields';
  if (!STRING_FIELDS.every((k) => typeof data[k] === 'string')) return 'non-string field';
  if (data.schema !== 'stats/2') return 'schema';
  if (!/^[0-9a-f]{16}$/.test(data.daily_id)) return 'daily_id';
  if (!/^\d{4}-\d{2}-\d{2}$/.test(data.date)) return 'date';
  if (data.date < dayOffset(today, -8) || data.date > dayOffset(today, 1)) return 'date range';
  if (!/^\d+\.\d+\.\d+[0-9A-Za-z.+-]{0,16}$/.test(data.version)) return 'version';
  if (!OS_VALUES.has(data.os)) return 'os';
  if (!/^3\.\d{1,2}$/.test(data.python)) return 'python';
  if (!TIERS.has(data.tier)) return 'tier';
  if (!INSTALL_AGES.has(data.install_age)) return 'install_age';
  if (!ADOPTIONS.has(data.adoption)) return 'adoption';
  if (!plainObject(data.tools) || Object.keys(data.tools).length === 0) return 'tools';
  for (const [name, pair] of Object.entries(data.tools)) {
    if (!TOOL_NAMES.has(name)) return 'tool name';
    if (!plainObject(pair) || Object.keys(pair).sort().join(',') !== 'err,ok') return 'tool counts';
    if (!COUNT_BUCKETS.has(pair.ok) || !COUNT_BUCKETS.has(pair.err)) return 'tool bucket';
  }
  if (!bucketMap(data.errors, ERROR_CATEGORIES, COUNT_BUCKETS)) return 'errors';
  if (!bucketMap(data.knowledge, KNOWLEDGE_KEYS, SIZE_BUCKETS)) return 'knowledge';
  return '';
}

function reply(status, body) {
  return new Response(body === null ? null : JSON.stringify(body), { status, headers: JSON_HEADERS });
}

/** The body as text, or null once it grows past `limit` bytes (reading stops there). */
async function readCapped(request, limit) {
  if (!request.body) return '';
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > limit) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(bytes);
}

export async function handleCounts(request, env) {
  // Operator switch: answer 410 without reading the body; clients then drop what they held.
  if (String(env.COUNTS_MODE || '').toLowerCase() === 'off') return reply(410, { error: 'gone' });
  const announced = request.headers.get('content-length');
  if (announced === null || !/^\d+$/.test(announced) || Number(announced) > MAX_COUNTS_BYTES) {
    return reply(413, { error: 'too large' });
  }
  const text = await readCapped(request, MAX_COUNTS_BYTES);
  if (text === null) return reply(413, { error: 'too large' });
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return reply(400, { error: 'invalid JSON' });
  }
  const problem = validateCounts(data);
  if (problem) return reply(422, { error: problem });
  await env.DB.prepare(
    `INSERT OR IGNORE INTO counts (daily_id, day, version, os, python, tier, install_age, adoption,
       tools, errors, knowledge) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  ).bind(data.daily_id, data.date, data.version, data.os, data.python, data.tier, data.install_age,
    data.adoption, JSON.stringify(data.tools), JSON.stringify(data.errors),
    JSON.stringify(data.knowledge)).run();
  return reply(204, null);
}

/** Daily retention for the counts and for the two older detail tables. */
export async function purgeOldCounts(env, today = utcToday()) {
  const cutoff = dayOffset(today, -RETENTION_DAYS);
  await env.DB.prepare('DELETE FROM counts WHERE day < ?').bind(cutoff).run();
  await env.DB.prepare('DELETE FROM events WHERE received < ?').bind(cutoff).run();
  await env.DB.prepare('DELETE FROM feedback WHERE received < ?').bind(cutoff).run();
}
