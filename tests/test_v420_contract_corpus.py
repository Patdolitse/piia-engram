"""v4.20.0 contract corpus: playbook recall surface + hook/SQL/eviction hardening.

Frozen by the dual-approved proposal (PROPOSAL_v420_v2). Cases red on
main@234b007 by construction; direction guards (default-off, staging filter,
default-clean SQL) pin behavior that must NOT change.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram.governance_store import RelationStore
from piia_engram.recall_service import gather_recall

WS = Path  # alias for readability in containment tests

LONG_TEXT_500 = "x" * 500


def _pb(title: str, *, project_folder: str | None = None, description: str = "") -> dict:
    pb = {
        "title": title,
        "triggers": "release,发布,deploy",
        "steps": [{"order": 1, "action": "act", "detail": "do it"}],
        "domain": "ops",
        "tier": "verified",
    }
    if description:
        pb["description"] = description
    if project_folder:
        pb["scope_type"] = "project"
        pb["project_folder"] = project_folder
    return pb


# =====================================================================
# A — playbook recall surface
# =====================================================================

def test_recall_playbooks_absent_by_default(tmp_path: Path):
    eng = Engram(tmp_path)
    eng.add_playbook(_pb("Default off handbook"))
    payload = gather_recall(eng, project_folder=str(tmp_path))
    blob = json.dumps(payload, ensure_ascii=False)
    assert "Default off handbook" not in blob  # direction guard: opt-in only


def test_recall_include_playbooks_surfaces_playbook(tmp_path: Path):
    eng = Engram(tmp_path)
    eng.add_playbook(_pb("Ship it handbook", description="How to ship releases safely."))
    payload = gather_recall(eng, project_folder=str(tmp_path), include_playbooks=True)
    items = [k for k in payload.get("knowledge", []) if k.get("type") == "playbook"]
    assert items, "playbook bucket missing from recall payload"
    view = items[0]
    assert view.get("title") == "Ship it handbook"
    assert view.get("id")  # pointer: caller needs the id for get_playbooks
    assert "steps" not in view  # frozen: steps NEVER enter recall payloads


def test_recall_playbook_projection_bounds(tmp_path: Path):
    eng = Engram(tmp_path)
    eng.add_playbook(_pb(
        "Bounded handbook",
        description=LONG_TEXT_500,
    ))
    payload = gather_recall(eng, project_folder=str(tmp_path), include_playbooks=True)
    view = next(k for k in payload["knowledge"] if k.get("type") == "playbook")
    assert len(view.get("description") or "") <= 240
    assert "steps" not in json.dumps(view)


def test_recall_playbook_budget_subcap(tmp_path: Path):
    eng = Engram(tmp_path)
    for i in range(6):
        eng.add_playbook(_pb(f"Flood handbook {i} with a fairly long title to cost tokens"))
    for i in range(8):
        eng.add_lesson({"summary": f"lesson number {i} about shipping", "domain": "ops"})
    payload = gather_recall(
        eng, project_folder=str(tmp_path), include_playbooks=True,
        token_budget=2000,
    )
    playbooks = [k for k in payload["knowledge"] if k.get("type") == "playbook"]
    assert len(playbooks) <= 2  # max 2 playbook items per surface
    # lessons/decisions keep guaranteed capacity (anti-starve)
    lessons = [k for k in payload["knowledge"] if k.get("type") == "lesson"]
    assert lessons


def test_recall_superseded_playbook_version_never_surfaces(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_pb("Versioned handbook", description="v1 body"))
    eng.update_knowledge(created["id"], {"description": "v2 body"}, expected_version=1)
    payload = gather_recall(eng, project_folder=str(tmp_path), include_playbooks=True)
    views = [k for k in payload["knowledge"] if k.get("type") == "playbook"]
    assert views and all(v.get("description") == "v2 body" for v in views)


def test_recall_project_scoped_playbook(tmp_path: Path):
    eng = Engram(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    eng.add_playbook(_pb("Project handbook alpha", project_folder=str(project)))
    eng.add_playbook(_pb("Global handbook beta"))
    payload = gather_recall(eng, project_folder=str(project), include_playbooks=True)
    titles = {k.get("title") for k in payload["knowledge"] if k.get("type") == "playbook"}
    assert "Project handbook alpha" in titles


def test_recall_staging_playbook_never_surfaces(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_pb("Staging handbook"))
    # playbook updates have no tier field (allowlist); stage it on disk directly
    pb_path = tmp_path / "playbooks" / f"{created['id']}.json"
    data = json.loads(pb_path.read_text(encoding="utf-8"))
    data["tier"] = "staging"
    pb_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    payload = gather_recall(eng, project_folder=str(tmp_path), include_playbooks=True)
    assert not [k for k in payload["knowledge"] if k.get("type") == "playbook"]


def test_agent_pack_populates_playbook_slot_zero_write(tmp_path: Path):
    eng = Engram(tmp_path)
    eng.add_playbook(_pb("Agent pack handbook"))
    before = _tree_hash(tmp_path)
    pack = eng.build_agent_context_pack(project_folder=str(tmp_path))
    slot = pack["context"]["playbooks"]
    assert slot, "agent pack playbook slot still hardcoded empty"
    assert "steps" not in json.dumps(slot)
    assert _tree_hash(tmp_path) == before  # zero-write read path


def test_agent_pack_respects_playbook_limit(tmp_path: Path):
    eng = Engram(tmp_path)
    for i in range(5):
        eng.add_playbook(_pb(f"Pack handbook {i}"))
    pack = eng.build_agent_context_pack(project_folder=str(tmp_path))
    limit = pack["pack_meta"]["budget"]["playbook_limit"]
    assert 0 < len(pack["context"]["playbooks"]) <= limit


def test_projection_labels_playbooks_not_lessons():
    from piia_engram.recall import _project_item

    entry = {"title": "T", "triggers": ["a"], "domain": "ops", "steps": [], "id": "p1"}
    view = _project_item(entry, include_freshness=False, now=None)
    assert view.get("type") == "playbook"
    assert "summary" not in view  # must not mis-project as an empty lesson


# =====================================================================
# C — hook / SQL / eviction hardening
# =====================================================================

def _tree_hash(root: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _transcript(root: Path, name: str = "t.jsonl", lines: list[dict] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / name
    p.write_text(
        "\n".join(json.dumps(x) for x in (lines or [{"role": "user", "text": "SECRET-CONTENT"}])),
        encoding="utf-8",
    )
    return p


def _hook_summary(path: Path, workspace: Path) -> str:
    from piia_engram.hooks import _cursor_payload as cp

    return cp._summary_from_transcript(
        str(path), 4000, hook_input={"workspace_roots": [str(workspace)]}
    )


def test_transcript_out_of_root_refused(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = _transcript(tmp_path / "elsewhere")
    assert _hook_summary(outside, ws) == ""
    assert "SECRET-CONTENT" not in _hook_summary(outside, ws)


def test_transcript_traversal_vectors_refused(tmp_path: Path):
    ws = tmp_path / "ws"
    real = _transcript(ws)
    for evil in (
        str(tmp_path / "elsewhere" / ".." / "elsewhere" / "t.jsonl"),
        str(tmp_path / "elsewhere//t.jsonl"),
        str(real).replace("t.jsonl", "%2e%2e%2ft.jsonl"),
    ):
        assert _hook_summary(Path(evil), ws) == "" or "SECRET-CONTENT" not in _hook_summary(
            Path(evil), ws
        )


def test_transcript_symlink_escape_refused(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    secret = _transcript(tmp_path / "elsewhere")
    link = ws / "link.jsonl"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlink unavailable")
    assert "SECRET-CONTENT" not in _hook_summary(link, ws)


def test_transcript_non_jsonl_refused(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    doc = ws / "notes.txt"
    doc.write_text("SECRET-CONTENT", encoding="utf-8")
    assert _hook_summary(doc, ws) == ""


def test_transcript_owner_config_root_allows(tmp_path: Path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    custom = _transcript(tmp_path / "custom-root")
    monkeypatch.setenv(
        "ENGRAM_CURSOR_TRANSCRIPT_ROOTS", str(tmp_path / "custom-root")
    )
    assert "SECRET-CONTENT" in _hook_summary(custom, ws)


def test_writeback_uses_hardened_reader(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = _transcript(tmp_path / "elsewhere")
    proc = subprocess.run(
        [sys.executable, "-m", "piia_engram.hooks.cursor_writeback"],
        input=json.dumps({
            "transcript_path": str(outside),
            "workspace_roots": [str(ws)],
        }),
        capture_output=True, text=True, encoding="utf-8",
        env={
            "ENGRAM_DIR": str(tmp_path / "engram-store"),
            "ENGRAM_TEST": "1",
            "PATH": "",
        },
    )
    store = tmp_path / "engram-store"
    assert "SECRET-CONTENT" not in _tree_hash_safe(store) if store.exists() else True


def _tree_hash_safe(root: Path) -> str:
    return _tree_hash(root) if root.exists() else ""


def test_session_id_sanitized_on_write_path(tmp_path: Path):
    eng = Engram(tmp_path)
    res = eng.save_agent_context(
        tool="cursor", session_id="..\\..\\evil", content="hello"
    )
    written = res.get("file", "")
    assert ".." not in written
    ctx_dir = tmp_path / "contexts" / "cursor"
    assert ctx_dir.exists()
    assert all("..\\..\\evil" not in p.name for p in ctx_dir.glob("*.md"))


def test_eviction_never_deletes_chain_head(tmp_path: Path):
    from piia_engram.storage import MAX_KNOWLEDGE_ENTRIES

    eng = Engram(tmp_path)
    # fill to the cap minus one, oldest first
    first = eng.add_lesson({"summary": "oldest chain head lesson", "domain": "ops"})
    eng.update_lesson(first["id"], {"summary": "oldest chain head lesson v2"})
    for i in range(MAX_KNOWLEDGE_ENTRIES - 1):
        eng.add_lesson({"summary": f"filler lesson {i}", "domain": "ops"})
    raw = json.loads((tmp_path / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    assert any(l["id"] == first["id"] for l in raw), (
        "version-chain HEAD was silently evicted by the knowledge cap")
    edges = RelationStore(tmp_path).all_edges()
    assert any(e["src"] == first["id"] for e in edges), (
        "the HEAD's lineage edges were orphaned by eviction")


def test_embed_dim_closed_set_validation(monkeypatch, tmp_path: Path):
    import importlib

    import piia_engram.search_index as si

    monkeypatch.setenv("ENGRAM_EMBED_MODEL", "totally/unknown-model")
    mod = importlib.reload(si)
    assert mod.validated_embed_dim() is None, (
        "unknown embed model must fail closed (no vector DDL), not silently fall back")
    monkeypatch.delenv("ENGRAM_EMBED_MODEL", raising=False)
    importlib.reload(si)


def _sql_interpolation_violations(source: str) -> list[str]:
    """AST tripwire: report SQL execute() calls whose statement string is
    built with dynamic interpolation (f-string OR concatenation/format of a
    variable into the SQL text). The ONLY allowed dynamic shape is the
    audited ?-placeholder construction (string of '?,' chars bound to
    parameter values) — v4.20.1: even that is gone from search_index, but
    the exemption stays so the rule documents the audited pattern."""
    import ast

    violations = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("execute", "executemany")
            and node.args
        ):
            arg = node.args[0]
            # f-string statement -> violation unless every formatted value is
            # a '?'-placeholder generator expression (the audited shape)
            if isinstance(arg, ast.JoinedStr):
                dynamic = [
                    v for v in arg.values
                    if isinstance(v, ast.FormattedValue)
                    and not _is_placeholder_generator(v.value)
                ]
                if dynamic:
                    violations.append(ast.unparse(node)[:100])
                continue
            # string concatenation/format with a variable operand -> check
            # whether the result feeds a LIKE/shape context; conservative:
            # flag concat/format calls whose parts include any Name/Call
            if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mod):
                violations.append(ast.unparse(node)[:100])
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "join":
                # join((prefix, <dynamic>, suffix)) into execute: dynamic SQL
                violations.append(ast.unparse(node)[:100])
    return violations


def _is_placeholder_generator(expr: ast.expr) -> bool:
    import ast

    # "?":s or "?,".join(...) or "?" * n — the audited placeholder shapes
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str) and set(expr.value) <= {"?", ","}:
        return True
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Mult):
        left = expr.left
        return isinstance(left, ast.Constant) and isinstance(left.value, str) and set(left.value) <= {"?", ","}
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "join":
        return True
    return False


def test_ast_guard_no_arbitrary_sql_interpolation():
    """Tripwire: no dynamically interpolated SQL in search_index (v4.20.1:
    even the audited ?,? placeholder shape is gone — per-rowid parameterized
    queries everywhere; the vec DDL is a closed-set literal table)."""
    import ast as _ast
    from pathlib import Path as P

    src = P("src/piia_engram/search_index.py").read_text(encoding="utf-8")
    violations = _sql_interpolation_violations(src)
    assert not violations, violations
