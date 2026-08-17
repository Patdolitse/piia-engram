#!/usr/bin/env python3
"""Guard the embedded-host facade contract against unintended semantic drift.

The facade promises its embedding hosts a stable contract
(``engram.embedded_host_facade.v1``). That promise is only as good as the
discipline that stops accidental surface changes. This guard pins the facade's
semantic identity - contract/schema identifiers, retrieval modes, read-only
guarantee, public API names, and item bound - to a checked-in manifest
(``docs/embedded/contract-manifest.json``).

Any facade change that alters the projection fails CI until the manifest is
consciously updated. Updating the manifest is the deliberate act; if the
contract or snapshot identifiers themselves changed, that is a contract
version bump and must be reflected in ``FACADE_CONTRACT_VERSION`` /
``SNAPSHOT_SCHEMA`` and the facade documentation.

Usage:
    python scripts/check_embedded_contract.py            # verify (CI mode)
    python scripts/check_embedded_contract.py --update   # rewrite manifest

Exit codes: 0 ok, 1 drift detected, 2 setup error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parent.parent / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "embedded"
    / "contract-manifest.json"
)
MANIFEST_SCHEMA = "engram.embedded_contract_manifest.v1"


def semantic_projection() -> dict:
    """Project the live facade surface into its semantic identity mapping."""
    from piia_engram import embedded
    from piia_engram.embedded import contract as contract_mod
    from piia_engram.embedded import snapshot as snapshot_mod

    return {
        "schema": MANIFEST_SCHEMA,
        "facade_contract": contract_mod.FACADE_CONTRACT_VERSION,
        "snapshot_schema": contract_mod.SNAPSHOT_SCHEMA,
        "witness_schema": contract_mod.WITNESS_SCHEMA,
        "retrieval_modes": sorted(contract_mod.RETRIEVAL_MODES),
        "read_only_guarantee": dict(contract_mod.READ_ONLY_GUARANTEE),
        "public_api": sorted(embedded.__all__),
        "max_items": int(snapshot_mod.MAX_ITEMS),
    }


def check(manifest: dict) -> list[str]:
    live = semantic_projection()
    if manifest.get("schema") != MANIFEST_SCHEMA:
        return ["manifest_schema_mismatch"]
    problems = []
    for key, live_value in live.items():
        if key == "schema":
            continue
        if manifest.get(key) != live_value:
            problems.append(f"{key}: manifest={manifest.get(key)!r} live={live_value!r}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite the manifest from the live surface (the deliberate act)",
    )
    args = parser.parse_args()

    try:
        projection = semantic_projection()
    except Exception as exc:  # pragma: no cover - setup failure path
        print(f"[error] cannot project the embedded facade: {exc}", file=sys.stderr)
        return 2

    if args.update:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(
            json.dumps(projection, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        print(f"[ok] manifest rewritten: {MANIFEST_PATH}")
        return 0

    if not MANIFEST_PATH.is_file():
        print(
            f"[fail] manifest missing: {MANIFEST_PATH} — generate it with --update",
            file=sys.stderr,
        )
        return 1
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[fail] manifest is not valid JSON: {exc}", file=sys.stderr)
        return 1

    problems = check(manifest)
    if not problems:
        print(
            "[ok] embedded facade contract manifest matches the live surface "
            f"({projection['facade_contract']})"
        )
        return 0
    print("::error::embedded facade contract drift detected:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "The facade surface changed. A surface change must be a conscious act:\n"
        "  1. re-run: python scripts/check_embedded_contract.py --update\n"
        "  2. regenerate: python scripts/generate_capability_witness.py -o "
        "docs/embedded/capability-witness.json\n"
        "  3. if identifiers changed, bump FACADE_CONTRACT_VERSION / SNAPSHOT_SCHEMA\n"
        "     and document the migration for embedding hosts.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
