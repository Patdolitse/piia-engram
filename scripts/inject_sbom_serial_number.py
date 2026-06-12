"""Inject a deterministic CycloneDX serialNumber for attestation compatibility.

``cyclonedx-py --output-reproducible`` intentionally omits ``serialNumber``,
but ``actions/attest`` only recognizes a CycloneDX SBOM when ``bomFormat``,
``serialNumber`` and ``specVersion`` are all present. Derive the serial from
the SBOM content itself (UUIDv5 over the canonical JSON without
serialNumber), so the output stays reproducible: same content, same serial.

Exit codes:
  0: serialNumber present (injected or already valid)
  2: setup/input error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path


OK = 0
SETUP_ERROR = 2

SERIAL_NUMBER_RE = re.compile(
    r"^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def derive_serial_number(document: dict) -> str:
    basis = {key: value for key, value in document.items() if key != "serialNumber"}
    canonical = json.dumps(basis, sort_keys=True, separators=(",", ":"))
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, canonical)}"


def inject_serial_number(path: Path | str) -> tuple[int, str]:
    sbom_path = Path(path)
    try:
        document = json.loads(sbom_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return SETUP_ERROR, f"cannot load SBOM: {exc}"
    if not isinstance(document, dict):
        return SETUP_ERROR, "SBOM root must be a JSON object"

    existing = document.get("serialNumber")
    if isinstance(existing, str) and SERIAL_NUMBER_RE.match(existing):
        return OK, f"serialNumber already present: {existing}"

    document["serialNumber"] = derive_serial_number(document)
    sbom_path.write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    return OK, f"injected deterministic serialNumber: {document['serialNumber']}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sbom_json")
    args = parser.parse_args(argv)

    code, message = inject_serial_number(args.sbom_json)
    if code == OK:
        print(f"[OK] {message} ({args.sbom_json})")
    else:
        print(f"[error] {message} ({args.sbom_json})", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
