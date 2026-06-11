"""Fail closed on unsafe CycloneDX SBOM output.

Exit codes:
  0: clean
  1: dirty SBOM content
  2: setup/input error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


CLEAN = 0
DIRTY = 1
SETUP_ERROR = 2

PRIVATE_STRING_MARKERS = (
    r"C:\Users",
    "C:/Users",
    "E:\\",
    "E:/",
    "/home/runner",
    "/Users/runner",
    "\\AppData\\",
    "RUNNER_TEMP",
    "pp3x3",
)

TOOLCHAIN_COMPONENT_NAMES = {
    "cyclonedx-bom",
    "cyclonedx-python-lib",
    "packageurl-python",
}


def _iter_string_values(value: Any, path: str = "$"):
    if isinstance(value, str):
        yield path, value
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_string_values(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_string_values(item, f"{path}.{key}")


def _main_component_mentions_project(document: dict[str, Any]) -> bool:
    component = document.get("metadata", {}).get("component")
    if not isinstance(component, dict):
        return False
    haystack = " ".join(
        str(component.get(key, ""))
        for key in ("name", "bom-ref", "purl", "group", "description")
    ).lower()
    return "piia-engram" in haystack


def _component_identity(component: dict[str, Any]) -> str:
    return " ".join(
        str(component.get(key, ""))
        for key in ("name", "bom-ref", "purl", "group")
    ).lower()


def _validate_structure(document: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(document, dict):
        return ["SBOM root must be a JSON object"]
    if document.get("bomFormat") != "CycloneDX":
        problems.append('SBOM bomFormat must be "CycloneDX"')
    components = document.get("components")
    if not isinstance(components, list):
        problems.append("SBOM components must be a list")
    if not _main_component_mentions_project(document):
        problems.append("SBOM metadata.component must identify piia-engram")
    return problems


def _find_dirty_content(document: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for path, value in _iter_string_values(document):
        for marker in PRIVATE_STRING_MARKERS:
            if marker in value:
                problems.append(f"private or runner path marker {marker!r} found at {path}")

    components = document.get("components", [])
    if isinstance(components, list):
        for index, component in enumerate(components):
            if not isinstance(component, dict):
                continue
            identity = _component_identity(component)
            for name in TOOLCHAIN_COMPONENT_NAMES:
                if name in identity:
                    problems.append(
                        f"toolchain component {name!r} found in components[{index}]"
                    )
    return problems


def check_sbom_hygiene(path: Path | str) -> tuple[int, list[str]]:
    sbom_path = Path(path)
    try:
        text = sbom_path.read_text(encoding="utf-8")
    except OSError as exc:
        return SETUP_ERROR, [f"cannot read SBOM: {exc}"]

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        return SETUP_ERROR, [f"invalid JSON: {exc}"]

    structure_problems = _validate_structure(document)
    if structure_problems:
        return SETUP_ERROR, structure_problems

    dirty_problems = _find_dirty_content(document)
    if dirty_problems:
        return DIRTY, dirty_problems
    return CLEAN, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sbom_json")
    args = parser.parse_args(argv)

    code, problems = check_sbom_hygiene(args.sbom_json)
    if code == CLEAN:
        print(f"[OK] SBOM hygiene check passed ({args.sbom_json}).")
        return CLEAN

    marker = "::error::" if code == DIRTY else "[error]"
    print(f"{marker} SBOM hygiene check failed ({args.sbom_json}):", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
