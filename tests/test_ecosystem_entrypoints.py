"""Static guards for the ecosystem entry points (Agent Skill + Cursor plugin).

These tests protect the honest-positioning skeleton against two failure modes:

1. Format drift — manifests must stay parseable and the skill frontmatter must
   keep the Anthropic-required ``name`` / ``description`` keys.
2. Overclaiming — neither the skill nor the Cursor plugin copy may describe
   capabilities that are planned but not implemented today.

They are deliberately filesystem-only (no server import) so they stay fast and
run anywhere the repo is checked out.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

SKILL_DIR = ROOT / "skills" / "engram"
SKILL_MD = SKILL_DIR / "SKILL.md"
SKILL_TOOLS_REF = SKILL_DIR / "references" / "tools.md"
SKILL_PRIVACY_REF = SKILL_DIR / "references" / "privacy.md"

CURSOR_PLUGIN = ROOT / ".cursor-plugin" / "plugin.json"
CURSOR_README = ROOT / ".cursor-plugin" / "README.md"

# Capabilities that are planned but NOT implemented. They must never appear in
# user-facing ecosystem copy. Matched case-insensitively. "acp" is matched on
# word boundaries so it doesn't false-positive inside words like "capacity".
FORBIDDEN_OVERCLAIMS = [
    "workflow_stage",
    "caller_role",
    "caller_depth",
    "stop-hook",
    "passive writeback",
]
FORBIDDEN_OVERCLAIM_WORDS = ["acp"]

# Cursor's documented plugin-name shape.
CURSOR_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")

# Anthropic Agent Skill name shape (lowercase, hyphen-separated) and the
# documented upper bound on the skill description length.
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SKILL_DESCRIPTION_MAX = 1024


def _assert_no_overclaims(text: str, where: str) -> None:
    lowered = text.lower()
    for phrase in FORBIDDEN_OVERCLAIMS:
        assert phrase not in lowered, f"{where} must not claim unimplemented feature: {phrase!r}"
    for word in FORBIDDEN_OVERCLAIM_WORDS:
        assert not re.search(rf"\b{re.escape(word)}\b", lowered), (
            f"{where} must not claim unimplemented feature: {word!r}"
        )

# Real Engram MCP entry-point command. The Cursor plugin must wire to this.
MCP_COMMAND = "piia-engram-mcp"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse a simple ``key: value`` YAML frontmatter block.

    Only handles the flat single-line keys the SKILL.md uses (name,
    description, license). Avoids a PyYAML dependency.
    """
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "SKILL.md must open with a '---' frontmatter fence"
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    raise AssertionError("SKILL.md frontmatter is not closed with a '---' fence")


# --- existence -------------------------------------------------------------

def test_skill_files_exist():
    assert SKILL_MD.is_file(), "skills/engram/SKILL.md must exist"
    assert SKILL_TOOLS_REF.is_file(), "skills/engram/references/tools.md must exist"
    assert SKILL_PRIVACY_REF.is_file(), "skills/engram/references/privacy.md must exist"


def test_cursor_plugin_files_exist():
    assert CURSOR_PLUGIN.is_file(), ".cursor-plugin/plugin.json must exist"
    assert CURSOR_README.is_file(), ".cursor-plugin/README.md must exist"


# --- skill frontmatter -----------------------------------------------------

def test_skill_frontmatter_has_required_keys():
    fm = _parse_frontmatter(_read(SKILL_MD))

    assert fm.get("name") == "engram", "skill frontmatter name must be 'engram'"
    description = fm.get("description", "")
    assert description, "skill frontmatter description must be non-empty"
    assert len(description) > 40, "skill description should be trigger-rich, not a stub"


def test_skill_name_matches_anthropic_pattern():
    """The skill name must match Anthropic's lowercase hyphen-separated shape."""
    fm = _parse_frontmatter(_read(SKILL_MD))
    name = fm.get("name", "")
    assert SKILL_NAME_RE.match(name), (
        f"skill name {name!r} violates Anthropic's skill-name pattern"
    )


def test_skill_description_within_length_budget():
    """Anthropic caps the skill description; keep it parseable and in-budget."""
    fm = _parse_frontmatter(_read(SKILL_MD))
    description = fm.get("description", "")
    assert len(description) <= SKILL_DESCRIPTION_MAX, (
        f"skill description is {len(description)} chars; max is {SKILL_DESCRIPTION_MAX}"
    )


def test_skill_description_is_trigger_rich():
    """The description should mention the situations that should trigger Engram."""
    fm = _parse_frontmatter(_read(SKILL_MD))
    description = fm["description"].lower()

    # A representative subset of the documented triggers.
    for phrase in ["continue from", "what did we decide", "remember this", "search prior"]:
        assert phrase in description, f"skill description should mention trigger: {phrase!r}"


# --- manifest parsing ------------------------------------------------------

def test_cursor_plugin_manifest_parses_and_is_named():
    plugin = json.loads(_read(CURSOR_PLUGIN))

    assert plugin["name"] == "engram"
    assert plugin["description"], "cursor plugin needs a non-empty description"
    # name must match cursor's documented kebab-case identifier shape
    assert CURSOR_NAME_RE.match(plugin["name"]), (
        f"plugin name {plugin['name']!r} violates cursor's name pattern"
    )


def test_claude_plugin_manifest_still_parses():
    """Sanity: the sibling Claude plugin manifest stays valid JSON."""
    plugin = json.loads(_read(ROOT / ".claude-plugin" / "plugin.json"))
    assert plugin["name"] == "piia-engram"


def test_cursor_plugin_wires_real_mcp_command():
    """If the plugin declares mcpServers, it must point at the real command."""
    plugin = json.loads(_read(CURSOR_PLUGIN))

    mcp_servers = plugin.get("mcpServers")
    assert mcp_servers, "skeleton declares mcpServers; keep it wired to Engram"

    # The real entry-point command must appear somewhere in the mcpServers block.
    assert MCP_COMMAND in json.dumps(mcp_servers), (
        f"cursor plugin mcpServers must reference {MCP_COMMAND!r}"
    )


def test_cursor_plugin_points_at_skill():
    plugin = json.loads(_read(CURSOR_PLUGIN))
    assert "skills" in plugin, "cursor plugin should point at the engram skill"


def test_skill_links_to_its_reference_files():
    """SKILL.md must actually route to the reference docs that exist on disk."""
    body = _read(SKILL_MD)
    assert "references/tools.md" in body, "SKILL.md should link references/tools.md"
    assert "references/privacy.md" in body, "SKILL.md should link references/privacy.md"


# --- overclaim guards ------------------------------------------------------

def test_skill_copy_has_no_forbidden_overclaims():
    text = "\n".join(_read(p) for p in (SKILL_MD, SKILL_TOOLS_REF, SKILL_PRIVACY_REF))
    _assert_no_overclaims(text, "skill copy")


def test_cursor_plugin_copy_has_no_forbidden_overclaims():
    text = _read(CURSOR_PLUGIN) + "\n" + _read(CURSOR_README)
    _assert_no_overclaims(text, "cursor copy")


def test_ecosystem_copy_avoids_known_marketing_overclaims():
    """Reuse the repo's house overclaim blocklist for the new surfaces."""
    text = "\n".join(
        _read(p)
        for p in (SKILL_MD, SKILL_TOOLS_REF, SKILL_PRIVACY_REF, CURSOR_PLUGIN, CURSOR_README)
    ).lower()
    for phrase in ["every ai tool", "absolutely secure", "no network ever", "zero network"]:
        assert phrase not in text, f"ecosystem copy must avoid overclaim: {phrase!r}"
