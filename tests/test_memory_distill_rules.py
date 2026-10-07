"""Rules for distilling memories: what to keep, relative dates, and revising an
entry with ``supersedes`` instead of adding an unrelated one.

* both served instruction texts (default and strict) carry the rules;
* every injected snippet carries them, and a default snippet Engram shipped
  before is refreshed while a block the Owner edited is never touched;
* a revision that names an id that does not exist is refused on every write path.

Every test points HOME, USERPROFILE, APPDATA, LOCALAPPDATA and ENGRAM_DIR at
tmp_path; nothing here reads or writes a real home or store.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)
from piia_engram import doctor, mcp_server
from piia_engram.core import Engram
from piia_engram.setup_wizard import (
    _INSTRUCTION_MARKER,
    _INSTRUCTION_MARKER_END,
    _INSTRUCTION_SNIPPETS,
    _KNOWN_DEFAULT_SNIPPET_FINGERPRINTS,
    _inject_instruction_snippet,
    _instruction_snippet_state,
    _instruction_snippet_text,
    _snippet_fingerprint,
    _snippet_inner,
)

_TOOLS = ("claude_code", "codex", "windsurf", "cursor")

# The strict (read + propose) English body as shipped in 4.21.2.
_STRICT_EN_4212 = (
    "## Engram Memory Layer (strict mode: read + propose)\n\n"
    "Piia Engram is installed and runs in strict mode: anything an AI writes is only a "
    "proposal, and it takes effect after the Owner approves it with the local "
    "`engram review` command.\n\n"
    "- Session start: when resuming earlier work, call `get_resume_brief`; for past "
    "preferences or cross-session continuity, call `get_user_context` or `search_knowledge`. "
    "New tasks, explicit instructions and plain technical questions need neither.\n"
    "- Propose only what is worth keeping, one claim per row: run `search_knowledge` first so "
    "you do not propose a duplicate; label the type in domain (type:rule, type:preference, "
    "type:project_fact, type:lesson or type:decision) and say in detail why it is worth keeping.\n"
    "- Do not propose session logs, progress notes, checkpoints, or anything already in files "
    "or git; keep those in the project's own notes.\n"
    "- Do not use `wrap_up_session`, `extract_session_insights` or `save_agent_context` as an "
    "auto-save.\n"
    "- Approving, rejecting, editing, merging, importing and identity changes are the Owner's, "
    "done locally; MCP refuses them.\n"
)

# The default Codex English block as shipped in 4.21.2.
_CODEX_EN_4212 = (
    f"\n{_INSTRUCTION_MARKER}\n"
    "## Engram Memory Layer\n\n"
    "Piia Engram (MCP memory layer) is installed.\n\n"
    "- Session start: call `get_resume_brief` to resume the previous session (cross-tool continuity)\n"
    "- First time / new project: call `get_user_context` to learn user identity and preferences\n"
    "- Lessons learned: call `add_lesson`\n"
    "- Decisions made: call `add_decision`\n"
    "- Task end: call `wrap_up_session` to save context\n"
    "- User asks about past conversations (\"what did I just ask\", \"where did we leave off\"): "
    "call `get_recent_context`\n"
    f"{_INSTRUCTION_MARKER_END}\n"
)

# An Owner-written block inside the Engram markers that mentions get_resume_brief.
_OWNER_BLOCK = (
    f"{_INSTRUCTION_MARKER}\n"
    "## Engram (my own strict rules)\n\n"
    "Read: call `get_resume_brief` when resuming earlier work.\n"
    "Write: proposals only, at most three per session.\n"
    f"{_INSTRUCTION_MARKER_END}\n"
)

# Fingerprints of the 4.21.2 strict snippets (zh/en; marked block and Cursor .mdc).
_STRICT_4212_FINGERPRINTS = {
    "d185faf1cc61b62230caaf247d0bba4403dd4f813a5d91a57dc4bdba8d7db005",
    "8661550f037b83c20ec053954ecec6c05676a9583ea381bfa93888070234078d",
    "a92d0ad53a563a6ba04d7d51c773005eed8d1a3416327d209c80dd1038a25717",
    "477fd57e28d527a896652d2c63e73edf42a8385ef410b829b32c59d96678ae59",
}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(h))
    monkeypatch.setenv("APPDATA", str(h / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(h / "AppData" / "Local"))
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("ENGRAM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ENGRAM_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("ENGRAM_NO_AUTO_BACKUP", "1")
    for var in ("ENGRAM_APPROVAL", "ENGRAM_RECONCILE", "ENGRAM_GOVERNANCE", "ENGRAM_CLIENT_TYPE"):
        monkeypatch.delenv(var, raising=False)
    return h


def _point(monkeypatch, tool_id: str, path: Path) -> Path:
    monkeypatch.setitem(_INSTRUCTION_SNIPPETS[tool_id], "path_fn", lambda _home: path)
    return path


def _latch_strict(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "approval_mode.json").write_text(
        '{"strict_first_seen_at": "2026-09-26T00:00:00Z"}', encoding="utf-8")


def _has_rules_en(text: str) -> None:
    assert "to-dos" in text and "temporary state" in text
    assert "(date unknown)" in text
    assert "supersedes=<old id>" in text


def _has_rules_zh(text: str) -> None:
    assert "待办" in text and "临时状态" in text
    assert "日期未定" in text
    assert "supersedes=<旧 id>" in text


# ---------------------------------------------------------------------------
# 1. served instructions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text_name", ["_DEFAULT_SERVER_INSTRUCTIONS", "_STRICT_SERVER_INSTRUCTIONS"])
def test_both_served_instructions_carry_the_distill_rules(text_name):
    text = getattr(mcp_server, text_name)
    _has_rules_en(text)
    assert "search_knowledge" in text
    # The version hint for changing an entry is said once, not repeated by the new rules.
    assert text.count("supersedes_expected_version") == 1


def test_strict_instructions_say_a_revision_waits_for_review():
    text = mcp_server._STRICT_SERVER_INSTRUCTIONS
    rule = next(line for line in text.splitlines() if "supersedes=<old id>" in line)
    assert "review" in rule or "review" in text.split(rule, 1)[1].splitlines()[0]


# ---------------------------------------------------------------------------
# 2. injected snippets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_id", _TOOLS)
@pytest.mark.parametrize("strict", [False, True])
def test_every_default_snippet_carries_the_rules(tool_id, strict):
    _has_rules_zh(_instruction_snippet_text(tool_id, "zh", strict=strict))
    _has_rules_en(_instruction_snippet_text(tool_id, "en", strict=strict))
    assert "get_resume_brief" in _instruction_snippet_text(tool_id, "en", strict=strict)


def test_every_snippet_shipped_in_4212_is_a_known_default():
    assert _STRICT_4212_FINGERPRINTS <= _KNOWN_DEFAULT_SNIPPET_FINGERPRINTS
    assert _snippet_fingerprint(_snippet_inner("codex", _CODEX_EN_4212)) in _KNOWN_DEFAULT_SNIPPET_FINGERPRINTS
    assert _snippet_fingerprint(_STRICT_EN_4212) in _KNOWN_DEFAULT_SNIPPET_FINGERPRINTS


def test_new_defaults_are_new_text():
    for tool_id in _TOOLS:
        for strict in (False, True):
            for lang in ("zh", "en"):
                fp = _snippet_fingerprint(_snippet_inner(
                    tool_id, _instruction_snippet_text(tool_id, lang, strict=strict)))
                assert fp not in _KNOWN_DEFAULT_SNIPPET_FINGERPRINTS, (tool_id, strict, lang)


def test_old_default_block_is_refreshed(home, monkeypatch):
    target = _point(monkeypatch, "codex", home / "AGENTS.md")
    target.write_text("# codex rules\n" + _CODEX_EN_4212, encoding="utf-8")
    assert _instruction_snippet_state("codex", target.read_text(encoding="utf-8")) == "stale_default"

    assert _inject_instruction_snippet("codex", lang="en") is not None

    text = target.read_text(encoding="utf-8")
    assert text.startswith("# codex rules\n")
    assert text.count(_INSTRUCTION_MARKER) == 1
    _has_rules_en(text)


def test_old_strict_block_is_refreshed_under_strict(home, tmp_path, monkeypatch):
    _latch_strict(tmp_path / "store")
    target = _point(monkeypatch, "claude_code", home / "CLAUDE.md")
    old = f"# mine\n\n{_INSTRUCTION_MARKER}\n{_STRICT_EN_4212}{_INSTRUCTION_MARKER_END}\n"
    target.write_bytes(old.replace("\n", "\r\n").encode("utf-8"))

    assert _inject_instruction_snippet("claude_code", lang="en") is not None

    text = target.read_text(encoding="utf-8")
    assert "# mine" in text and text.count(_INSTRUCTION_MARKER) == 1
    assert "strict mode" in text
    _has_rules_en(text)


def test_old_strict_cursor_rule_is_refreshed(home, tmp_path, monkeypatch):
    _latch_strict(tmp_path / "store")
    target = _point(monkeypatch, "cursor", home / "engram.mdc")
    old_header = (
        "---\ndescription: Engram memory layer (strict mode) — AI reads and proposes, the Owner approves\n"
        "globs:\nalwaysApply: true\n---\n\n"
    )
    target.write_text(old_header + _STRICT_EN_4212, encoding="utf-8")

    assert _inject_instruction_snippet("cursor", lang="en") is not None
    _has_rules_en(target.read_text(encoding="utf-8"))


@pytest.mark.parametrize("strict", [False, True])
def test_owner_block_with_resume_brief_is_never_touched(home, tmp_path, monkeypatch, strict):
    if strict:
        _latch_strict(tmp_path / "store")
    target = _point(monkeypatch, "claude_code", home / "CLAUDE.md")
    original = ("# global\r\n\r\n" + _OWNER_BLOCK.replace("\n", "\r\n")).encode("utf-8")
    target.write_bytes(original)

    assert _inject_instruction_snippet("claude_code", lang="zh") is None
    assert _inject_instruction_snippet("claude_code", lang="en") is None
    assert target.read_bytes() == original


def test_no_snippet_appends_the_new_default(home, monkeypatch):
    target = _point(monkeypatch, "codex", home / "AGENTS.md")
    target.write_text("# codex rules\n", encoding="utf-8")

    assert _inject_instruction_snippet("codex", lang="zh") is not None

    text = target.read_text(encoding="utf-8")
    assert text.startswith("# codex rules\n") and text.count(_INSTRUCTION_MARKER) == 1
    _has_rules_zh(text)


@pytest.mark.parametrize("tool_id", _TOOLS)
@pytest.mark.parametrize("strict", [False, True])
def test_refreshing_the_new_default_is_idempotent(home, tmp_path, monkeypatch, tool_id, strict):
    if strict:
        _latch_strict(tmp_path / "store")
    target = _point(monkeypatch, tool_id, home / f"{tool_id}.md")
    assert _inject_instruction_snippet(tool_id, lang="en") is not None
    first = target.read_bytes()

    assert _inject_instruction_snippet(tool_id, lang="en") is not None
    assert target.read_bytes() == first
    assert _instruction_snippet_state(tool_id, first.decode("utf-8"), strict=strict) == "current"


def test_doctor_names_a_newer_default_for_an_owner_block(home, tmp_path):
    store = tmp_path / "store"
    Engram(root=store)
    _latch_strict(store)
    claude = home / ".claude" / "CLAUDE.md"
    claude.parent.mkdir(parents=True)
    claude.write_text("# rules\n\n" + _OWNER_BLOCK, encoding="utf-8")
    before = claude.read_bytes()

    buf = io.StringIO()
    with redirect_stdout(buf):
        doctor._run_functional_checks(fix=True)
    out = buf.getvalue()

    assert claude.read_bytes() == before
    assert "your own Engram block" in out
    assert "newer default" in out and "merge" in out


# ---------------------------------------------------------------------------
# 3. a revision must name an entry that exists
# ---------------------------------------------------------------------------


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    if getattr(request, "param", False):
        monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    old_session = mcp_server._session
    try:
        old_session._stop_event.set()
    except Exception:
        pass
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    monkeypatch.setattr(mcp_server, "_track_count", 0, raising=False)
    engram = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _store(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for sub in ("knowledge", "playbooks", "staging") if (root / sub).exists()
        for p in sorted((root / sub).rglob("*")) if p.is_file()
    }


_CONTENT = {
    "lesson": {"summary": "Rotate the build cache weekly to keep disks free"},
    "decision": {"question": "Which build cache store?", "choice": "local disk"},
    "playbook": {"title": "Clear the build cache", "steps": [{"action": "stop"}, {"action": "clear"}]},
}


def _calls(kind: str, version):
    content = {**_CONTENT[kind], "supersedes": "no-such-entry-id"}
    if version is not None:
        content["supersedes_expected_version"] = version
    yield "memory_store", mcp_server.memory_store(
        kind=kind, content_json=json.dumps(content), user_confirmed=True)
    if kind != "playbook":  # batch mode takes lessons and decisions only
        yield "memory_store batch", mcp_server.memory_store(
            kind=kind, items_json=json.dumps([dict(_CONTENT[kind]), dict(content)]), user_confirmed=True)
    if kind == "lesson":
        yield "add_lesson", mcp_server.add_lesson(
            summary=_CONTENT[kind]["summary"], supersedes="no-such-entry-id",
            supersedes_expected_version=version, user_confirmed=True)
    elif kind == "decision":
        yield "add_decision", mcp_server.add_decision(
            question=_CONTENT[kind]["question"], choice=_CONTENT[kind]["choice"],
            supersedes="no-such-entry-id", supersedes_expected_version=version, user_confirmed=True)
    else:
        yield "add_playbook", mcp_server.add_playbook(
            title=_CONTENT[kind]["title"], triggers="build cache",
            steps_json=json.dumps(_CONTENT[kind]["steps"]),
            supersedes="no-such-entry-id", supersedes_expected_version=version, user_confirmed=True)


@pytest.mark.parametrize("eng", [False, True], indirect=True, ids=["default", "strict"])
@pytest.mark.parametrize("kind", ["lesson", "decision", "playbook"])
@pytest.mark.parametrize("version", [None, 1])
def test_a_revision_of_an_unknown_id_is_refused_everywhere(eng, kind, version):
    before = _store(eng.root)
    for name, coro in _calls(kind, version):
        result = json.loads(asyncio.run(coro))
        assert result["error"] == "supersedes_target_not_found", (name, result)
        assert result["changed"] is False
        assert _store(eng.root) == before, name
