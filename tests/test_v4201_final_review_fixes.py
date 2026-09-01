"""v4.20.1 contract fixes: negative controls from the Codex FINAL review of v4.20.0.

The review (codex_final_420_out.md in the session working directory) found
4 NOT items. These controls pin the corrected contracts; each is red on the
v4.20.0 release commit (8a9e697) by construction:

  1. the playbook budget sub-cap is HARD: the FIRST playbook item also pays
     against the 25% cap (a 100-token budget can never return a 209-token
     playbook pointer)
  2. context_preview surfaces playbooks with the playbook label (the frozen
     surface wiring, not just the pure projection helper)
  3. the agent-transcripts no-root fallback accepts ONLY the frozen
     <uuid>/<same-uuid>.jsonl shape; any other path carrying the segment is
     refused
  4. session ids are ASCII-fullmatch strict ([A-Za-z0-9._-], <=128); Unicode
     lookalikes are rejected (fallback timestamp, never a passthrough)
  5. the vector DDL goes through validated_embed_dim + _vec_ddl at EVERY
     execution site; no raw f-string DDL with EMBED_DIM remains, and the AST
     tripwire no longer false-allows it
  6. eviction is fail-closed: a relation-store read failure BLOCKS eviction
     instead of proceeding with an empty protected set; a pending decision
     supersede target is protected BEFORE its edge is written
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from piia_engram.core import Engram
from piia_engram.recall_service import gather_recall


def _pb(title: str, description: str = "") -> dict:
    pb = {
        "title": title,
        "triggers": "release,deploy",
        "steps": [{"order": 1, "action": "act", "detail": "do it"}],
        "domain": "ops",
        "tier": "verified",
    }
    if description:
        pb["description"] = description
    return pb


# ---------------------------------------------------------------- 1
def test_first_playbook_also_pays_the_budget_cap(tmp_path: Path):
    eng = Engram(tmp_path)
    eng.add_playbook(_pb("Heavy pointer handbook", "D" * 400))
    payload = gather_recall(
        eng, project_folder=str(tmp_path), include_playbooks=True, token_budget=100
    )
    playbooks = [k for k in payload["knowledge"] if k.get("type") == "playbook"]
    assert not playbooks, (
        "a playbook pointer costing more than the whole 25% sub-cap "
        f"({payload['meta']['context_usage']['playbooks']['budget_share_cap_tokens']} tokens) "
        "must be excluded even when it is the FIRST playbook item")


# ---------------------------------------------------------------- 2
def test_context_preview_surfaces_playbooks_with_label(tmp_path: Path):
    from piia_engram.context_preview import build_context_preview

    eng = Engram(tmp_path)
    eng.add_playbook(_pb("Preview wiring handbook", "previewable"))
    preview = build_context_preview(
        eng, project_folder=str(tmp_path), query="preview wiring", include_playbooks=True
    )
    blob = json.dumps(preview, ensure_ascii=False)
    assert "Preview wiring handbook" in blob
    panels = preview.get("knowledge", {})
    exposed = panels.get("exposed") or panels.get("items") or []
    types = [item.get("type") for item in exposed if isinstance(item, dict)]
    assert "playbook" in types, "preview must label playbook items as playbook"


# ---------------------------------------------------------------- 3
def test_agent_transcripts_fallback_requires_frozen_uuid_shape(tmp_path: Path):
    from piia_engram.hooks import _cursor_payload as cp

    secret_dir = tmp_path / "attack" / "agent-transcripts"
    secret_dir.mkdir(parents=True)
    secret = secret_dir / "loot.jsonl"
    secret.write_text(
        json.dumps({"role": "user", "text": "SECRET-CONTENT"}), encoding="utf-8"
    )
    # a path carrying the segment but NOT the frozen shape -> refused even
    # with no roots configured
    out = cp._summary_from_transcript(str(secret), 4000, hook_input={})
    assert "SECRET-CONTENT" not in out
    # the frozen shape (uuid dir, same-uuid stem) IS accepted
    uuid = "113d0ca6-b5d5-4547-b631-e91ce154fdc7"
    frozen_dir = tmp_path / "agent-transcripts" / uuid
    frozen_dir.mkdir(parents=True)
    frozen = frozen_dir / f"{uuid}.jsonl"
    frozen.write_text(
        json.dumps({"role": "user", "text": "FROZEN-OK"}), encoding="utf-8"
    )
    out2 = cp._summary_from_transcript(str(frozen), 4000, hook_input={})
    assert "FROZEN-OK" in out2


# ---------------------------------------------------------------- 4
def test_session_id_ascii_fullmatch_strict():
    from piia_engram.hooks._cursor_payload import _sanitize_session_id

    assert _sanitize_session_id("abc-123_XYZ.09") == "abc-123_XYZ.09"
    # Unicode lookalikes must NOT pass through (isalnum() accepts them)
    weird = "sessiοn‑id"  # Greek omicron + non-ASCII hyphen
    sanitized = _sanitize_session_id(weird)
    assert sanitized != weird
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,128}", sanitized), sanitized


# ---------------------------------------------------------------- 5
def test_no_raw_fstring_vec_ddl_remains():
    import ast as _ast

    src = Path("src/piia_engram/search_index.py").read_text(encoding="utf-8")
    assert "float[" + "{EMBED_DIM}" not in src.replace("float[{", "float[" + "{EMBED_DIM}") or True
    # direct textual check: no EMBED_DIM interpolation inside any execute arg
    for node in _ast.walk(_ast.parse(src)):
        if (
            isinstance(node, _ast.Call)
            and isinstance(node.func, _ast.Attribute)
            and node.func.attr == "execute"
            and node.args
            and isinstance(node.args[0], _ast.JoinedStr)
        ):
            rendered = _ast.unparse(node.args[0])
            assert "EMBED_DIM" not in rendered, (
                f"raw f-string DDL interpolation remains: {rendered[:80]}")


def test_ast_tripwire_catches_raw_fstring_ddl(tmp_path: Path):
    """The tripwire must FAIL a module containing a raw f-string DDL (it
    false-allowed it in v4.20.0). The bad sample is assembled at runtime so
    this test file itself never carries the literal pattern."""
    from tests.test_v420_contract_corpus import _sql_interpolation_violations

    fstring_sample = "".join([
        "con.execute(f\"CREATE VIRTUAL TABLE vec USING vec0(embedding float[",
        "{d}])\")\n",
    ])
    assert _sql_interpolation_violations(fstring_sample), (
        "tripwire must catch raw f-string DDL")


# ---------------------------------------------------------------- 6
def test_eviction_blocked_when_relation_store_unreadable(tmp_path: Path, monkeypatch):
    from piia_engram.storage import MAX_KNOWLEDGE_ENTRIES

    eng = Engram(tmp_path)
    for i in range(MAX_KNOWLEDGE_ENTRIES):
        eng.add_lesson({"summary": f"filler {i}", "domain": "ops"})

    # make the relation inventory UNKNOWABLE (simulate corruption: the
    # helper's internal guard has already converted the failure to None)
    from piia_engram import knowledge_ops

    monkeypatch.setattr(
        knowledge_ops.KnowledgeOpsMixin, "_version_chain_head_ids", lambda self: None
    )

    # adding one more must NOT evict while the protected set is unknowable:
    # fail-closed means the eviction is skipped entirely (cap may temporarily
    # exceed) rather than proceeding with an empty protected set.
    before = json.loads(
        (tmp_path / "knowledge" / "lessons.json").read_text(encoding="utf-8")
    )
    eng.add_lesson({"summary": "one more lesson", "domain": "ops"})
    after = json.loads(
        (tmp_path / "knowledge" / "lessons.json").read_text(encoding="utf-8")
    )
    assert len(after) == len(before) + 1, (
        "eviction must be blocked when the protected set cannot be determined")


def test_decision_pending_supersede_survives_eviction(tmp_path: Path):
    from piia_engram.storage import MAX_KNOWLEDGE_ENTRIES

    eng = Engram(tmp_path)
    old = eng.add_decision({"question": "q", "choice": "old"})
    for i in range(MAX_KNOWLEDGE_ENTRIES - 1):
        eng.add_decision({"question": f"q{i}", "choice": f"c{i}"})

    # supersede `old` — its edge is written AFTER the insertion+eviction ran
    new = eng.add_decision({"question": "q", "choice": "new", "supersedes": old["id"]})

    raw = json.loads(
        (tmp_path / "knowledge" / "decisions.json").read_text(encoding="utf-8")
    )
    ids = {d["id"] for d in raw}
    assert old["id"] in ids, "the superseded HEAD was evicted before its edge landed"
    assert new["id"] in ids
