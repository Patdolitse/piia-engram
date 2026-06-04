"""Deny-by-default write-gate matrix (Codex round-5 a4 hardening).

Round 5 found that a4's write gate was a *manual allowlist*: each handler had
``maybe_refuse_write`` inserted by hand, and the a4 test only enumerated the
tools that had already been gated — a self-confirming loop that missed 11 write
tools (incl. ``set_caller_trust``, which let a low-trust caller self-escalate to
owner and read secret content).

This module replaces the manual allowlist with two enforced invariants:

1. **Completeness (reflection):** every ``@mcp.tool()`` MUST appear in
   ``mcp_server.TOOL_GOVERNANCE_CLASS``. A newly added tool that nobody
   classified makes this test RED — deny by default.

2. **Behaviour (writer-spy):** for every tool classified as a mutating class
   (``governed_write`` / ``owner_only_write`` / ``export_owner_only``), a
   low-trust ``read-only-external`` caller MUST be refused AND leave the data
   store byte-for-byte unchanged (governance receipts excluded — those are the
   one thing a refused caller is allowed to write).

Together: "forgot to gate a new write tool" is a failing build, not a silent
low-trust write hole.
"""

import ast
import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mcp_tool_function_nodes_from_source() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Reflect over the mcp_server source AST for every ``@mcp.tool()`` def.

    Source-level (not the live registry) so tier filtering — which removes
    non-core tools from ``_tool_manager._tools`` at import — cannot hide a tool
    from the completeness check.
    """
    import piia_engram.mcp_server as mcp_server

    src = Path(mcp_server.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    nodes: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            # matches @mcp.tool() and @mcp.tool
            if isinstance(target, ast.Attribute) and target.attr == "tool":
                nodes[node.name] = node
    return nodes


def _mcp_tool_names_from_source() -> set[str]:
    return set(_mcp_tool_function_nodes_from_source())


def _node_calls_governance_gate(
    node: ast.FunctionDef | ast.AsyncFunctionDef, gate_name: str
) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and func.attr == gate_name:
            return True
    return False


def _setup_engram(tmp_path: Path) -> Path:
    engram = tmp_path / "engram"
    identity = engram / "identity"
    identity.mkdir(parents=True)
    (identity / "profile.json").write_text(
        json.dumps({"role": "developer", "language": "en"}), encoding="utf-8"
    )
    knowledge = engram / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "lessons.json").write_text("[]", encoding="utf-8")
    (knowledge / "decisions.json").write_text("[]", encoding="utf-8")
    return engram


def _make_engram(engram_dir: Path):
    import sys

    sys.path.insert(0, str(_ROOT / "src"))
    from piia_engram.core import Engram

    return Engram(engram_dir)


def _run(coro):
    return asyncio.run(coro)


def _is_governance_artifact(rel: Path) -> bool:
    """True ONLY for the receipt ledger a refused low-trust caller is *allowed*
    to append to: ``governance_ledger.jsonl`` and its lock
    ``.engram-governance-ledger.lock``, both at the engram root.

    Codex round-6 caught that excluding the whole ``governance/`` subtree was
    too broad — it would silently swallow a ``governance/grants.json`` mutation
    (the exact self-escalation we are guarding against). So we deliberately do
    NOT exclude the ``governance/`` subdir: a refused caller has no business
    writing grants, and if one ever does, this snapshot MUST flag it."""
    if rel.parts and rel.parts[0] == "governance":
        return False  # grants.json etc. — a refused write here is a real breach
    name = rel.name.lower()
    return name == "governance_ledger.jsonl" or name == ".engram-governance-ledger.lock"


def _snapshot(root: Path) -> dict[str, str]:
    """Map every file under ``root`` to a content hash, EXCLUDING governance
    receipt artifacts — a refused low-trust caller is permitted to write a
    governance receipt and nothing else, so it must not count as a data
    mutation."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if _is_governance_artifact(rel):
            continue
        out[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _snapshot_plain(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _snapshot_many(engram_root: Path, extra_roots: list[Path] | None = None) -> dict[str, str]:
    out = {f"engram::{rel}": digest for rel, digest in _snapshot(engram_root).items()}
    for idx, root in enumerate(extra_roots or []):
        root = root.resolve()
        if root == engram_root.resolve():
            continue
        for rel, digest in _snapshot_plain(root).items():
            out[f"external{idx}::{rel}"] = digest
    return out


def _external_watch_roots(tmp_path: Path, monkeypatch) -> list[Path]:
    """Known root-external locations that read-path side effects often target."""
    fake_home = tmp_path / "fake-home"
    fake_tmp = tmp_path / "fake-temp"
    fake_home.mkdir()
    fake_tmp.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("TMP", str(fake_tmp))
    monkeypatch.setenv("TEMP", str(fake_tmp))
    monkeypatch.setattr(tempfile, "tempdir", str(fake_tmp), raising=False)
    return [fake_home / ".engram", fake_home / ".claude", fake_tmp]


def _enable_local_telemetry(root: Path) -> None:
    (root / "telemetry_config.json").write_text(
        json.dumps({"enabled": True, "local_uuid": "00000000-0000-4000-8000-000000000001"}),
        encoding="utf-8",
    )


def _dummy_kwargs(func) -> dict:
    """Fill a tool's required parameters with type-appropriate dummies.

    The write gate fires before any argument is consumed, so the dummy values
    never reach mutation logic — they only need to satisfy the call signature.
    """
    import inspect

    kwargs: dict = {}
    sig = inspect.signature(func)
    for name, param in sig.parameters.items():
        if param.default is not inspect.Parameter.empty:
            continue  # optional — leave it
        ann = param.annotation
        if ann is int:
            kwargs[name] = 1
        elif ann is bool:
            kwargs[name] = False
        elif ann is float:
            kwargs[name] = 1.0
        else:
            kwargs[name] = "x"
    return kwargs


def _is_refusal(result) -> bool:
    import piia_engram.mcp_server as mcp_server

    return mcp_server._gov_rt.is_governance_refusal(result)


# ---------------------------------------------------------------------------
# 1. Completeness — every @mcp.tool() is classified (deny by default)
# ---------------------------------------------------------------------------


class TestClassificationCompleteness:
    def test_every_tool_is_classified(self):
        import piia_engram.mcp_server as mcp_server

        tools = _mcp_tool_names_from_source()
        classified = set(mcp_server.TOOL_GOVERNANCE_CLASS)
        missing = tools - classified
        assert not missing, (
            "Unclassified @mcp.tool(s) — add to TOOL_GOVERNANCE_CLASS "
            f"(deny-by-default): {sorted(missing)}"
        )

    def test_no_stale_classification_entries(self):
        import piia_engram.mcp_server as mcp_server

        tools = _mcp_tool_names_from_source()
        classified = set(mcp_server.TOOL_GOVERNANCE_CLASS)
        stale = classified - tools
        assert not stale, f"TOOL_GOVERNANCE_CLASS has entries for non-tools: {sorted(stale)}"

    def test_all_categories_valid(self):
        import piia_engram.mcp_server as mcp_server

        bad = {
            name: cat
            for name, cat in mcp_server.TOOL_GOVERNANCE_CLASS.items()
            if cat not in mcp_server.WRITE_GATE_CLASSES
        }
        assert not bad, f"Invalid governance categories: {bad}"

    def test_mutating_tools_call_their_declared_gate(self):
        import piia_engram.mcp_server as mcp_server

        gate_by_category = {
            "governed_write": "maybe_refuse_write",
            "owner_only_write": "maybe_refuse_owner_write",
            "export_owner_only": "maybe_refuse_export",
        }
        nodes = _mcp_tool_function_nodes_from_source()
        missing: dict[str, str] = {}
        for name, category in mcp_server.TOOL_GOVERNANCE_CLASS.items():
            gate = gate_by_category.get(category)
            if gate is None:
                continue
            node = nodes.get(name)
            if node is None or not _node_calls_governance_gate(node, gate):
                missing[name] = gate
        assert not missing, (
            "Mutating tool(s) are classified but do not call their declared "
            f"governance gate: {missing}"
        )


# ---------------------------------------------------------------------------
# 2. Writer-spy — low-trust caller refused + zero data-file delta
# ---------------------------------------------------------------------------


def _mutating_tools() -> list[str]:
    import piia_engram.mcp_server as mcp_server

    return sorted(
        name
        for name, cat in mcp_server.TOOL_GOVERNANCE_CLASS.items()
        if cat in mcp_server.WRITE_GATE_CLASSES_MUTATING
    )


def _read_tools() -> list[str]:
    import piia_engram.mcp_server as mcp_server

    return sorted(
        name
        for name, cat in mcp_server.TOOL_GOVERNANCE_CLASS.items()
        if cat == "read"
    )


def _read_tools_that_call_track() -> list[str]:
    import piia_engram.mcp_server as mcp_server

    src = Path(mcp_server.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: list[str] = []
    read_tools = set(_read_tools())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if node.name not in read_tools:
            continue
        if any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_track"
            for child in ast.walk(node)
        ):
            out.append(node.name)
    return sorted(out)


def _reset_tracking(mcp_server) -> None:
    """Reset telemetry/session globals so file-delta tests are deterministic."""
    mcp_server._track_count = 0
    if getattr(mcp_server, "_ToolCallTracker", None) is not None:
        mcp_server._tracker = mcp_server._ToolCallTracker()
    try:
        mcp_server._session._stop_event.set()
        thread = getattr(mcp_server._session, "_heartbeat_thread", None)
        if thread is not None:
            thread.join(timeout=1.0)
    except Exception:
        pass
    mcp_server._session = mcp_server._SessionTracker()


class TestWriterSpyExternalNoDelta:
    """read-only-external caller: every mutating tool refuses and writes no
    data file (governance receipts excluded)."""

    @pytest.mark.parametrize("tool_name", _mutating_tools())
    def test_external_refused_and_no_file_delta(self, tmp_path, monkeypatch, tool_name):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e

        func = getattr(mcp_server, tool_name)
        before = _snapshot(engram)
        result = _run(func(**_dummy_kwargs(func)))
        after = _snapshot(engram)

        assert _is_refusal(result), (
            f"{tool_name} did not refuse a read-only-external caller; "
            f"returned: {result!r:.200}"
        )
        assert before == after, (
            f"{tool_name} mutated data files despite refusing the caller. "
            f"Changed: {sorted(set(before) ^ set(after)) or 'content of existing files'}"
        )


# ---------------------------------------------------------------------------
# 3. Self-escalation regression (Round-5 P1-1 / P1-4)
# ---------------------------------------------------------------------------


class TestNoSelfEscalation:
    """A low-trust web caller must not be able to grant itself owner trust."""

    def test_set_caller_trust_external_refused_and_grant_not_written(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e

        result = _run(mcp_server.set_caller_trust("web", "private-self"))
        assert _is_refusal(result), "set_caller_trust must refuse a non-owner caller"

        # The grant must NOT have been written — web is still read-only-external.
        from piia_engram.governance_store import GrantStore

        store = GrantStore(engram)
        assert store.trust_level_for("web", "web") != "private-self", (
            "web self-escalated to private-self — grant store was mutated by a "
            "non-owner"
        )

    def test_revoke_caller_external_refused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e

        result = _run(mcp_server.revoke_caller("victim-agent"))
        assert _is_refusal(result), "revoke_caller must refuse a non-owner caller"

        from piia_engram.governance_store import GrantStore

        store = GrantStore(engram)
        assert not store.is_revoked("victim-agent"), (
            "non-owner mutated the revocation list"
        )


# ---------------------------------------------------------------------------
# 4. Owner / trusted-local still work (no regression)
# ---------------------------------------------------------------------------


class TestOwnerStillWorks:
    def test_owner_set_caller_trust_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")  # private-self
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e

        result = _run(mcp_server.set_caller_trust("cursor", "trusted-local"))
        assert not _is_refusal(result), "owner must be allowed to set caller trust"

        from piia_engram.governance_store import GrantStore

        store = GrantStore(engram)
        assert store.trust_level_for("cursor", "cursor") == "trusted-local"

    def test_trusted_local_wrap_up_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")  # trusted-local
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e

        result = _run(mcp_server.wrap_up_session(summary="did some work today"))
        assert not _is_refusal(result), (
            "trusted-local must be allowed to wrap up a session"
        )

    def test_governance_off_wrap_up_allowed(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e

        result = _run(mcp_server.wrap_up_session(summary="work with governance off"))
        assert not _is_refusal(result)


# ---------------------------------------------------------------------------
# 5. Read-path side-effect leak (Codex round-6 P1)
# ---------------------------------------------------------------------------


def _seed_knowledge(e) -> None:
    """Add a secret + public lesson and decision so the read tools have
    entries whose access bookkeeping *would* be bumped+written if not gated."""
    e.add_lesson("secret lesson body", domain="python", sensitivity="secret")
    e.add_lesson("public lesson body", domain="python", sensitivity="public")
    e.add_decision("secret q", "secret choice", sensitivity="secret")
    e.add_decision("public q", "public choice", sensitivity="public")


def _seed_read_probe_store(tmp_path: Path, e) -> dict[str, str]:
    """Populate enough data for every read-classed tool to run with real args."""
    project = tmp_path / "probe-project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "probe"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    lesson = e.add_lesson(
        "public lesson body for read sweep",
        domain="python",
        sensitivity="public",
        source_tool="codex",
    )
    e.add_lesson(
        "secret lesson body for read sweep",
        domain="python",
        sensitivity="secret",
        source_tool="codex",
    )
    decision = e.add_decision(
        "public decision question",
        "public decision choice",
        reasoning="public reasoning",
        domain="python",
        sensitivity="public",
        source_tool="codex",
        project=str(project),
    )
    e.add_decision(
        "secret decision question",
        "secret decision choice",
        reasoning="secret reasoning",
        domain="python",
        sensitivity="secret",
        source_tool="codex",
        project=str(project),
    )
    playbook = e.add_playbook(
        {
            "title": "Public read sweep playbook",
            "domain": "python",
            "sensitivity": "public",
            "triggers": ["read sweep"],
            "steps": [
                {"order": 1, "action": "Run public step", "detail": "public detail"}
            ],
        },
        source_tool="codex",
    )
    e.save_project_snapshot(
        str(project),
        {
            "title": "Probe Project",
            "tech_stack": "python",
            "known_issues": "none",
        },
    )
    e.save_agent_context(
        tool="codex",
        content="public saved context",
        session_id="seed-session",
        project_folder=str(project),
        actions=[{"tool_called": "seed", "arguments_summary": "", "result_summary": ""}],
    )
    e.register_tool(
        {
            "name": "probe-tool",
            "category": "runtime",
            "path": str(project),
            "purpose": "read sweep seed",
        },
        registered_by="codex",
    )
    e.add_relation(lesson["id"], "implemented_by", decision["id"])
    return {
        "project_folder": str(project),
        "lesson_id": lesson["id"],
        "decision_id": decision["id"],
        "playbook_id": playbook["id"],
    }


def _read_tool_kwargs(tool_name: str, ids: dict[str, str]) -> dict:
    return {
        "find_similar_knowledge": {"item_id": ids["lesson_id"], "limit": 5},
        "find_tool": {"query": "probe"},
        "get_daily_log": {"project_folder": ids["project_folder"]},
        "get_decision_history": {
            "question": "public decision question",
            "threshold": 0.1,
        },
        "get_decision_thread": {"seed_id": ids["decision_id"]},
        "get_execution_status": {"playbook_id": ids["playbook_id"]},
        "get_knowledge_inheritance": {
            "description": "python read sweep",
            "limit": 5,
        },
        "get_playbook": {"playbook_id": ids["playbook_id"]},
        "get_project_context": {"project_folder": ids["project_folder"]},
        "get_recall": {
            "project_folder": ids["project_folder"],
            "query": "public",
            "limit": 5,
            "token_budget": 500,
        },
        "get_related_knowledge": {"item_id": ids["lesson_id"]},
        "get_relevant_knowledge": {"project_folder": ids["project_folder"], "limit": 5},
        "get_resume_brief": {"project_folder": ids["project_folder"], "token_budget": 500},
        "read_web_content": {"url": "http://127.0.0.1:1/engram-r8-no-server"},
        "search_knowledge": {"query": "public", "scope": "all", "limit": 5},
    }.get(tool_name, {})


class TestReadPathNoAccessWrite:
    """A read by a low-trust read-only-external caller must not write access
    bookkeeping (access_count / last_reviewed) to data files — that bump runs
    before governance filtering, so it is both a low-trust write AND a touch of
    entries above the caller's sensitivity ceiling."""

    @pytest.mark.parametrize(
        "call",
        [
            lambda m: m.get_lessons(),
            lambda m: m.get_decisions(),
            lambda m: m.get_relevant_knowledge(project_folder="x"),
            lambda m: m.get_playbooks(),
        ],
        ids=["get_lessons", "get_decisions", "get_relevant_knowledge", "get_playbooks"],
    )
    def test_external_read_writes_no_access_bookkeeping(
        self, tmp_path, monkeypatch, call
    ):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")  # read-only-external
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        _seed_knowledge(e)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e

        before = _snapshot(engram)
        _run(call(mcp_server))
        after = _snapshot(engram)

        assert before == after, (
            "low-trust read mutated data files via access bookkeeping. "
            f"Changed: {sorted(set(before) ^ set(after)) or 'content of existing files'}"
        )

    def test_owner_read_does_bump_access(self, tmp_path, monkeypatch):
        """Control: the owner's read still records access (no over-correction)."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")  # private-self owner
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        _seed_knowledge(e)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e

        before = _snapshot(engram)
        _run(mcp_server.get_lessons())
        after = _snapshot(engram)

        assert before != after, (
            "owner read should still bump access_count/last_reviewed; the gate "
            "over-corrected and suppressed the owner's own bookkeeping"
        )


class TestAllReadToolsNoFileDelta:
    """Reflection sweep for every tool classed as read.

    Round 8's invariant is broader than access bookkeeping: a non-owner read
    must not persist telemetry, beta events, audit rows, search indexes,
    caches, session checkpoints, or any other file side effect.
    """

    @pytest.mark.parametrize("client_type", ["web", "claude_code"])
    @pytest.mark.parametrize("tool_name", _read_tools())
    def test_non_owner_read_tool_writes_no_files(
        self, tmp_path, monkeypatch, client_type, tool_name
    ):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", client_type)
        monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
        monkeypatch.setenv("ENGRAM_AUDIT", "1")
        monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
        monkeypatch.delenv("ENGRAM_BETA_TRACKING", raising=False)  # default-on
        monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        ids = _seed_read_probe_store(tmp_path, e)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e
        _reset_tracking(mcp_server)

        func = getattr(mcp_server, tool_name)
        before = _snapshot(engram)
        _run(func(**_read_tool_kwargs(tool_name, ids)))
        after = _snapshot(engram)

        assert before == after, (
            f"{client_type} read tool {tool_name} wrote files. "
            f"Changed: {sorted(set(before) ^ set(after)) or 'content of existing files'}"
        )

    @pytest.mark.parametrize("client_type", ["web", "claude_code"])
    def test_non_owner_read_sequence_does_not_flush_or_checkpoint(
        self, tmp_path, monkeypatch, client_type
    ):
        extra_roots = _external_watch_roots(tmp_path, monkeypatch)
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", client_type)
        monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
        monkeypatch.setenv("ENGRAM_AUDIT", "1")
        monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
        monkeypatch.delenv("ENGRAM_BETA_TRACKING", raising=False)
        monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        ids = _seed_read_probe_store(tmp_path, e)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e
        _reset_tracking(mcp_server)

        before = _snapshot_many(engram, extra_roots)
        for tool_name in _read_tools():
            func = getattr(mcp_server, tool_name)
            _run(func(**_read_tool_kwargs(tool_name, ids)))
        after = _snapshot_many(engram, extra_roots)

        assert before == after, (
            f"{client_type} read sequence flushed telemetry/session files. "
            f"Changed: {sorted(set(before) ^ set(after)) or 'content of existing files'}"
        )

    @pytest.mark.parametrize("client_type", ["web", "claude_code"])
    def test_non_owner_repeated_read_never_checkpoints(
        self, tmp_path, monkeypatch, client_type
    ):
        """Hammer a single _track-ing read past the flush + checkpoint thresholds.

        The per-tool and full-sweep tests above invoke each tool only ONCE, so
        they never reach ``_FLUSH_EVERY`` (telemetry.log) or
        ``_SessionTracker._CHECKPOINT_EVERY`` (contexts/mcp_auto checkpoint) —
        a non-owner ``_track`` that wasn't suppressed slips straight through
        them (verified: removing the ``_track`` owner-gate leaves the sweep
        green). The real leak only surfaces on the 20th call. This test pins
        the ``_track`` owner-gate by driving one read past both thresholds and
        asserting zero file delta; it goes RED the moment the gate is removed."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", client_type)
        monkeypatch.setenv("ENGRAM_AUDIT", "1")
        monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
        monkeypatch.delenv("ENGRAM_BETA_TRACKING", raising=False)
        monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        ids = _seed_read_probe_store(tmp_path, e)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e
        _reset_tracking(mcp_server)

        # Drive past BOTH the telemetry flush and the session checkpoint so a
        # leaked non-owner _track persists a file. Read thresholds from the
        # module so this keeps biting if the constants change.
        reps = max(
            getattr(mcp_server, "_FLUSH_EVERY", 10),
            getattr(mcp_server._session, "_CHECKPOINT_EVERY", 20),
        ) + 5
        before = _snapshot(engram)
        for _ in range(reps):
            _run(
                mcp_server.get_project_context(
                    project_folder=ids["project_folder"]
                )
            )
        after = _snapshot(engram)

        assert before == after, (
            f"{client_type} repeated read ({reps}x) flushed telemetry or wrote "
            f"a session checkpoint. Changed: "
            f"{sorted(set(before) ^ set(after)) or 'content of existing files'}"
        )


# ---------------------------------------------------------------------------
# 6. Cold-start telemetry leak (Codex round-7 P1)
# ---------------------------------------------------------------------------


class TestReadPathStructuralClosure:
    """Round 9 structural closure: list-, threshold-, path-, and error-independent."""

    @pytest.mark.parametrize("client_type", ["web", "claude_code"])
    @pytest.mark.parametrize("tool_name", _read_tools_that_call_track())
    def test_every_tracked_read_repeated_past_thresholds_writes_nothing(
        self, tmp_path, monkeypatch, client_type, tool_name
    ):
        extra_roots = _external_watch_roots(tmp_path, monkeypatch)
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", client_type)
        monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
        monkeypatch.setenv("ENGRAM_AUDIT", "1")
        monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
        monkeypatch.delenv("ENGRAM_BETA_TRACKING", raising=False)
        monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "1")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        _enable_local_telemetry(engram)
        e = _make_engram(engram)
        ids = _seed_read_probe_store(tmp_path, e)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e
        _reset_tracking(mcp_server)

        reps = max(
            getattr(mcp_server, "_FLUSH_EVERY", 10),
            getattr(mcp_server._session, "_CHECKPOINT_EVERY", 20),
        ) + 5
        func = getattr(mcp_server, tool_name)
        before = _snapshot_many(engram, extra_roots)
        for _ in range(reps):
            _run(func(**_read_tool_kwargs(tool_name, ids)))
        mcp_server._session._heartbeat_tick()
        after = _snapshot_many(engram, extra_roots)

        assert before == after, (
            f"{client_type} repeated read {tool_name} ({reps}x) wrote inside or "
            f"outside the Engram root. Changed: "
            f"{sorted(set(before) ^ set(after)) or 'content of existing files'}"
        )

    def test_external_get_recall_preflights_before_gathering(self, tmp_path, monkeypatch):
        """Recall bundles identity + knowledge, so non-owners must be refused
        before any search, telemetry, or session-tracking side effect runs."""
        extra_roots = _external_watch_roots(tmp_path, monkeypatch)
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
        monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
        monkeypatch.setenv("ENGRAM_AUDIT", "1")
        monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
        monkeypatch.delenv("ENGRAM_BETA_TRACKING", raising=False)
        monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "1")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        _enable_local_telemetry(engram)
        e = _make_engram(engram)
        ids = _seed_read_probe_store(tmp_path, e)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e
        _reset_tracking(mcp_server)

        before = _snapshot_many(engram, extra_roots)
        result = _run(mcp_server.get_recall(
            project_folder=ids["project_folder"],
            query="public",
            limit=5,
            token_budget=500,
        ))
        mcp_server._session._heartbeat_tick()
        after = _snapshot_many(engram, extra_roots)

        assert _is_refusal(result), (
            f"get_recall must refuse a non-owner before gathering; returned: {result!r:.200}"
        )
        assert before == after, (
            "non-owner get_recall wrote files before refusal. Changed: "
            f"{sorted(set(before) ^ set(after)) or 'content of existing files'}"
        )

    def test_track_owner_gate_resolution_error_fails_closed(self, tmp_path, monkeypatch):
        extra_roots = _external_watch_roots(tmp_path, monkeypatch)
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
        monkeypatch.setenv("ENGRAM_AUDIT", "1")
        monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
        monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        _enable_local_telemetry(engram)
        e = _make_engram(engram)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e
        _reset_tracking(mcp_server)

        def _boom(*args, **kwargs):
            raise RuntimeError("grant resolution unavailable")

        monkeypatch.setattr(mcp_server._gov_rt, "caller_is_owner", _boom)
        reps = max(
            getattr(mcp_server, "_FLUSH_EVERY", 10),
            getattr(mcp_server._session, "_CHECKPOINT_EVERY", 20),
        ) + 5
        before = _snapshot_many(engram, extra_roots)
        for _ in range(reps):
            mcp_server._track("get_project_context", success=True)
        after = _snapshot_many(engram, extra_roots)

        assert before == after, (
            "_track must fail closed when owner resolution errors for a read tool; "
            f"Changed: {sorted(set(before) ^ set(after)) or 'content of existing files'}"
        )

    def test_audit_owner_gate_resolution_error_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
        monkeypatch.setenv("ENGRAM_AUDIT", "1")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import governance_runtime as gov_rt

        def _boom(*args, **kwargs):
            raise RuntimeError("grant resolution unavailable")

        monkeypatch.setattr(gov_rt, "caller_is_owner", _boom)
        before = _snapshot(engram)
        e._audit.log("read", "knowledge/lessons", detail="returned 1 item")
        after = _snapshot(engram)

        assert before == after, (
            "Audit read logging must fail closed when owner resolution errors; "
            f"Changed: {sorted(set(before) ^ set(after)) or 'content of existing files'}"
        )

    def test_owner_read_side_effects_still_record(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        monkeypatch.setenv("ENGRAM_AUDIT", "1")
        monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
        monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        _enable_local_telemetry(engram)
        e = _make_engram(engram)
        ids = _seed_read_probe_store(tmp_path, e)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e
        _reset_tracking(mcp_server)
        reps = max(
            getattr(mcp_server, "_FLUSH_EVERY", 10),
            getattr(mcp_server._session, "_CHECKPOINT_EVERY", 20),
        ) + 5
        before = _snapshot(engram)
        for _ in range(reps):
            _run(mcp_server.get_project_context(project_folder=ids["project_folder"]))
        after = _snapshot(engram)

        assert before != after, "owner read telemetry/audit/checkpoint should still record"
        assert (engram / "audit.log").exists()
        assert (engram / "telemetry.log").exists()
        assert any((engram / "contexts").rglob("*.md"))


class TestColdStartNoTelemetryWrite:
    """``get_user_context`` is classed ``read``, but it used to emit a
    ``cold_start`` beta event (``<root>/beta_events.jsonl``) and a telemetry
    record BEFORE the owner-only governance gate ran. A low-trust read therefore
    landed a write on disk — the same "side-effect-before-govern" bug class as
    rounds 5/6. The fix evaluates ``caller_is_owner`` up front and short-circuits
    a non-owner to the refusal before any side effect."""

    @pytest.mark.parametrize("client_type", ["web", "claude_code"])
    @pytest.mark.parametrize("level", ["quick", "standard", "full"])
    def test_non_owner_cold_start_all_levels_write_nothing(
        self, tmp_path, monkeypatch, client_type, level
    ):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", client_type)
        monkeypatch.delenv("ENGRAM_BETA_TRACKING", raising=False)  # default-on
        monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
        monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        _seed_knowledge(e)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e
        _reset_tracking(mcp_server)

        before = _snapshot(engram)
        result = _run(mcp_server.get_user_context(level=level))
        after = _snapshot(engram)

        assert _is_refusal(result), (
            f"get_user_context(level={level!r}) must refuse {client_type}; "
            f"returned: {result!r:.200}"
        )
        assert before == after, (
            f"{client_type} cold-start level={level!r} wrote files. "
            f"Changed: {sorted(set(before) ^ set(after)) or 'content of existing files'}"
        )
        assert not (engram / "beta_events.jsonl").exists()

    def test_external_cold_start_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")  # read-only-external
        monkeypatch.delenv("ENGRAM_BETA_TRACKING", raising=False)  # default-on
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        _seed_knowledge(e)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e

        before = _snapshot(engram)
        result = _run(mcp_server.get_user_context())
        after = _snapshot(engram)

        assert _is_refusal(result), (
            "get_user_context must refuse a read-only-external caller "
            f"(cold-start dump is owner-only); returned: {result!r:.200}"
        )
        assert before == after, (
            "low-trust cold-start mutated files (e.g. beta_events.jsonl) before "
            "the governance gate. "
            f"Changed: {sorted(set(before) ^ set(after)) or 'content of existing files'}"
        )
        # Belt-and-braces: the beta event file must not exist at all.
        assert not (engram / "beta_events.jsonl").exists(), (
            "a refused low-trust caller wrote a cold_start beta event"
        )

    def test_owner_cold_start_still_records(self, tmp_path, monkeypatch):
        """Control: the owner's cold start still emits its beta event (no
        over-correction that silences owner telemetry)."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")  # private-self owner
        monkeypatch.delenv("ENGRAM_BETA_TRACKING", raising=False)  # default-on
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        _seed_knowledge(e)

        import piia_engram.mcp_server as mcp_server

        mcp_server._engram = e

        result = _run(mcp_server.get_user_context())
        # The owner's rendered context legitimately contains the word
        # "Governance" (in its permissions section), so _is_refusal would
        # false-positive here. Assert the owner got REAL content instead — the
        # dump refusal would not include the user's identity section.
        assert "关于用户" in result, (
            f"owner cold start should return real context, not a refusal: {result!r:.200}"
        )
        assert (engram / "beta_events.jsonl").exists(), (
            "owner cold start should still emit its cold_start beta event; the "
            "gate over-corrected and suppressed owner telemetry"
        )
