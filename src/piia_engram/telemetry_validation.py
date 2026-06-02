"""Local telemetry contract validation (Phase 13) — static checks, no network.

Verifies that the three sides of the telemetry contract stay consistent *without
any remote action*:

- the client **payload** (``telemetry.build_payload``),
- the worker **schema** (``worker/schema.sql``),
- the **migrations** (``worker/migrations/*.sql``).

It also enforces the privacy boundary statically: neither the declared payload
contract nor the worker schema may contain a content-bearing field (summary,
detail, choice, body, …), and the v1.1 migration must be **additive / forward
only** (ALTER ADD COLUMN / CREATE INDEX — no DROP/DELETE/UPDATE/rewrite).

Pure: reads the ``.sql`` files and a declared contract constant. No network, no
D1, no deploy. Intended to back tests and a local pre-deploy check.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# The v1.1 client payload contract: every key ``build_payload`` may emit, mapped
# to the worker ``events`` column it lands in. Transport-only keys (used in
# transit but not stored as a same-named column) are listed separately.
PAYLOAD_TO_COLUMN: dict[str, str] = {
    "schema": "schema_v",
    "daily_id": "daily_id",
    "engram_version": "version",
    "prev_version": "prev_version",
    "session_type": "session_type",
    "install_age_bucket": "install_age_bucket",
    "error_categories": "error_categories",
    "contract_version": "contract_version",
    "version_adoption": "version_adoption",
    "activation_state": "activation_state",
    "returning_bucket": "returning_bucket",
    "error_trend": "error_trend",
    "os_platform": "os",
    "python_version": "py",
    "tools_tier": "tier",
    "tool_calls": "tool_calls",
    "knowledge_counts": "knowledge",
}
# Emitted in transit but intentionally not stored under a same-named column
# (the worker stamps its own ``received`` timestamp).
TRANSPORT_ONLY_KEYS = frozenset({"timestamp"})

# The five derived buckets added by the v1.1 contract (must exist in schema +
# the v1.1 migration).
V1_1_DERIVED_COLUMNS = frozenset({
    "contract_version", "version_adoption", "activation_state",
    "returning_bucket", "error_trend",
})

# Field-name fragments that would indicate stored *content* (not metadata). The
# telemetry boundary is metadata-only, so none of these may appear as a payload
# key or a schema column name.
CONTENT_FIELD_MARKERS = (
    "summary", "detail", "choice", "question", "reasoning", "body",
    "content", "prompt", "text", "title", "message", "note",
)


def _events_block(schema_sql: str) -> str:
    m = re.search(r"CREATE TABLE[^(]*\bevents\b[^(]*\((.*?)\)\s*;", schema_sql,
                  re.IGNORECASE | re.DOTALL)
    return m.group(1) if m else ""


def parse_schema_columns(schema_sql: str, table: str = "events") -> set[str]:
    """Extract column names from a ``CREATE TABLE <table> (...)`` block."""
    if table == "events":
        block = _events_block(schema_sql)
    else:
        m = re.search(rf"CREATE TABLE[^(]*\b{re.escape(table)}\b[^(]*\((.*?)\)\s*;",
                      schema_sql, re.IGNORECASE | re.DOTALL)
        block = m.group(1) if m else ""
    columns: set[str] = set()
    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        # Skip table-level constraints.
        if re.match(r"(PRIMARY|FOREIGN|UNIQUE|CHECK|CONSTRAINT)\b", line, re.IGNORECASE):
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", line)
        if m:
            columns.add(m.group(1))
    return columns


def parse_added_columns(migration_sql: str) -> set[str]:
    """Extract column names from ``ALTER TABLE ... ADD COLUMN <name>`` statements."""
    return set(re.findall(r"ADD\s+COLUMN\s+([A-Za-z_][A-Za-z0-9_]*)",
                          migration_sql, re.IGNORECASE))


def _strip_sql_comments(sql: str) -> str:
    """Drop ``--`` line comments so a comment like '-- update index' never trips
    the destructive-statement scan."""
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def validate_migration_additive(migration_sql: str) -> tuple[bool, list[str]]:
    """Confirm a migration is additive / forward-only (no destructive statements).

    Comments are stripped first, and ``UPDATE`` is matched only as a statement
    head (start-of-statement), so a legitimate additive ``ON CONFLICT DO UPDATE``
    upsert is not misflagged.
    """
    sql = _strip_sql_comments(migration_sql)
    problems: list[str] = []
    forbidden = (
        (r"\bDROP\s+TABLE\b", "DROP TABLE"),
        (r"\bDROP\s+COLUMN\b", "DROP COLUMN"),
        (r"\bDELETE\s+FROM\b", "DELETE FROM"),
        # UPDATE only as a statement head (after ';' or start), not 'DO UPDATE'.
        (r"(?:^|;)\s*UPDATE\s+\w", "UPDATE"),
        (r"\bTRUNCATE\b", "TRUNCATE"),
        (r"\bALTER\s+COLUMN\b", "ALTER COLUMN"),
    )
    for pattern, label in forbidden:
        if re.search(pattern, sql, re.IGNORECASE | re.MULTILINE):
            problems.append(f"non-additive statement: {label}")
    return (not problems), problems


def _content_markers_in(names: set[str]) -> list[str]:
    hits: list[str] = []
    for name in names:
        low = name.lower()
        for marker in CONTENT_FIELD_MARKERS:
            if marker in low:
                hits.append(name)
                break
    return hits


def validate_telemetry_contract(worker_dir: str | Path) -> dict[str, Any]:
    """Run the full static telemetry-contract consistency check.

    Returns a report dict with ``ok`` plus per-check detail and a ``problems``
    list. Never raises on a missing file (it records the absence as a problem).
    """
    wdir = Path(worker_dir).expanduser().resolve()
    problems: list[str] = []

    schema_path = wdir / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8") if schema_path.is_file() else ""
    if not schema_sql:
        problems.append("missing worker/schema.sql")
    events_columns = parse_schema_columns(schema_sql) if schema_sql else set()

    # 1. Every storable payload field maps to an existing events column.
    payload_unmapped = []
    for key, column in PAYLOAD_TO_COLUMN.items():
        if column not in events_columns:
            payload_unmapped.append(f"{key} -> {column}")
    if payload_unmapped:
        problems.append(f"payload fields with no schema column: {payload_unmapped}")

    # 2. The v1.1 derived buckets exist in the schema.
    missing_v1_1 = sorted(V1_1_DERIVED_COLUMNS - events_columns)
    if missing_v1_1:
        problems.append(f"v1.1 derived columns missing from schema: {missing_v1_1}")

    # 3. The v1.1 migration adds exactly the derived buckets and is additive.
    mig_v1_1 = wdir / "migrations" / "20260603_telemetry_contract_v1_1.sql"
    migration_added: set[str] = set()
    if mig_v1_1.is_file():
        mig_sql = mig_v1_1.read_text(encoding="utf-8")
        migration_added = parse_added_columns(mig_sql)
        if not V1_1_DERIVED_COLUMNS.issubset(migration_added):
            problems.append(
                f"v1.1 migration missing ADD COLUMN for: "
                f"{sorted(V1_1_DERIVED_COLUMNS - migration_added)}")
        additive_ok, additive_problems = validate_migration_additive(mig_sql)
        if not additive_ok:
            problems.append(f"v1.1 migration not additive: {additive_problems}")
    else:
        problems.append("missing worker/migrations/20260603_telemetry_contract_v1_1.sql")

    # 4. Privacy boundary: no content-bearing field in payload or schema.
    payload_content = _content_markers_in(set(PAYLOAD_TO_COLUMN))
    schema_content = _content_markers_in(events_columns)
    if payload_content:
        problems.append(f"content-bearing payload field(s): {payload_content}")
    if schema_content:
        problems.append(f"content-bearing schema column(s): {schema_content}")

    return {
        "worker_dir": str(wdir),
        "events_columns": sorted(events_columns),
        "v1_1_migration_added": sorted(migration_added),
        "payload_fields": sorted(PAYLOAD_TO_COLUMN),
        "problems": problems,
        "ok": not problems,
    }


def render_validation_text(report: dict[str, Any]) -> str:
    status = "OK" if report.get("ok") else f"{len(report.get('problems', []))} problem(s)"
    lines = [f"Telemetry contract validation: {status}",
             f"  events columns: {len(report.get('events_columns', []))}",
             f"  payload fields: {len(report.get('payload_fields', []))}"]
    for p in report.get("problems", []):
        lines.append(f"  - {p}")
    return "\n".join(lines)
