"""Always-on static guard for the worker side of the daily usage counts."""

from __future__ import annotations

import re
from pathlib import Path

from piia_engram import telemetry_validation as tv
from piia_engram import usage_stats as us
from piia_engram.tool_annotations import TOOL_ANNOTATIONS

WORKER = Path(__file__).resolve().parents[1] / "worker"
MIGRATION = "migrations/20261126_usage_counts.sql"


def _read(rel: str) -> str:
    return (WORKER / rel).read_text(encoding="utf-8")


def _js_set(source: str, name: str) -> set[str]:
    body = re.search(rf"const {name} = new Set\(\[(.*?)\]\)", source, re.S)
    assert body, name
    return set(re.findall(r"'([^']*)'", body.group(1)))


def _js_list(source: str, name: str) -> list[str]:
    body = re.search(rf"const {name} = \[(.*?)\];", source, re.S)
    assert body, name
    return re.findall(r"'([^']*)'", body.group(1))


def test_the_counts_route_is_public_and_placed_before_the_login_check():
    src = _read("src/index.js")
    counts = src.index("url.pathname === '/v1/counts' && request.method === 'POST'")
    assert counts < src.index("await sessionValid(request, env)")
    # The dashboard's own read API keeps its path; the counts do not share it.
    assert "'/v1/stats' && request.method === 'POST'" not in src


def test_the_counts_handler_never_reads_ip_or_client_headers():
    src = _read("src/counts.js").lower()
    for needle in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip", "request.cf", "user-agent"):
        assert needle not in src
    assert re.findall(r"headers\.get\('([^']+)'\)", src) == ["content-length"]


def test_the_worker_tool_list_matches_the_registered_tools():
    assert _js_set(_read("src/counts.js"), "TOOL_NAMES") == set(TOOL_ANNOTATIONS) | {us.OTHER_TOOL}
    assert us.tool_names() == frozenset(TOOL_ANNOTATIONS)


def test_the_worker_accepts_exactly_the_client_vocabulary():
    src = _read("src/counts.js")
    assert set(_js_list(src, "FIELDS")) == us.FIELDS
    assert _js_set(src, "COUNT_BUCKETS") == set(us.COUNT_BUCKETS)
    assert _js_set(src, "SIZE_BUCKETS") == set(us.SIZE_BUCKETS)
    assert _js_set(src, "TIERS") == us.TIERS
    assert _js_set(src, "INSTALL_AGES") == us.INSTALL_AGES
    assert _js_set(src, "ADOPTIONS") == us.ADOPTIONS
    assert _js_set(src, "ERROR_CATEGORIES") == us.ERROR_CATEGORIES
    assert _js_set(src, "KNOWLEDGE_KEYS") == us.KNOWLEDGE_KEYS
    assert "data.schema !== 'stats/2'" in src and us.SCHEMA == "stats/2"
    assert f"MAX_COUNTS_BYTES = {us.MAX_BODY_BYTES};" in src


def test_schema_and_migration_define_the_counts_table():
    migration = _read(MIGRATION).replace("\r\n", "\n").strip()
    schema = _read("schema.sql").replace("\r\n", "\n")
    assert migration in schema
    assert re.search(r"CREATE TABLE IF NOT EXISTS counts\b", migration)
    assert "PRIMARY KEY (daily_id, day)" in migration
    columns = set(re.findall(r"^\s+(\w+)\s+TEXT NOT NULL", migration, re.M))
    assert columns == {"daily_id", "day", "version", "os", "python", "tier", "install_age",
                       "adoption", "tools", "errors", "knowledge"}
    # No install id, no address, no receive time on the row.
    for word in ("install_id", "received", "ip", "client"):
        assert word not in columns
    ok, problems = tv.validate_migration_additive(migration)
    assert ok, problems


def test_retention_stays_under_thirteen_months_for_every_table():
    counts = _read("src/counts.js")
    usage = _read("src/usage.js")
    assert re.search(r"RETENTION_DAYS = 395\b", counts)
    assert re.search(r"RETENTION_DAYS = 395\b", usage)
    for table, column in (("counts", "day"), ("events", "received"), ("feedback", "received")):
        assert f"DELETE FROM {table} WHERE {column} < ?" in counts
    index = _read("src/index.js")
    assert "purgeOldCounts(env)" in index.split("async scheduled", 1)[1]


def test_the_off_switch_answers_before_the_body_is_read():
    body = _read("src/counts.js").split("export async function handleCounts", 1)[1]
    assert body.index("COUNTS_MODE") < body.index("request.headers") < body.index("request.text()")
    assert "reply(410" in body


def test_ci_runs_the_counts_harness():
    ci = (WORKER.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "node worker/test/counts.mjs" in ci
