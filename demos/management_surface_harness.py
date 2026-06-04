"""GUI-ready read-only management surface evidence harness (Task 5, B+).

Proves the read-only management surface (:func:`build_readonly_management_surface`)
is strictly read-only and leaks nothing, on a synthetic store in a temp dir:

1. Seed a synthetic Engram store with staging review items + a playbook.
2. Fingerprint the store on disk.
3. Build the read-only surface (and the underlying management view).
4. Fingerprint again — assert byte-identical (the surface mutated nothing).
5. Assert the closed schema holds, the capability contract declares read-only /
   no exposed mutations / no network listener, and no body/path/secret leaks.

Safety invariants (enforced):
- Writes only under the caller-provided temp base; never touches ``~/.engram``.
- ``ENGRAM_TEST=1`` suppresses the fragmentation check.
- The surface is a plain return value — no server, no socket, no listener.

Run from the repo root::

    python demos/management_surface_harness.py            # human summary
    python demos/management_surface_harness.py --json      # machine-readable
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("ENGRAM_TEST", "1")

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from piia_engram.core import Engram  # noqa: E402
from piia_engram.management_view import (  # noqa: E402
    assert_readonly_surface_closed,
    build_readonly_management_surface,
)

# A token that must never appear anywhere in the surface output.
_SECRET = "ZZ_SURFACE_SECRET_TOKEN"

# Body-only keys that must never surface (raw payload / path leakage).
_FORBIDDEN_KEYS = {
    "summary",
    "detail",
    "body",
    "content",
    "reasoning",
    "question",
    "choice",
    "title",
    "description",
    "triggers",
    "steps",
    "project_folder",
    "raw_path",
}


def seed_store(root: Path) -> Engram:
    """Seed a synthetic store with staging items + a playbook (all fake)."""
    eng = Engram(root=root)
    eng.add_lesson(
        f"{_SECRET} lesson summary",
        detail=f"{_SECRET} lesson detail",
        domain=f"{_SECRET} domain",
        tier="staging",
    )
    eng.add_decision(
        f"{_SECRET} decision question",
        choice=f"{_SECRET} decision choice",
        reasoning=f"{_SECRET} decision reasoning",
        tier="staging",
    )
    eng.add_playbook({
        "title": f"{_SECRET} playbook title",
        "description": f"{_SECRET} playbook description",
        "triggers": [f"{_SECRET} trigger"],
        "steps": [f"{_SECRET} step"],
        "scope_type": "project",
        "project_folder": str(root),
    })
    return eng


def _store_fingerprint(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root)).replace("\\", "/")
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    return out


def _collect_keys(value: Any, into: set[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            into.add(str(key))
            _collect_keys(nested, into)
    elif isinstance(value, list):
        for item in value:
            _collect_keys(item, into)


def run_harness(base: Path) -> dict[str, Any]:
    """Build the surface over a synthetic store and verify read-only + no leak."""
    base.mkdir(parents=True, exist_ok=True)
    root = base / "store"
    eng = seed_store(root)

    before = _store_fingerprint(root)
    surface = build_readonly_management_surface(eng, project_folder=str(root))
    after = _store_fingerprint(root)

    store_unchanged = before == after

    schema_ok = True
    schema_error = ""
    try:
        assert_readonly_surface_closed(surface)
    except AssertionError as exc:
        schema_ok = False
        schema_error = str(exc)

    rendered = json.dumps(surface, ensure_ascii=False, sort_keys=True)
    no_secret_leak = _SECRET not in rendered
    no_path_leak = str(root) not in rendered

    all_keys: set[str] = set()
    _collect_keys(surface, all_keys)
    no_body_keys = not (all_keys & _FORBIDDEN_KEYS)

    caps = surface.get("capabilities", {}) if isinstance(surface, dict) else {}
    read_only = caps.get("read_only") is True and caps.get("exposed_mutations") == []
    no_listener = caps.get("network_listener") is False

    return {
        "schema": 1,
        "harness": "readonly_management_surface_v1",
        "synthetic_only": True,
        "temp_dir_only": True,
        "store_unchanged": store_unchanged,
        "schema_closed": schema_ok,
        "schema_error": schema_error,
        "read_only_contract": read_only,
        "no_network_listener": no_listener,
        "no_secret_leak": no_secret_leak,
        "no_path_leak": no_path_leak,
        "no_body_keys": no_body_keys,
        "review_pending": surface["view"]["review_queue"]["pending_count"],
        "playbook_total": surface["view"]["playbooks"]["total"],
        "overall_passed": (
            store_unchanged
            and schema_ok
            and read_only
            and no_listener
            and no_secret_leak
            and no_path_leak
            and no_body_keys
        ),
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "Engram read-only management surface harness (synthetic, temp-dir only)",
        f"  store_unchanged={report['store_unchanged']}  schema_closed={report['schema_closed']}",
        f"  read_only_contract={report['read_only_contract']}  "
        f"no_network_listener={report['no_network_listener']}",
        f"  no_secret_leak={report['no_secret_leak']}  no_path_leak={report['no_path_leak']}  "
        f"no_body_keys={report['no_body_keys']}",
        f"  review_pending={report['review_pending']}  playbook_total={report['playbook_total']}",
        f"  overall: {'PASS' if report['overall_passed'] else 'FAIL'}",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the synthetic read-only management surface evidence harness."
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--keep", action="store_true", help="Keep the temp base and print its path.")
    args = parser.parse_args()

    base = Path(tempfile.mkdtemp(prefix="engram-mgmt-surface-"))
    try:
        report = run_harness(base)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(render_text(report))
        if args.keep:
            print(f"Kept base: {base}")
        return 0 if report["overall_passed"] else 1
    finally:
        if not args.keep:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
