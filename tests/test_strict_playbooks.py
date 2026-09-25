"""Playbook proposals under strict (4.21.1 plan V4 section (v)).

Under strict every agent-created or agent-edited playbook is a pending proposal:
staged at the single insert point, invisible to every agent read surface, never
executable, capped, and decided only by the Owner through the local CLI. Unset
mode keeps 4.21.0 behaviour (pinned). Engram(read_only=True) never bumps playbook
access counters (a 2026-09-25 finding, fixed in both modes).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

from piia_engram.core import Engram

TOKEN = "zqxtokenpendingonly"


def _run(coro):
    return asyncio.run(coro)


def _mcp():
    import piia_engram.mcp_server as mcp_server

    return mcp_server


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "engram"
    (root / "knowledge").mkdir(parents=True)
    (root / "identity").mkdir(parents=True)
    (root / "identity" / "profile.json").write_text(json.dumps({"role": "developer"}), encoding="utf-8")
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    m = _mcp()
    m._engram = Engram(root)
    return m, root


def _strict(monkeypatch):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")


def _pb_rows(root: Path) -> dict[str, dict]:
    out = {}
    for p in (root / "playbooks").glob("*.json"):
        if p.name.startswith("_"):
            continue
        row = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(row, dict) and row.get("id"):
            out[row["id"]] = row
    return out


def _snapshot(root: Path, skip=("audit.log",)) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name not in skip
    }


def _audit(root: Path) -> list[dict]:
    path = root / "audit.log"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _is_strict_refusal(result) -> bool:
    m = _mcp()
    return m._gov_rt.is_governance_refusal(result) and "ENGRAM_APPROVAL=strict" in result


def _add_mcp(m, title="Deploy the docs site", steps=("build the site", "upload the build", "purge the cache"),
             triggers="deploy docs", description="why: the docs deploy is repeated weekly"):
    root = Path(m._engram.root)
    before = set(_pb_rows(root))
    text = _run(m.add_playbook(
        title=title, triggers=triggers, steps_json=json.dumps(list(steps)),
        description=description, domain="type:lesson", user_confirmed=True,
    ))
    try:
        result = json.loads(text)
    except ValueError:
        result = {"text": text}
    created = set(_pb_rows(root)) - before
    if created and not _pid(result):
        result["id"] = created.pop()
    return result


def _seed_verified(m, title="Rotate the log files", steps=("stop writer", "rotate", "start writer")):
    return m._engram.add_playbook({"title": title, "steps": list(steps), "triggers": ["rotate logs"]})


def _pid(result: dict) -> str:
    return str(result.get("id") or result.get("playbook_id") or result.get("existing_id") or "")


# ---------------------------------------------------------------------------
# 1. every agent route stages the playbook; trust fields are ignored
# ---------------------------------------------------------------------------


def test_strict_mcp_add_playbook_is_a_pending_proposal(env, monkeypatch):
    m, root = env
    _strict(monkeypatch)

    result = _add_mcp(m)

    row = _pb_rows(root)[_pid(result)]
    assert row["tier"] == "staging"
    assert row.get("approval_status") == "pending"


def test_strict_memory_store_playbook_is_a_pending_proposal(env, monkeypatch):
    m, root = env
    _strict(monkeypatch)

    _run(m.memory_store(
        kind="playbook", user_confirmed=True,
        content_json=json.dumps({"title": "Clean the build cache", "triggers": "cache",
                                 "steps_json": json.dumps(["find cache", "remove cache", "rebuild"])}),
    ))

    rows = list(_pb_rows(root).values())
    assert [r["tier"] for r in rows] == ["staging"]


_TRUST = {"tier": "verified", "status": "active", "approval_status": "approved",
          "user_confirmed": True, "promotion_reason": "user_confirmed", "promoted_at": "2026-01-01T00:00:00Z"}


def _route_core(m, root, old):
    m._engram.add_playbook({"title": "Core route playbook", "steps": ["one", "two", "three"]})


def _route_mcp(m, root, old):
    _add_mcp(m, title="Mcp route playbook")


def _route_memory_store(m, root, old):
    _run(m.memory_store(kind="playbook", user_confirmed=True, content_json=json.dumps(
        {"title": "Memory store route", "triggers": "x", "steps_json": json.dumps(["a1", "a2", "a3"])})))


def _route_update(m, root, old):
    _run(m.manage_playbook(action="update", playbook_id=old["id"], description="a better why"))


def _route_session_draft(m, root, old):
    m._engram.extract_playbook_from_session(
        "Steps: 1. first build the package, 2. then run the tests, 3. then upload the wheel, "
        "4. finally tag the release."
    )


PLAYBOOK_ROUTES = {
    "core": _route_core,
    "mcp_add_playbook": _route_mcp,
    "memory_store": _route_memory_store,
    "manage_playbook_update": _route_update,
    "session_draft": _route_session_draft,
}


@pytest.mark.parametrize("route", sorted(PLAYBOOK_ROUTES))
def test_strict_every_playbook_route_stages_and_ignores_trust_fields(env, monkeypatch, route):
    m, root = env
    old = _seed_verified(m)
    before = set(_pb_rows(root))
    _strict(monkeypatch)
    real = Engram.add_playbook

    def with_trust(self, playbook, *args, **kwargs):
        return real(self, {**dict(playbook), **_TRUST}, *args, **kwargs)

    monkeypatch.setattr(Engram, "add_playbook", with_trust)

    PLAYBOOK_ROUTES[route](m, root, old)

    new_rows = [r for pid, r in _pb_rows(root).items() if pid not in before and r.get("tier") == "staging"]
    created = [r for pid, r in _pb_rows(root).items() if pid not in before]
    if route == "manage_playbook_update":
        created = [r for r in created if r.get("pending_supersedes")]
    assert created, f"route {route} created no playbook"
    for row in created:
        assert row["tier"] == "staging", route
        assert row.get("approval_status") == "pending", route
        assert "promotion_reason" not in row and "promoted_at" not in row, route
        assert row.get("user_confirmed") is not True, route
    assert new_rows


# ---------------------------------------------------------------------------
# 2. manage_playbook under strict
# ---------------------------------------------------------------------------


def test_strict_update_is_a_full_merged_proposal_and_the_old_row_stays(env, monkeypatch):
    m, root = env
    old = _seed_verified(m)
    old_bytes = (root / "playbooks" / f"{old['id']}.json").read_bytes()
    _strict(monkeypatch)

    _run(m.manage_playbook(action="update", playbook_id=old["id"], description=f"new why {TOKEN}"))

    assert (root / "playbooks" / f"{old['id']}.json").read_bytes() == old_bytes
    (proposal,) = [r for r in _pb_rows(root).values() if r.get("pending_supersedes") == old["id"]]
    assert proposal["tier"] == "staging"
    assert proposal["title"] == old["title"]
    assert len(proposal["steps"]) == len(old["steps"])
    assert TOKEN in proposal["description"]


def test_strict_update_with_a_status_argument_is_refused_and_audited(env, monkeypatch):
    m, root = env
    old = _seed_verified(m)
    _strict(monkeypatch)
    before = _snapshot(root)

    result = _run(m.manage_playbook(action="update", playbook_id=old["id"], status="active"))

    assert _is_strict_refusal(result)
    assert _snapshot(root) == before
    assert any("field: status" in json.dumps(a) or "field=status" in json.dumps(a) for a in _audit(root))


@pytest.mark.parametrize("action", ["archive", "delete", "restore"])
def test_strict_archive_delete_restore_are_refused(env, monkeypatch, action):
    m, root = env
    old = _seed_verified(m)
    _strict(monkeypatch)
    before = _snapshot(root)

    result = _run(m.manage_playbook(action=action, playbook_id=old["id"], dry_run=False, confirm=True))

    assert _is_strict_refusal(result)
    assert _snapshot(root) == before


# ---------------------------------------------------------------------------
# 3. pending is invisible and not executable under strict
# ---------------------------------------------------------------------------


def _pending_with_token(m) -> str:
    return _pid(_add_mcp(m, title=f"Pending procedure {TOKEN}",
                         steps=(f"step one {TOKEN}", "step two", "step three"), triggers=f"trigger {TOKEN}"))


def test_strict_get_playbooks_never_returns_pending(env, monkeypatch):
    m, root = env
    _seed_verified(m)
    _strict(monkeypatch)
    pid = _pending_with_token(m)

    core_ids = {p["id"] for p in m._engram.get_playbooks(limit=None)}
    assert pid not in core_ids
    for mode in ("list", "recent", "management"):
        text = _run(m.get_playbooks(mode=mode, status="all"))
        assert TOKEN not in text, mode
    assert TOKEN not in _run(m.get_playbooks(mode="get", playbook_id=pid))


def test_strict_pending_id_is_not_executable(env, monkeypatch):
    m, root = env
    _strict(monkeypatch)
    pid = _pending_with_token(m)
    executions = root / "playbooks" / "executions"
    before = sorted(p.name for p in executions.glob("*")) if executions.exists() else []

    result = json.loads(_run(m.playbook_execution(action="prepare", playbook_id=pid)))

    assert result.get("status") == "pending_not_executable"
    after = sorted(p.name for p in executions.glob("*")) if executions.exists() else []
    assert after == before


def test_strict_old_id_runs_the_old_content_after_an_update_proposal(env, monkeypatch):
    m, root = env
    old = _seed_verified(m)
    _strict(monkeypatch)
    _run(m.manage_playbook(action="update", playbook_id=old["id"],
                           steps_json=json.dumps([f"new step {TOKEN}", "b", "c"])))

    text = _run(m.playbook_execution(action="prepare", playbook_id=old["id"]))

    assert TOKEN not in text
    assert "stop writer" in text


def test_strict_search_drops_pending_playbooks_in_every_scope(env, monkeypatch):
    m, root = env
    _strict(monkeypatch)
    _pending_with_token(m)

    for scope in ("all", "playbooks"):
        assert TOKEN not in _run(m.search_knowledge(query="Pending procedure step one", scope=scope)), scope


_SWEEP_SKIP: set[str] = set()  # A5: export_engram is swept too; strict MCP exports skip pending


def _sweep_tools() -> list[str]:
    m = _mcp()
    names = [n for n, c in m.TOOL_GOVERNANCE_CLASS.items() if c == "read"]
    names += ["get_playbooks", "search_knowledge", "export_engram", "export_knowledge_report", "get_identity_card",
              "refresh_quick_context", "request_outline_review"]
    return sorted(set(names) - _SWEEP_SKIP)


def _sweep_kwargs(func) -> dict:
    import inspect

    kwargs = {}
    for name, param in inspect.signature(func).parameters.items():
        if name in ("query", "topic", "text", "question", "keyword", "q"):
            # words of the pending playbook, never the token itself, so a tool that
            # echoes its query cannot produce a false leak
            kwargs[name] = "Pending procedure step one trigger"
            continue
        if param.default is not inspect.Parameter.empty:
            continue
        ann = param.annotation
        kwargs[name] = 1 if ann is int else False if ann is bool else 1.0 if ann is float else "x"
    return kwargs


@pytest.mark.parametrize("tool_name", _sweep_tools())
def test_strict_token_leak_sweep(env, monkeypatch, tool_name):
    m, root = env
    _strict(monkeypatch)
    _pending_with_token(m)
    func = getattr(m, tool_name)

    try:
        response = _run(func(**_sweep_kwargs(func)))
    except Exception as exc:  # a dummy argument may be rejected
        response = str(exc)

    assert TOKEN not in str(response), f"{tool_name} leaked a pending playbook"
    for derived in ("quick_context.md", "exports", "contexts"):
        path = root / derived
        files = [path] if path.is_file() else list(path.rglob("*")) if path.exists() else []
        for f in files:
            if f.is_file():
                assert TOKEN not in f.read_text(encoding="utf-8", errors="replace"), f"{tool_name} -> {f}"


# ---------------------------------------------------------------------------
# 4. capacity: own cap, refuse, never drop
# ---------------------------------------------------------------------------


def test_strict_playbook_cap_refuses_and_audits(env, monkeypatch):
    m, root = env
    _strict(monkeypatch)
    monkeypatch.setenv("ENGRAM_PLAYBOOK_QUEUE_MAX", "2")
    _add_mcp(m, title="Alpha procedure", steps=("a", "b", "c"))
    _add_mcp(m, title="Bravo workflow", steps=("d", "e", "f"))

    third = _add_mcp(m, title="Charlie routine", steps=("g", "h", "i"))

    assert third.get("status") == "queue_full"
    assert len(_pb_rows(root)) == 2
    assert any("queue_full" in json.dumps(a) for a in _audit(root))


def test_strict_same_pending_proposal_returns_the_existing_id(env, monkeypatch):
    m, root = env
    _strict(monkeypatch)
    first = _add_mcp(m)

    again = _add_mcp(m)

    assert _pid(again) == _pid(first)
    assert len(_pb_rows(root)) == 1


def test_strict_session_draft_at_the_cap_does_not_raise(env, monkeypatch):
    m, root = env
    _strict(monkeypatch)
    monkeypatch.setenv("ENGRAM_PLAYBOOK_QUEUE_MAX", "1")
    _add_mcp(m, title="Alpha procedure", steps=("a", "b", "c"))

    m._engram.extract_playbook_from_session(
        "Steps: 1. first build the package, 2. then run the tests, 3. then upload the wheel."
    )

    assert len(_pb_rows(root)) == 1


# ---------------------------------------------------------------------------
# 5. Owner decisions through the CLI
# ---------------------------------------------------------------------------


def _cli(monkeypatch, capsys, *argv: str) -> tuple[int, str]:
    from piia_engram import setup_wizard

    monkeypatch.setattr(sys, "argv", ["engram", *argv])
    with pytest.raises(SystemExit) as exc:
        setup_wizard.main()
    return int(exc.value.code or 0), capsys.readouterr().out


def _marks(tmp_path: Path, marks: list[dict]) -> Path:
    path = tmp_path / "marks.json"
    path.write_text(json.dumps(marks), encoding="utf-8")
    return path


def test_cli_approve_makes_a_pending_playbook_visible_and_executable(env, monkeypatch, capsys, tmp_path):
    m, root = env
    _strict(monkeypatch)
    pid = _pending_with_token(m)

    code, _ = _cli(monkeypatch, capsys, "review", "apply", str(_marks(tmp_path, [{"id": pid, "mark": "approve"}])),
                   "--operator", "owner", "--yes")

    assert code == 0
    assert _pb_rows(root)[pid]["tier"] == "verified"
    assert pid in {p["id"] for p in m._engram.get_playbooks(limit=None)}
    assert json.loads(_run(m.playbook_execution(action="prepare", playbook_id=pid))).get("status") != \
        "pending_not_executable"


def test_cli_approving_an_update_proposal_retires_the_old_row(env, monkeypatch, capsys, tmp_path):
    m, root = env
    old = _seed_verified(m)
    _strict(monkeypatch)
    _run(m.manage_playbook(action="update", playbook_id=old["id"], description="better"))
    (proposal,) = [r for r in _pb_rows(root).values() if r.get("pending_supersedes") == old["id"]]

    _cli(monkeypatch, capsys, "review", "apply", str(_marks(tmp_path, [{"id": proposal["id"], "mark": "approve"}])),
         "--operator", "owner", "--yes")

    rows = _pb_rows(root)
    assert rows[proposal["id"]]["tier"] == "verified"
    assert rows[old["id"]]["status"] != "active"


def test_cli_reject_tombstones_and_blocks_the_same_procedure(env, monkeypatch, capsys, tmp_path):
    m, root = env
    _strict(monkeypatch)
    pid = _pid(_add_mcp(m))

    _cli(monkeypatch, capsys, "review", "apply", str(_marks(tmp_path, [{"id": pid, "mark": "reject"}])),
         "--operator", "owner", "--yes")

    stones = [json.loads(l) for l in (root / "knowledge" / "tombstones.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(s["id"], s["kind"]) for s in stones] == [(pid, "playbook")]
    assert _pb_rows(root)[pid]["status"] != "active"
    assert _add_mcp(m).get("status") == "rejected_before"


def test_cli_retire_and_restore_marks(env, monkeypatch, capsys, tmp_path):
    m, root = env
    old = _seed_verified(m)
    _strict(monkeypatch)

    _cli(monkeypatch, capsys, "review", "apply", str(_marks(tmp_path, [{"id": old["id"], "mark": "retire"}])),
         "--operator", "owner", "--yes")
    assert _pb_rows(root)[old["id"]]["status"] != "active"
    assert not (root / "knowledge" / "tombstones.jsonl").exists()

    _cli(monkeypatch, capsys, "review", "apply", str(_marks(tmp_path, [{"id": old["id"], "mark": "restore"}])),
         "--operator", "owner", "--yes")
    assert _pb_rows(root)[old["id"]]["status"] == "active"


def test_cli_playbook_edit_type_accepts_only_three_types(env, monkeypatch, capsys, tmp_path):
    m, root = env
    _strict(monkeypatch)
    pid = _pid(_add_mcp(m))
    before = _snapshot(root)

    code, _ = _cli(monkeypatch, capsys, "review", "apply",
                   str(_marks(tmp_path, [{"id": pid, "mark": "edit-type:preference"}])), "--operator", "owner", "--yes")

    assert code != 0
    assert _snapshot(root) == before


def test_cli_export_shows_full_steps_flags_and_the_cap(env, monkeypatch, capsys, tmp_path):
    m, root = env
    _strict(monkeypatch)
    pid = _pending_with_token(m)
    m._engram.add_playbook({"title": "No rationale procedure", "steps": ["x1", "x2", "x3"]})
    out = tmp_path / "out"

    code, _ = _cli(monkeypatch, capsys, "review", "export", "--out", str(out))

    text = (out / "review.md").read_text(encoding="utf-8")
    assert code == 0
    assert f"step one {TOKEN}" in text and "step three" in text
    assert "pending playbooks 2/10" in text
    assert "missing rationale" in text
    assert pid in json.loads((out / "ids.json").read_text(encoding="utf-8"))


def test_cli_playbook_list_staging_is_read_only(env, monkeypatch, capsys):
    m, root = env
    _strict(monkeypatch)
    pid = _pending_with_token(m)
    before = _snapshot(root, skip=())

    code, out = _cli(monkeypatch, capsys, "playbook", "list", "--tier", "staging")

    assert code == 0 and pid in out
    assert _snapshot(root, skip=()) == before


# ---------------------------------------------------------------------------
# 6. unset pins (4.21.0) and the read_only fix
# ---------------------------------------------------------------------------


def test_unset_add_playbook_writes_verified(env):
    m, root = env

    result = _add_mcp(m)

    assert _pb_rows(root)[_pid(result)]["tier"] == "verified"


def test_unset_staging_draft_stays_visible_and_executable(env):
    m, root = env
    draft = m._engram.add_playbook({"title": "Draft procedure", "steps": ["a", "b", "c"], "tier": "staging"})

    assert draft["id"] in {p["id"] for p in m._engram.get_playbooks(limit=None)}
    result = json.loads(_run(m.playbook_execution(action="prepare", playbook_id=draft["id"])))
    assert result.get("status") != "pending_not_executable"


@pytest.mark.parametrize("mode", ["strict", None])
def test_read_only_get_playbooks_leaves_the_store_byte_identical(env, monkeypatch, mode):
    m, root = env
    _seed_verified(m)
    if mode:
        _strict(monkeypatch)
    before = _snapshot(root, skip=())

    Engram(root, read_only=True).get_playbooks(limit=None)

    assert _snapshot(root, skip=()) == before


def test_unset_export_engram_is_complete(env):
    m, root = env
    draft = m._engram.add_playbook({"title": f"Draft {TOKEN}", "steps": ["a", "b", "c"], "tier": "staging"})

    path = m._engram.export_all(str(root.parent / "full.json"))

    assert TOKEN in Path(path).read_text(encoding="utf-8")
    assert draft["id"]
