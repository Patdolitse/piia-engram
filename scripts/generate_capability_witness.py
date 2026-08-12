#!/usr/bin/env python3
"""Generate the embedded-host capability witness.

An embedding host uses the witness to admit this runtime by contract instead of
by version string: it carries the facade contract version, the snapshot schema,
the supported retrieval modes, the read-only guarantee, and a sha256 digest of
every source file that defines facade behaviour — plus a self-hash so a witness
handed over out-of-band can be checked for tampering.

Usage:
    python scripts/generate_capability_witness.py                 # print to stdout
    python scripts/generate_capability_witness.py -o witness.json # write a file
    python scripts/generate_capability_witness.py --verify w.json # verify a file

Exit codes: 0 ok, 1 verification failed, 2 usage/setup error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parent.parent / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

try:
    from piia_engram.embedded import (
        capability_witness,
        verify_witness,
        write_capability_witness,
    )
except Exception as exc:  # pragma: no cover - setup failure path
    print(f"[error] cannot import the embedded facade: {exc}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--output", help="write the witness to this path")
    ap.add_argument("--verify", help="verify an existing witness file instead of generating one")
    args = ap.parse_args()

    if args.verify:
        path = Path(args.verify)
        if not path.is_file():
            print(f"[error] no such witness file: {path}", file=sys.stderr)
            return 2
        try:
            witness = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[fail] witness is not valid JSON: {exc}", file=sys.stderr)
            return 1
        ok, problems = verify_witness(witness)
        if ok:
            print(f"[ok] witness verified: {witness['facade_contract']}")
            return 0
        for problem in problems:
            print(f"[fail] {problem}", file=sys.stderr)
        return 1

    if args.output:
        target = write_capability_witness(args.output)
        print(f"[ok] witness written: {target}")
        return 0

    print(json.dumps(capability_witness(), sort_keys=True, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
