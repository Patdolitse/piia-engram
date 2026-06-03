"""Local telemetry contract validation (Phase 13) — static checks, no network.

Verifies that the three sides of the telemetry contract stay consistent *without
any remote action*:

- the client **payload** (``telemetry.build_payload``),
- the worker **schema** (``worker/schema.sql``),
- the **migrations** (``worker/migrations/*.sql``).

It also enforces the privacy boundary statically: neither the declared payload
contract, the worker schema, the worker field allowlists, nor any migration's
added columns may contain a content-bearing field (summary, detail, choice,
body, …), and the v1.1 migration must be **additive / forward only** (ALTER ADD
COLUMN / CREATE INDEX — no DROP/DELETE/UPDATE/rewrite).

This module also hosts the **feedback-report send-boundary guard**
(:func:`validate_feedback_report`): an allowlist + content check enforced
*before* a feedback report is serialized for the network, independent of how the
report was built. The remote worker's feedback validator is deliberately relaxed
(it persists the whole payload in ``raw_json``), so this client-side allowlist is
the real privacy gate.

Pure: reads the ``.sql`` / ``.js`` files and declared contract constants. No
network, no D1, no deploy. Intended to back tests and a local pre-deploy check.
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

# The four P0 metadata columns added by the v1 contract. The v1.1 migration must
# NOT re-add these (proper sequencing: v1 lands them, v1.1 only adds the derived
# buckets), so the validator can confirm "apply v1 before v1.1" statically.
V1_P0_COLUMNS = frozenset({
    "prev_version", "session_type", "install_age_bucket", "error_categories",
})

# Migration filenames (relative to ``worker/migrations``). Order matters: v1
# before v1.1.
V1_MIGRATION_NAME = "20260603_telemetry_contract_v1.sql"
V1_1_MIGRATION_NAME = "20260603_telemetry_contract_v1_1.sql"

# Dashboard wording the worker MUST keep (anonymous daily-id language) and MUST
# NOT use (unique-person claims) anywhere a daily_id is being counted. The
# remote-readiness check and the worker-contract tests both reference these so a
# single edit can't silently reintroduce a "unique users" claim.
DASHBOARD_REQUIRED_WORDING = ("匿名日 ID", "daily_id 按 UTC 日期轮换")
DASHBOARD_FORBIDDEN_WORDING = ("独立用户", "用户数")
# The v1.1 analysis tiles (all anonymous-daily-id based). Labels are chosen to be
# distinct from the v1 "首跑激活" session-type tile so the two contracts don't
# blur together on the dashboard.
DASHBOARD_V1_1_LABELS = ("版本采纳", "知识激活", "匿名回访分桶", "错误趋势")

# Field-name fragments that would indicate stored *content* (not metadata). The
# telemetry boundary is metadata-only, so none of these may appear as a payload
# key or a schema column name.
CONTENT_FIELD_MARKERS = (
    "summary", "detail", "choice", "question", "reasoning", "body",
    "content", "prompt", "text", "title", "message", "note",
    # broadened so a plausibly-named content column can't slip past the lint
    "input", "raw", "comment", "descr", "verbatim", "transcript",
    "rationale", "excerpt", "snippet", "quote", "caption", "memo",
    "remark", "answer",
)

# Canonical feedback-report field allowlist. MUST mirror the worker's
# ``FEEDBACK_ALLOWED_FIELDS`` (worker/src/index.js); a drift between the two is
# flagged by :func:`validate_telemetry_contract`. The worker's feedback validator
# is deliberately *relaxed* (it stores the whole payload in ``raw_json``), so this
# client-side allowlist — enforced by :func:`validate_feedback_report` at the send
# boundary — is the real privacy gate.
FEEDBACK_ALLOWED_KEYS = frozenset({
    "report_type", "report_version", "generated_at", "daily_id",
    "engram_version", "os", "python",
    "knowledge", "top_domains", "source_tools",
    "first_knowledge_date", "days_with_knowledge", "avg_staging_age_days",
    "session_count", "top_mcp_tools", "configured_tools", "beta_events",
})


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
    """Extract column names from ``ALTER TABLE ... ADD COLUMN <name>`` statements.

    Comments are stripped first so prose like ``-- ADD COLUMN is not idempotent``
    can't inject a phantom column name (e.g. ``is``)."""
    sql = _strip_sql_comments(migration_sql)
    return set(re.findall(r"ADD\s+COLUMN\s+([A-Za-z_][A-Za-z0-9_]*)",
                          sql, re.IGNORECASE))


