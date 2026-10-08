"""Always-on static guard for the worker side of the daily usage ping."""

from __future__ import annotations

import re
from pathlib import Path

from piia_engram import telemetry_validation as tv

WORKER = Path(__file__).resolve().parents[1] / "worker"


def _read(rel: str) -> str:
    return (WORKER / rel).read_text(encoding="utf-8")


def test_the_ping_route_is_public_and_the_usage_page_is_behind_login():
    src = _read("src/index.js")
    ping = src.index("url.pathname === '/v1/ping'")
    health = src.index("url.pathname === '/v1/health'")
    auth = src.index("await sessionValid(request, env)")
    assert ping < auth and health < auth
    for route in ("'/'", "'/usage'", "'/details'", "'/v1/usage'", "'/v1/stats'"):
        assert auth < src.index(f"url.pathname === {route}"), route


def _route_block(src: str, route: str) -> str:
    """The source of the first `if (...)` route block that mentions this path."""
    start = src.index(f"url.pathname === {route}")
    end = src.find("\n    if (url.pathname", start)
    return src[start:end if end != -1 else len(src)]


def test_home_is_the_usage_page_and_the_old_dashboard_moved_to_details():
    src = _read("src/index.js")
    home = _route_block(src, "'/'")
    assert "renderUsage(" in home and "renderDashboard(" not in home
    assert "'/usage'" in home and "'/v1/usage'" in home
    assert "url.searchParams.get('days')" in home and "fetchPypiStats" in home
    details = _route_block(src, "'/details'")
    assert "renderDashboard(" in details and "getStatsData(env)" in details
    stats = _route_block(src, "'/v1/stats'")
    assert "getStatsData(env)" in stats and "renderUsage(" not in stats
    assert "'Location': '/'" in src  # a successful login still lands on the home page


def test_ci_sets_up_node_before_the_worker_tests():
    ci = (WORKER.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci[ci.index("  canonical-count:"):ci.index("\n  test:\n")]
    node = job.index("uses: actions/setup-node@")
    assert re.search(r"node-version:\s*['\"]?22['\"]?\s*$", job[node:job.index("- name: Worker tests")], re.M)
    assert node < job.index("node worker/test/smoke.mjs")


def test_the_ping_handler_never_reads_ip_or_client_headers():
    src = _read("src/usage.js").lower()
    for needle in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip", "request.cf", "user-agent"):
        assert needle not in src


def test_schema_and_migration_define_the_new_tables():
    for rel in ("schema.sql", "migrations/20261006_usage_ping.sql"):
        sql = _read(rel)
        for table in ("pings", "dash_sessions", "login_failures"):
            assert re.search(rf"CREATE TABLE IF NOT EXISTS {table}\b", sql), (rel, table)
        assert "PRIMARY KEY (install_id, day)" in sql


def test_pings_have_a_covering_day_index_and_the_migration_matches_the_schema():
    migration = _read("migrations/20261006_usage_ping.sql").replace("\r\n", "\n").strip()
    schema = _read("schema.sql").replace("\r\n", "\n")
    assert migration in schema
    for sql in (migration, schema):
        assert "ON pings(day, install_id)" in sql
        assert "idx_pings_day ON pings(day)" not in sql


def test_the_migration_is_additive():
    ok, problems = tv.validate_migration_additive(_read("migrations/20261006_usage_ping.sql"))
    assert ok, problems


def test_a_daily_cron_runs_retention():
    assert re.search(r'^\[triggers\]\s*\ncrons\s*=\s*\["17 3 \* \* \*"\]', _read("wrangler.toml"), re.M)
    src = _read("src/index.js")
    assert "async scheduled(" in src and "purgeOldPings" in src and "purgeAuth" in src


def test_the_password_derived_cookie_is_gone():
    src = _read("src/index.js")
    assert "hashPassword" not in src and "'engram-session'" not in src


# --- the worker and the client agree on the payload ---------------------------


def _js_strings(src: str, name: str) -> set[str]:
    m = re.search(rf"const {name} = (?:new Set\()?\[(.*?)\]", src, re.S)
    assert m, name
    return set(re.findall(r"'([^']*)'", m.group(1)))


def _js_regex(src: str, field: str) -> str:
    m = re.search(rf"if \(!/(.+?)/\.test\(data\.{field}\)\) return '{field}';", src)
    assert m, field
    return m.group(1)


def test_the_worker_accepts_exactly_what_the_client_sends():
    from piia_engram import usage_ping as up

    src = _read("src/usage.js")
    assert _js_strings(src, "FIELDS") == set(up._FIELDS)
    assert _js_strings(src, "OS_VALUES") == set(up._OS_VALUES)
    assert _js_strings(src, "CLIENTS") == set(up.CLIENTS)
    assert f"data.schema !== '{up.SCHEMA}'" in src


def test_the_worker_and_the_client_use_the_same_field_formats():
    from piia_engram import usage_ping as up

    src = _read("src/usage.js")
    for field, pattern in (("install_id", up._ID_RE), ("version", up._VERSION_RE),
                           ("python", up._PYTHON_RE), ("date", up._DATE_RE)):
        js = _js_regex(src, field)
        assert js.startswith("^") and js.endswith("$"), field
        assert js[1:-1] == pattern.pattern, field
