"""The setup message about seeded best practices matches what Engram does.

Seeded entries are staging; usage only suggests a promotion
(``promotion_suggested``), it never changes the tier, so both languages say
review decides what becomes verified.
"""

from __future__ import annotations

import ast
from pathlib import Path

from piia_engram.core import Engram

_SOURCE = Path(__file__).resolve().parents[1] / "src" / "piia_engram" / "setup_wizard.py"


def _seed_message() -> tuple[str, str]:
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_t" and len(node.args) == 2
                and all(isinstance(a, ast.Constant) for a in node.args)
                and "marked staging" in str(node.args[1].value)):
            return str(node.args[0].value), str(node.args[1].value)
    raise AssertionError("seed message not found")


def test_seed_message_says_the_same_in_both_languages():
    zh, en = _seed_message()
    assert "review" in en.lower()
    assert "审核" in zh
    assert "自动晋升" not in zh and "3 次" not in zh


def test_usage_never_promotes_a_staging_entry(tmp_path):
    eng = Engram(root=tmp_path)
    row = eng.add_lesson({"summary": "A seeded best practice that is used a lot", "domain": "setup",
                          "tier": "staging", "access_count": 9})
    eng.evaluate_tiers()
    assert eng._find_item_by_id(row["id"])[1]["tier"] == "staging"
