/**
 * Dashboard sessions: a random token per login (only its hash is stored),
 * bound to the current DASH_PASSWORD so a password change ends every session.
 * The stored tag mixes in the token itself, so the table alone cannot be used
 * to test password guesses.
 * Login lockout: after 10 unsuccessful attempts within 10 minutes, further attempts
 * are refused without checking the password. Every attempt reserves a row before
 * it is checked, so concurrent requests cannot slip past the limit; only a
 * successful login removes its own row. While locked, attempts are refused by a
 * capped count before any write, so sustained probing costs at most 11 rows read
 * per request and no writes; the lockout ends 10 minutes after the last counted
 * attempt.
 */

const COOKIE_NAME = 'engram_session';
export const SESSION_MAX_AGE = 86400 * 7;
const MAX_FAILURES = 10;
const FAILURE_WINDOW_S = 600;

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function nowS() {
  return Math.floor(Date.now() / 1000);
}

async function pwTag(token, password) {
  return sha256Hex(`engram-pw-tag:${token}:${password}`);
}

export async function passwordMatches(given, expected) {
  return (await sha256Hex(`cmp:${given}`)) === (await sha256Hex(`cmp:${expected}`));
}

function readToken(request) {
  const cookie = request.headers.get('cookie') || '';
  const match = cookie.match(/(?:^|;\s*)engram_session=([0-9a-f]{64})(?:;|$)/);
  return match ? match[1] : '';
}

export function sessionCookie(token, maxAge) {
  return `${COOKIE_NAME}=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${maxAge}`;
}

export async function createSession(env) {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  const token = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
  await env.DB.prepare('INSERT INTO dash_sessions (token_hash, pw_tag, expires) VALUES (?, ?, ?)')
    .bind(await sha256Hex(token), await pwTag(token, env.DASH_PASSWORD), nowS() + SESSION_MAX_AGE).run();
  return token;
}

export async function sessionValid(request, env) {
  if (!env.DASH_PASSWORD) return true; // unchanged: no password configured = open dashboard
  const token = readToken(request);
  if (!token) return false;
  const row = await env.DB.prepare('SELECT pw_tag, expires FROM dash_sessions WHERE token_hash = ?')
    .bind(await sha256Hex(token)).first();
  if (!row || Number(row.expires) < nowS()) return false;
  return row.pw_tag === (await pwTag(token, env.DASH_PASSWORD));
}

export async function deleteSession(request, env) {
  const token = readToken(request);
  if (!token) return;
  await env.DB.prepare('DELETE FROM dash_sessions WHERE token_hash = ?').bind(await sha256Hex(token)).run();
}

/** Rows in the window, read at most MAX_FAILURES + 1 of them. */
async function windowCount(env) {
  const row = await env.DB.prepare(
    'SELECT COUNT(*) AS n FROM (SELECT 1 FROM login_failures WHERE ts >= ? LIMIT ?)',
  ).bind(nowS() - FAILURE_WINDOW_S, MAX_FAILURES + 1).first();
  return Number(row?.n || 0);
}

/** Reserve a row for this attempt, then count the window including it. */
export async function reserveAttempt(env) {
  const row = await env.DB.prepare('INSERT INTO login_failures (ts) VALUES (?) RETURNING rowid')
    .bind(nowS()).first();
  return { rowid: row?.rowid ?? null, locked: (await windowCount(env)) > MAX_FAILURES };
}

export async function clearAttempt(env, rowid) {
  if (rowid === null || rowid === undefined) return;
  await env.DB.prepare('DELETE FROM login_failures WHERE rowid = ?').bind(rowid).run();
}

/**
 * One login attempt: { status: 'locked' } (password not compared),
 * { status: 'ok', token } or { status: 'wrong' }. Only a successful login removes
 * its reserved row, so a burst of attempts keeps the login locked.
 */
export async function attemptLogin(env, password, compare = passwordMatches) {
  // Cheap pre-check: while locked, refuse without writing anything.
  if ((await windowCount(env)) >= MAX_FAILURES) return { status: 'locked' };
  // The decision itself stays reserve-then-check, so concurrency cannot slip past.
  const attempt = await reserveAttempt(env);
  if (attempt.locked) return { status: 'locked' }; // keeps its row; password never compared
  if (await compare(password, env.DASH_PASSWORD)) {
    await clearAttempt(env, attempt.rowid);
    return { status: 'ok', token: await createSession(env) };
  }
  return { status: 'wrong' };
}

export async function purgeAuth(env) {
  await env.DB.prepare('DELETE FROM dash_sessions WHERE expires < ?').bind(nowS()).run();
  await env.DB.prepare('DELETE FROM login_failures WHERE ts < ?').bind(nowS() - FAILURE_WINDOW_S).run();
}