def parse_js_string_set(js_source: str, var_name: str) -> set[str]:
    """Extract the string members of a ``const <var_name> = new Set([...])`` literal.

    Used to read the worker's field allowlists (``ALLOWED_FIELDS`` /
    ``FEEDBACK_ALLOWED_FIELDS``) so the local guard can detect drift against the
    Python-side contract without executing any JS. Returns an empty set if the
    declaration is absent.
    """
    m = re.search(rf"{re.escape(var_name)}\s*=\s*new\s+Set\(\[(.*?)\]\)",
                  js_source, re.DOTALL)
    if not m:
        return set()
    return set(re.findall(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", m.group(1)))


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

    # 5. No content-bearing column planted in *any* migration's ADD COLUMNs.
    mig_dir = wdir / "migrations"
    if mig_dir.is_dir():
        for mig in sorted(mig_dir.glob("*.sql")):
            try:
                added = parse_added_columns(mig.read_text(encoding="utf-8"))
            except OSError:
                continue
            mig_content = _content_markers_in(added)
            if mig_content:
                problems.append(
                    f"content-bearing column in migration {mig.name}: {sorted(set(mig_content))}")

    # 6. Worker field allowlists: must stay aligned with the client contract and
    #    carry no content field. The worker code is *read only* here — never run.
    index_path = wdir / "src" / "index.js"
    worker_js = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""
    worker_event_allow: set[str] = set()
    worker_feedback_allow: set[str] = set()
    if not worker_js:
        problems.append("missing worker/src/index.js")
    else:
        worker_event_allow = parse_js_string_set(worker_js, "ALLOWED_FIELDS")
        worker_feedback_allow = parse_js_string_set(worker_js, "FEEDBACK_ALLOWED_FIELDS")
        expected_event = set(PAYLOAD_TO_COLUMN) | TRANSPORT_ONLY_KEYS
        ev_missing = sorted(expected_event - worker_event_allow)
        ev_extra = sorted(worker_event_allow - expected_event)
        if ev_missing or ev_extra:
            problems.append(
                f"worker event allowlist drift: missing={ev_missing} extra={ev_extra}")
        fb_missing = sorted(FEEDBACK_ALLOWED_KEYS - worker_feedback_allow)
        fb_extra = sorted(worker_feedback_allow - FEEDBACK_ALLOWED_KEYS)
        if fb_missing or fb_extra:
            problems.append(
                f"feedback allowlist drift: missing={fb_missing} extra={fb_extra}")
        worker_content = _content_markers_in(worker_event_allow | worker_feedback_allow)
        if worker_content:
            problems.append(f"content-bearing worker allowlist field(s): {sorted(set(worker_content))}")

    return {
        "worker_dir": str(wdir),
        "events_columns": sorted(events_columns),
        "v1_1_migration_added": sorted(migration_added),
        "payload_fields": sorted(PAYLOAD_TO_COLUMN),
        "worker_event_allowlist": sorted(worker_event_allow),
        "worker_feedback_allowlist": sorted(worker_feedback_allow),
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


# ---------------------------------------------------------------------------
# Remote-readiness report (still pure / local / read-only)
# ---------------------------------------------------------------------------
#
# A pre-deploy checklist the maintainer can run *before* touching any remote D1
# or worker. It composes the static contract check above with extra checks that
# matter specifically for a remote rollout: the v1 migration is present, the two
# migrations are sequenced correctly (v1 lands the P0 columns, v1.1 only adds the
# derived buckets), the dashboard keeps anonymous-daily-id wording and carries
# the v1.1 analysis tiles, the client's opt-in/opt-out defaults are unchanged,
# and no surface carries a content field. It performs NO network/D1/deploy
# action — it only reads files and the declared contract.


def validate_optin_defaults(telemetry_src: str) -> tuple[bool, list[str]]:
    """Confirm the client keeps telemetry opt-*out* by default and gates remote
    + feedback sends behind their own consent. Static source scan — never runs
    the module. Returns ``(ok, problems)``."""
    problems: list[str] = []
    src = telemetry_src or ""
    if not src:
        return False, ["missing telemetry.py source"]
    # Local stats default OFF: is_enabled falls back to the stored flag defaulting
    # to False.
    if 'get("enabled", False)' not in src:
        problems.append("local telemetry default is not opt-out (enabled→False)")
    # The gate pattern: ``if not is_enabled():`` immediately followed by a
    # ``return False`` (so enabling local stats can't silently turn on a
    # downstream send). Matched as a unit so a stray ``return False`` elsewhere
    # in the body doesn't satisfy the check on its own.
    gate_re = re.compile(r"if not is_enabled\(\):\s*\n\s*return False")
    # Remote send requires local stats first.
    if "def is_remote_enabled" in src:
        body = src.split("def is_remote_enabled", 1)[1].split("\ndef ", 1)[0]
        if not gate_re.search(body):
            problems.append("is_remote_enabled does not gate on is_enabled()")
        if 'get("remote_enabled", False)' not in body:
            problems.append("remote send default is not opt-out (remote_enabled→False)")
    else:
        problems.append("is_remote_enabled missing")
    # Feedback send requires local stats first and its own consent.
    if "def is_feedback_enabled" in src:
        body = src.split("def is_feedback_enabled", 1)[1].split("\ndef ", 1)[0]
        if not gate_re.search(body):
            problems.append("is_feedback_enabled does not gate on is_enabled()")
        if 'get("feedback_enabled", False)' not in body:
            problems.append("feedback default is not opt-out (feedback_enabled→False)")
    else:
        problems.append("is_feedback_enabled missing")
    return (not problems), problems


def validate_dashboard_wording(worker_js: str) -> tuple[bool, list[str]]:
    """Confirm the dashboard counts anonymous daily IDs (never claims unique
    people) and renders the v1.1 analysis tiles. Static source scan. Returns
    ``(ok, problems)``."""
    problems: list[str] = []
    src = worker_js or ""
    if not src:
        return False, ["missing worker/src/index.js"]
    for token in DASHBOARD_REQUIRED_WORDING:
        if token not in src:
            problems.append(f"dashboard missing required daily-id wording: {token!r}")
    for token in DASHBOARD_FORBIDDEN_WORDING:
        if token in src:
            problems.append(f"dashboard uses unique-person claim: {token!r}")
    for label in DASHBOARD_V1_1_LABELS:
        if label not in src:
            problems.append(f"dashboard missing v1.1 analysis tile label: {label!r}")
    return (not problems), problems


def _default_package_src(worker_dir: Path) -> Path:
    """Best-effort path to the installed/source ``telemetry.py`` relative to the
    worker dir (``<repo>/worker`` → ``<repo>/src/piia_engram/telemetry.py``)."""
    return worker_dir.parent / "src" / "piia_engram" / "telemetry.py"


def validate_remote_readiness(
    worker_dir: str | Path,
    *,
    package_src: str | Path | None = None,
) -> dict[str, Any]:
    """Produce a local, read-only remote-readiness report.

    Returns a dict with an overall ``ok`` plus a ``checks`` list — one entry per
    named check (``name``, ``ok``, ``detail``, ``problems``) — and a flattened
    ``problems`` list. Never raises on a missing file and never performs any
    network/D1/deploy action.

    Checks: client payload fields, worker event allowlist, worker feedback
    allowlist, schema columns, v1/v1.1 migration files, dashboard wording,
    opt-in defaults, no content fields, and D1 migration sequencing (v1 before
    v1.1).
    """
    wdir = Path(worker_dir).expanduser().resolve()
    contract = validate_telemetry_contract(wdir)
    checks: list[dict[str, Any]] = []

    def add(name: str, problems: list[str], detail: str) -> None:
        checks.append({
            "name": name,
            "ok": not problems,
            "detail": detail,
            "problems": problems,
        })

    # 1. Client payload fields ↔ schema columns (reuse the contract check).
    events_columns = set(contract.get("events_columns", []))
    payload_problems = [
        f"{key} -> {col}"
        for key, col in PAYLOAD_TO_COLUMN.items()
        if col not in events_columns
    ]
    add("client_payload_fields", payload_problems,
        f"{len(PAYLOAD_TO_COLUMN)} declared payload fields")

    # 4. Schema columns present (events table parsed).
    schema_problems = [] if events_columns else ["events table has no columns / schema.sql missing"]
    add("schema_columns", schema_problems, f"{len(events_columns)} events columns")

    # 2/3. Worker allowlists in sync (lift the drift problems from the contract).
    allow_problems = [
        p for p in contract.get("problems", [])
        if "allowlist drift" in p or "content-bearing worker allowlist" in p
    ]
    ev_allow = contract.get("worker_event_allowlist", [])
    fb_allow = contract.get("worker_feedback_allowlist", [])
    add("worker_event_allowlist",
        [p for p in allow_problems if "event allowlist" in p],
        f"{len(ev_allow)} event allowlist fields")
    add("worker_feedback_allowlist",
        [p for p in allow_problems if "feedback allowlist" in p],
        f"{len(fb_allow)} feedback allowlist fields")

    # 5 + 9. Migration files present and sequenced (v1 before v1.1).
    mig_dir = wdir / "migrations"
    v1_path = mig_dir / V1_MIGRATION_NAME
    v1_1_path = mig_dir / V1_1_MIGRATION_NAME
    seq_problems: list[str] = []
    v1_added: set[str] = set()
    v1_1_added: set[str] = set()
    if v1_path.is_file():
        v1_added = parse_added_columns(v1_path.read_text(encoding="utf-8"))
        missing_p0 = sorted(V1_P0_COLUMNS - v1_added)
        if missing_p0:
            seq_problems.append(f"v1 migration missing P0 columns: {missing_p0}")
    else:
        seq_problems.append(f"missing v1 migration: {V1_MIGRATION_NAME}")
    if v1_1_path.is_file():
        v1_1_added = parse_added_columns(v1_1_path.read_text(encoding="utf-8"))
        missing_p1 = sorted(V1_1_DERIVED_COLUMNS - v1_1_added)
        if missing_p1:
            seq_problems.append(f"v1.1 migration missing derived columns: {missing_p1}")
        # Sequencing: v1.1 must not re-add the P0 columns (those are v1's job);
        # overlap would mean v1.1 can't be applied after v1 without erroring.
        overlap = sorted(V1_P0_COLUMNS & v1_1_added)
        if overlap:
            seq_problems.append(
                f"v1.1 migration re-adds P0 columns (apply-after-v1 would fail): {overlap}")
    else:
        seq_problems.append(f"missing v1.1 migration: {V1_1_MIGRATION_NAME}")
    # Lexical ordering sanity: the v1 file name sorts before the v1.1 file name,
    # so a plain `migrations apply` runs them in the right order.
    if V1_MIGRATION_NAME >= V1_1_MIGRATION_NAME:
        seq_problems.append("migration filenames do not sort v1 before v1.1")
    add("migration_files",
        [p for p in seq_problems if "missing" in p],
        f"v1 adds {len(v1_added)}, v1.1 adds {len(v1_1_added)} columns")
    add("migration_sequencing",
        [p for p in seq_problems if "re-adds" in p or "do not sort" in p],
        "v1 (P0) then v1.1 (derived buckets)")

    # 6. Dashboard wording + v1.1 tiles.
    index_path = wdir / "src" / "index.js"
    worker_js = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""
    dash_ok, dash_problems = validate_dashboard_wording(worker_js)
    add("dashboard_wording", dash_problems, "anonymous daily-id wording + v1.1 tiles")

    # 7. Opt-in / opt-out defaults unchanged.
    src_path = Path(package_src) if package_src else _default_package_src(wdir)
    telemetry_src = src_path.read_text(encoding="utf-8") if src_path.is_file() else ""
    optin_ok, optin_problems = validate_optin_defaults(telemetry_src)
    add("optin_defaults", optin_problems, "telemetry opt-out by default; remote/feedback gated")

    # 8. No content fields anywhere (reuse the contract's content findings).
    content_problems = [p for p in contract.get("problems", []) if "content-bearing" in p]
    add("no_content_fields", content_problems, "metadata-only across payload/schema/migration/worker")

    flat_problems: list[str] = []
    for c in checks:
        flat_problems.extend(c["problems"])

    return {
        "worker_dir": str(wdir),
        "package_src": str(src_path),
        "checks": checks,
        "contract_ok": contract.get("ok", False),
        "problems": flat_problems,
        "ok": not flat_problems,
    }


def render_readiness_text(report: dict[str, Any]) -> str:
    status = "READY" if report.get("ok") else f"{len(report.get('problems', []))} blocker(s)"
    lines = [f"Telemetry remote-readiness: {status}"]
    for c in report.get("checks", []):
        mark = "ok " if c["ok"] else "XX "
        lines.append(f"  [{mark}] {c['name']}: {c['detail']}")
        for p in c.get("problems", []):
            lines.append(f"         - {p}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Feedback-report send-boundary guard (L1)
# ---------------------------------------------------------------------------
#
# Enforced at/just-before ``telemetry.send_feedback`` so privacy holds at the
# *send* boundary regardless of what a report builder produced. A coarse,
# count/distribution-only report passes; anything carrying free text, a prompt,
# a file path, an email, or a URL is rejected before network serialization.

# A feedback report carries only coarse metadata: counts, buckets, versions,
# dates, and short category/tool *tags*. None of the real values exceed ~40
# chars, so the caps below are generous for legitimate data while still well
# under the size of a lesson body, prompt, or path. Tags (nested-dict keys, e.g.
# user ``domain`` labels) get the *tighter* budget because they are the only
# user-authored free-text surface that reaches the report.
_FB_MAX_VALUE_LEN = 64      # a string value longer than this is content-shaped
_FB_MAX_TAG_LEN = 48        # nested-dict tag keys (domains, tool/source names)
_FB_MAX_WORDS = 6           # >= this many whitespace tokens ⇒ free text / prompt
_FB_MAX_TAG_WORDS = 5       # a tag is 1–4 words; a sentence is more
_FB_MAX_CJK = 12            # >= this many CJK chars ⇒ a phrase/sentence, not a tag
_FB_MAX_DEPTH = 6           # structural runaway guard

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://|www\.|://", re.IGNORECASE)
_DRIVE_RE = re.compile(r"[A-Za-z]:[\\/]")
# Homoglyph at-signs (fullwidth/small/CJK) — block unicode email/handle bypasses.
_HOMOGLYPH_AT = "＠﹫"
# CJK Unified Ideographs (incl. extension A) — used to size CJK text, which has
# no whitespace and would otherwise defeat the word-count free-text check.
_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")


def _cjk_count(value: str) -> int:
    return len(_CJK_RE.findall(value))


def _content_string_reason(value: str) -> str:
    """Return a reason code if ``value`` looks like content rather than coarse
    metadata, else ``''``. Catches over-long values, control chars, emails
    (incl. homoglyph @), URLs, file paths, multi-word free text, and CJK
    phrases/sentences (which carry no whitespace)."""
    if len(value) > _FB_MAX_VALUE_LEN:
        return "too_long"
    if any(ch in value for ch in "\n\r\t"):
        return "control_chars"
    if _EMAIL_RE.search(value) or any(ch in value for ch in _HOMOGLYPH_AT):
        return "email"
    if _URL_RE.search(value):
        return "url"
    if _DRIVE_RE.search(value) or "/" in value or "\\" in value:
        return "path"
    if _cjk_count(value) >= _FB_MAX_CJK:
        return "cjk_text"
    if len(value.split()) >= _FB_MAX_WORDS:
        return "free_text"
    return ""


def _safe_feedback_tag(key: Any) -> bool:
    """A nested-dict key must be a short tag (domain/tool/source name), not a
    path/email/url/sentence. An empty string is allowed — it is the "unidentified
    source" bucket the builders emit and carries no content."""
    if not isinstance(key, str):
        return False
    if key == "":
        return True
    if len(key) > _FB_MAX_TAG_LEN:
        return False
    if any(ch in key for ch in "\n\r\t"):
        return False
    if _EMAIL_RE.search(key) or any(ch in key for ch in _HOMOGLYPH_AT):
        return False
    if _URL_RE.search(key):
        return False
    if _DRIVE_RE.search(key) or "/" in key or "\\" in key:
        return False
    if _cjk_count(key) >= _FB_MAX_CJK:
        return False
    if len(key.split()) >= _FB_MAX_TAG_WORDS:
        return False
    return True


def _safe_feedback_value(value: Any, *, depth: int = 0) -> tuple[bool, str]:
    """Recursively confirm a value is coarse metadata only.

    Allowed: ``None`` / bool / int / float; short non-content strings; lists of
    safe values; dicts whose keys are safe tags and whose values are safe.
    """
    if depth > _FB_MAX_DEPTH:
        return False, "nesting_too_deep"
    if value is None or isinstance(value, (bool, int, float)):
        return True, ""
    if isinstance(value, str):
        reason = _content_string_reason(value)
        return (not reason), reason
    if isinstance(value, (list, tuple)):
        for item in value:
            ok, reason = _safe_feedback_value(item, depth=depth + 1)
            if not ok:
                return False, reason
        return True, ""
    if isinstance(value, dict):
        for k, v in value.items():
            if not _safe_feedback_tag(k):
                return False, f"unsafe_key:{str(k)[:40]}"
            ok, reason = _safe_feedback_value(v, depth=depth + 1)
            if not ok:
                return False, reason
        return True, ""
    return False, f"unsupported_type:{type(value).__name__}"


def validate_feedback_report(report: Any) -> tuple[bool, list[str]]:
    """Validate a feedback report against the send-boundary allowlist + content guard.

    Returns ``(ok, problems)``. ``ok`` is True only when every top-level key is on
    :data:`FEEDBACK_ALLOWED_KEYS` **and** every value (recursively) is coarse
    metadata — counts, buckets, short tags, versions, dates — never free text,
    prompts, file paths, emails, or URLs. Pure and never raises.
    """
    if not isinstance(report, dict):
        return False, ["report is not a dict"]
    problems: list[str] = []
    for key, value in report.items():
        if key not in FEEDBACK_ALLOWED_KEYS:
            problems.append(f"disallowed key: {str(key)[:60]}")
            continue
        ok, reason = _safe_feedback_value(value)
        if not ok:
            problems.append(f"content-like value at '{key}': {reason}")
    return (not problems), problems
