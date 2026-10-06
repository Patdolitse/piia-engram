/** Node harness for worker/src/session.js. Run: node worker/test/session.mjs */
import { createSession, sessionValid, deleteSession, reserveAttempt, clearAttempt, attemptLogin,
  passwordMatches, sessionCookie, purgeAuth } from '../src/session.js';
import worker from '../src/index.js';

let failures = 0;
function check(name, cond, extra = '') {
  if (!cond) failures++;
  console.log(`${cond ? 'ok  ' : 'FAIL'}  ${name}${extra ? '  — ' + extra : ''}`);
}

// A small random delay so concurrent calls really interleave between statements.
const jitter = () => new Promise((r) => setTimeout(r, Math.floor(Math.random() * 4)));

function fakeDB() {
  const sessions = new Map();
  const fails = []; // { rowid, ts }
  const counts = []; // { limit, scanned } for every login_failures count
  let nextRowid = 1;
  return {
    sessions, fails, counts,
    prepare(sql) {
      return { bind(...v) { return {
        async run() {
          await jitter();
          if (sql.startsWith('INSERT INTO dash_sessions')) sessions.set(v[0], { pw_tag: v[1], expires: v[2] });
          else if (sql.startsWith('DELETE FROM dash_sessions WHERE token_hash')) sessions.delete(v[0]);
          else if (sql.startsWith('DELETE FROM dash_sessions WHERE expires')) { for (const [k, r] of sessions) if (r.expires < v[0]) sessions.delete(k); }
          else if (sql.startsWith('DELETE FROM login_failures WHERE rowid')) { const i = fails.findIndex((f) => f.rowid === v[0]); if (i >= 0) fails.splice(i, 1); }
          else if (sql.startsWith('DELETE FROM login_failures WHERE ts')) { for (let i = fails.length - 1; i >= 0; i--) if (fails[i].ts < v[0]) fails.splice(i, 1); }
          else throw new Error('unexpected SQL ' + sql);
          return { success: true };
        },
        async first() {
          await jitter();
          if (sql.startsWith('SELECT pw_tag')) return sessions.get(v[0]) || null;
          if (sql.startsWith('INSERT INTO login_failures (ts) VALUES (?) RETURNING rowid')) {
            const row = { rowid: nextRowid++, ts: v[0] };
            fails.push(row);
            return { rowid: row.rowid };
          }
          if (sql.startsWith('SELECT COUNT(*) AS n FROM (SELECT 1 FROM login_failures WHERE ts >= ? LIMIT ?)')) {
            const scanned = Math.min(fails.filter((f) => f.ts >= v[0]).length, v[1]);
            counts.push({ limit: v[1], scanned });
            return { n: scanned };
          }
          throw new Error('unexpected SQL ' + sql);
        },
      }; } };
    },
  };
}

function reqWith(token) {
  return new Request('https://t.local/', { headers: token ? { cookie: `engram_session=${token}` } : {} });
}

function loginReq(password) {
  const form = new FormData();
  form.set('password', password);
  return new Request('https://t.local/login', { method: 'POST', body: form });
}

{
  const db = fakeDB();
  const env = { DB: db, DASH_PASSWORD: 'correct horse' };
  const token = await createSession(env);
  check('token is 64 random hex chars', /^[0-9a-f]{64}$/.test(token));
  check('only a hash of the token is stored', !db.sessions.has(token) && db.sessions.size === 1);
  check('session valid', await sessionValid(reqWith(token), env));
  check('no cookie → invalid', !(await sessionValid(reqWith(''), env)));
  check('wrong token → invalid', !(await sessionValid(reqWith('b'.repeat(64)), env)));
  check('password change ends the session', !(await sessionValid(reqWith(token), { ...env, DASH_PASSWORD: 'new pass' })));
  await deleteSession(reqWith(token), env);
  check('logout deletes the session', !(await sessionValid(reqWith(token), env)));
  check('cookie flags', /HttpOnly; Secure; SameSite=Strict/.test(sessionCookie(token, 60)));
}
{
  const db = fakeDB();
  const env = { DB: db, DASH_PASSWORD: 'p' };
  const token = await createSession(env);
  for (const row of db.sessions.values()) row.expires = 1;
  check('expired session invalid', !(await sessionValid(reqWith(token), env)));
  await purgeAuth(env);
  check('purge removes expired sessions', db.sessions.size === 0);
}
{
  const db = fakeDB();
  const env = { DB: db };
  const first = await reserveAttempt(env);
  check('reserveAttempt returns a rowid and is not locked', Number.isInteger(first.rowid) && first.locked === false);
  await clearAttempt(env, first.rowid);
  check('clearAttempt removes the reserved row', db.fails.length === 0);
  for (let i = 0; i < 10; i++) await reserveAttempt(env);
  check('10 kept attempts: the 11th is locked', (await reserveAttempt(env)).locked === true);
}
{
  // Sequential: 9 wrong passwords, then the right one still gets in and leaves no row behind.
  const db = fakeDB();
  const env = { DB: db, DASH_PASSWORD: 'right' };
  for (let i = 0; i < 9; i++) await attemptLogin(env, 'wrong');
  const res = await attemptLogin(env, 'right');
  check('correct password before lockout → session', res.status === 'ok' && /^[0-9a-f]{64}$/.test(res.token)
    && db.sessions.size === 1);
  check('successful login leaves no reserved row', db.fails.length === 9);
}
{
  // Concurrent: 20 wrong passwords at once compare the password at most 10 times.
  const db = fakeDB();
  const env = { DB: db, DASH_PASSWORD: 'right' };
  let compared = 0;
  const counting = async (given, expected) => { compared++; return passwordMatches(given, expected); };
  const results = await Promise.all(Array.from({ length: 20 }, () => attemptLogin(env, 'wrong', counting)));
  check('20 concurrent wrong attempts: at most 10 password comparisons', compared <= 10, `compared=${compared}`);
  check('20 concurrent wrong attempts: the rest are locked', results.filter((r) => r.status === 'locked').length >= 10);
  check('rows bounded by the attempts that got past the pre-check', db.fails.length >= 10 && db.fails.length <= 20,
    `rows=${db.fails.length}`);
  const after = await attemptLogin(env, 'right', counting);
  check('correct password after lockout → locked, not compared', after.status === 'locked' && compared <= 10
    && db.sessions.size === 0);
}
{
  // Staggered starts: some attempts get compared, never more than 10.
  const db = fakeDB();
  const env = { DB: db, DASH_PASSWORD: 'right' };
  let compared = 0;
  const counting = async (given, expected) => { compared++; return passwordMatches(given, expected); };
  await Promise.all(Array.from({ length: 20 }, (_, i) =>
    new Promise((r) => setTimeout(r, i * 2)).then(() => attemptLogin(env, 'wrong', counting))));
  check('20 staggered wrong attempts: at most 10 password comparisons', compared <= 10, `compared=${compared}`);
  check('then the correct password is locked', (await attemptLogin(env, 'right', counting)).status === 'locked');
}
{
  // Sustained probing after a lockout: no new rows, every count reads at most 11 rows.
  const db = fakeDB();
  const env = { DB: db, DASH_PASSWORD: 'right' };
  for (let i = 0; i < 10; i++) await attemptLogin(env, 'wrong');
  const rows = db.fails.length;
  db.counts.length = 0;
  let compared = 0;
  const counting = async (given, expected) => { compared++; return passwordMatches(given, expected); };
  for (let i = 0; i < 50; i++) await attemptLogin(env, 'wrong', counting);
  await Promise.all(Array.from({ length: 50 }, () => attemptLogin(env, 'right', counting)));
  check('100 attempts after lockout: no new rows, no comparisons', db.fails.length === rows && compared === 0,
    `rows ${rows} -> ${db.fails.length}, compared=${compared}`);
  check('every count is capped at 11 rows', db.counts.length >= 100
    && db.counts.every((c) => c.limit === 11 && c.scanned <= 11), `counts=${db.counts.length}`);
}
{
  // Same through the real router.
  const db = fakeDB();
  const env = { DB: db, DASH_PASSWORD: 'right' };
  const responses = await Promise.all(Array.from({ length: 20 }, () => worker.fetch(loginReq('wrong'), env)));
  const wrong = responses.filter((r) => r.status === 401).length;
  const locked = responses.filter((r) => r.status === 429).length;
  check('router: 20 concurrent wrong logins → at most 10 checked, rest 429', wrong <= 10 && wrong + locked === 20,
    `401=${wrong} 429=${locked}`);
  const after = await worker.fetch(loginReq('right'), env);
  check('router: correct password after lockout → 429', after.status === 429 && !after.headers.get('set-cookie'));
}
{
  const db = fakeDB();
  const env = { DB: db, DASH_PASSWORD: 'right' };
  const res = await worker.fetch(loginReq('right'), env);
  check('router: correct password → session cookie, no leftover row',
    res.status === 302 && /engram_session=[0-9a-f]{64};/.test(res.headers.get('set-cookie') || '')
    && db.fails.length === 0 && db.sessions.size === 1);
}
check('passwordMatches true', await passwordMatches('abc', 'abc'));
check('passwordMatches false', !(await passwordMatches('abc', 'abd')));

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log('\nall session checks passed');
