"""Guard piia-engram's public product boundary contract.

The contract lives in docs/public-facts.json so public facts stay centralized.
This script is intentionally read-only and metadata-only: it reports file names,
line numbers, and rule codes, but never echoes matched unsafe content.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

DEFAULT_FACTS = "docs/public-facts.json"
TOOL_SURFACE = "docs/mcp-tool-surface.json"
CANONICAL_STATUS = "canonical_public_product_boundary"

_PACKAGE_IMPORT_ROOTS = {"piia_engram", "engram_core"}
_TEXT_SUFFIXES = {".json", ".md", ".py", ".toml", ".yaml", ".yml"}
_PRIVATE_PATH_RE = re.compile(
    r"(?i)(?:[A-Z]:[\\/]+Users[\\/]+[A-Za-z0-9_.-]+|/[Uu]sers/[A-Za-z0-9._-]+/|/home/[A-Za-z0-9._-]+/)"
)


class SetupError(Exception):
    """Boundary contract or repository layout problem.

    The message is deliberately stable and path-free because setup failures can
    be triggered with caller-controlled paths.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise SetupError("json_missing", "configured JSON input is missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SetupError("json_invalid", "configured JSON input is invalid") from exc
    if not isinstance(data, dict):
        raise SetupError("json_not_object", "configured JSON input must be an object")
    return data


def _problem(code: str, rel: str, detail: str, *, line: int | None = None) -> dict:
    out = {"code": code, "file": rel, "detail": detail}
    if line is not None:
        out["line"] = line
    return out


def load_contract(root: Path, facts_path: str = DEFAULT_FACTS) -> dict:
    facts = _read_json(root / facts_path)
    contract = facts.get("product_boundary_contract")
    if not isinstance(contract, dict):
        raise SetupError(
            "contract_missing",
            "public facts manifest is missing product_boundary_contract",
        )
    return contract


def validate_contract(contract: dict) -> list[dict]:
    required = [
        "schema_version",
        "status",
        "source_doc",
        "public_core",
        "public_advanced_adapters",
        "optional_extensions",
        "public_package_roots",
        "public_export_surfaces",
        "public_surface_files",
        "forbidden_package_path_markers",
        "forbidden_public_surface_terms",
        "non_claims",
    ]
    problems: list[dict] = []
    for key in required:
        if key not in contract:
            problems.append(_problem("contract_missing_key", DEFAULT_FACTS, key))
    if problems:
        return problems
    if contract["schema_version"] != 1:
        problems.append(_problem("contract_schema_version", DEFAULT_FACTS, "expected schema_version=1"))
    if contract["status"] != CANONICAL_STATUS:
        problems.append(_problem("contract_status", DEFAULT_FACTS, "status must be canonical_public_product_boundary"))
    list_keys = (
        "public_core",
        "public_advanced_adapters",
        "public_package_roots",
        "public_export_surfaces",
        "public_surface_files",
        "forbidden_package_path_markers",
        "forbidden_public_surface_terms",
        "non_claims",
    )
    for key in list_keys:
        value = contract.get(key)
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
            problems.append(_problem("contract_bad_list", DEFAULT_FACTS, key))
    extensions = contract.get("optional_extensions")
    if (
        not isinstance(extensions, dict)
        or not extensions
        or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in extensions.items())
    ):
        problems.append(_problem("contract_bad_extensions", DEFAULT_FACTS, "optional_extensions must be a non-empty object"))
    return problems


def _path_parts(rel: str) -> list[str]:
    return [p.lower() for p in rel.replace("\\", "/").split("/") if p]


def _contains_marker(value: str, markers: set[str]) -> bool:
    lower = value.lower()
    parts = re.split(r"[^a-z0-9]+", lower)
    return any(marker in parts or marker in lower for marker in markers)


def _redact_marker_segments(rel: str, markers: set[str]) -> str:
    safe_parts = []
    for part in rel.replace("\\", "/").split("/"):
        if _contains_marker(part, markers):
            safe_parts.append("<redacted>")
        else:
            safe_parts.append(part)
    return "/".join(safe_parts)


def _module_name_from_path(rel: str) -> str:
    path = rel.replace("\\", "/")
    if not path.startswith("src/") or not path.endswith(".py"):
        return ""
    stem = path[4:-3]
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")


def _import_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    names: list[str] = []
    if node.module:
        names.append("." * node.level + node.module if node.level else node.module)
    for alias in node.names:
        names.append("." * node.level + alias.name if node.level else alias.name)
    return names


def check_package_surface(
    root: Path,
    contract: dict,
    *,
    tracked_files: list[str] | None = None,
    text_by_rel: dict[str, str] | None = None,
) -> list[dict]:
    roots = set(contract["public_package_roots"])
    markers = {m.lower() for m in contract["forbidden_package_path_markers"]}
    tracked = tracked_files if tracked_files is not None else _git_tracked_files(root)
    text_by_rel = text_by_rel or {}
    problems: list[dict] = []

    for rel in tracked:
        rel = rel.replace("\\", "/")
        if not rel.startswith("src/") or not rel.endswith(".py"):
            continue
        parts = _path_parts(rel)
        if len(parts) < 3:
            continue
        package_root = parts[1]
        if package_root not in roots:
            problems.append(_problem("package_root_uncontracted", rel, "source package root is not in contract"))
            continue
        module_name = _module_name_from_path(rel)
        if _contains_marker(module_name, markers):
            problems.append(
                _problem(
                    "package_module_private_marker",
                    _redact_marker_segments(rel, markers),
                    "module path uses a private/internal marker",
                )
            )

        text = text_by_rel.get(rel)
        if text is None:
            path = root / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for name in _import_names(node):
                if _contains_marker(name, markers):
                    problems.append(
                        _problem(
                            "package_import_private_marker",
                            rel,
                            "package import uses a private/internal marker",
                            line=node.lineno,
                        )
                    )
                    break
    return problems


def _pyproject_optional_extras(root: Path) -> set[str]:
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib

    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data.get("project", {}).get("optional-dependencies", {}))


def check_optional_extensions(root: Path, contract: dict) -> list[dict]:
    declared = set(contract["optional_extensions"])
    actual = _pyproject_optional_extras(root)
    problems: list[dict] = []
    missing = declared - actual
    extra = actual - declared - {"dev"}
    if missing:
        problems.append(_problem("optional_extra_missing", "pyproject.toml", "contract names an optional extra missing from pyproject"))
    if extra:
        problems.append(_problem("optional_extra_uncontracted", "pyproject.toml", "pyproject optional extra is not in contract"))
    return problems


def check_tool_surface(root: Path, contract: dict) -> list[dict]:
    data = _read_json(root / TOOL_SURFACE)
    forbidden = {t.lower() for t in contract["forbidden_public_surface_terms"]}
    problems: list[dict] = []
    for i, tier in enumerate(data.get("tiers", [])):
        blob = json.dumps(tier, ensure_ascii=False).lower()
        if any(term in blob for term in forbidden):
            problems.append(_problem("tool_surface_private_term", TOOL_SURFACE, "tier label or meaning uses a private/internal public term", line=i + 1))
    expected = {"core", "advanced_public", "owner_local_maintenance", "preview_proposal_only"}
    names = {tier.get("name") for tier in data.get("tiers", [])}
    if not expected.issubset(names):
        problems.append(_problem("tool_surface_tier_contract", TOOL_SURFACE, "required public tier names are missing"))
    return problems


def _scan_text_terms(rel: str, text: str, forbidden_terms: set[str]) -> list[dict]:
    problems: list[dict] = []
    lower_terms = {term.lower() for term in forbidden_terms}
    for lineno, line in enumerate(text.splitlines(), start=1):
        lower = line.lower()
        if _PRIVATE_PATH_RE.search(line):
            problems.append(_problem("public_surface_private_path", rel, "public surface contains a private/local path shape", line=lineno))
        if any(term in lower for term in lower_terms):
            problems.append(_problem("public_surface_private_term", rel, "public surface contains a private/internal public term", line=lineno))
    return problems


def check_public_surfaces(
    root: Path,
    contract: dict,
    *,
    text_by_rel: dict[str, str] | None = None,
) -> list[dict]:
    text_by_rel = text_by_rel or {}
    forbidden_terms = set(contract["forbidden_public_surface_terms"])
    rels: list[str] = []
    for rel in contract["public_surface_files"]:
        if rel.endswith("/"):
            for path in sorted((root / rel).glob("*")):
                if path.is_file() and path.suffix.lower() in _TEXT_SUFFIXES:
                    rels.append(path.relative_to(root).as_posix())
        else:
            rels.append(rel)

    problems: list[dict] = []
    for rel in rels:
        text = text_by_rel.get(rel)
        if text is None:
            path = root / rel
            if not path.is_file():
                problems.append(_problem("public_surface_missing", rel, "contracted public surface is missing"))
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        problems.extend(_scan_text_terms(rel, text, forbidden_terms))
    return problems


def check_public_export_contract(contract: dict) -> list[dict]:
    expected = {"identity_card", "knowledge_report", "agents_md"}
    surfaces = set(contract.get("public_export_surfaces", []))
    if surfaces != expected:
        return [_problem("export_surface_contract", DEFAULT_FACTS, "public export surface names drifted")]
    return []


def _git_tracked_files(root: Path) -> list[str]:
    import subprocess

    out = subprocess.check_output(["git", "ls-files"], cwd=root, text=True, encoding="utf-8")
    return [line.replace("\\", "/") for line in out.splitlines() if line.strip()]


def audit_development_surface(root: Path, tracked_files: list[str] | None = None) -> dict:
    tracked = tracked_files if tracked_files is not None else _git_tracked_files(root)
    counts = Counter()
    for rel in tracked:
        rel = rel.replace("\\", "/")
        if rel.startswith("src/"):
            counts["package_source"] += 1
        elif rel.startswith("tests/"):
            counts["tests"] += 1
        elif rel.startswith("docs/"):
            counts["public_docs"] += 1
        elif rel.startswith("release-evidence/"):
            counts["release_evidence"] += 1
        elif rel.startswith("scripts/"):
            counts["guard_or_helper_scripts"] += 1
        elif rel.startswith(".github/"):
            counts["ci_workflows"] += 1
        else:
            counts["other_public_files"] += 1
    return {"tracked_files": len(tracked), "categories": dict(sorted(counts.items()))}


def run(root: str | Path = ".", *, facts_path: str = DEFAULT_FACTS) -> tuple[bool, dict]:
    root_path = Path(root).resolve()
    contract = load_contract(root_path, facts_path)
    problems: list[dict] = []
    problems.extend(validate_contract(contract))
    if not problems:
        problems.extend(check_package_surface(root_path, contract))
        problems.extend(check_optional_extensions(root_path, contract))
        problems.extend(check_tool_surface(root_path, contract))
        problems.extend(check_public_export_contract(contract))
        problems.extend(check_public_surfaces(root_path, contract))
    report = {
        "ok": not problems,
        "contract": "configured_public_facts",
        "source_doc": contract.get("source_doc"),
        "audit": audit_development_surface(root_path),
        "problems": problems,
    }
    return not problems, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="Repo root (default: cwd).")
    parser.add_argument("--facts", default=DEFAULT_FACTS, help="Public facts manifest path.")
    parser.add_argument("--audit", action="store_true", help="Emit development-surface audit counts.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    try:
        ok, report = run(args.root, facts_path=args.facts)
    except SetupError as exc:
        report = {
            "ok": False,
            "setup_error": {"code": exc.code, "detail": exc.detail},
        }
        ok = False

    if args.audit and "audit" in report and not args.json:
        print(json.dumps(report["audit"], ensure_ascii=False, indent=2))
        return 0 if ok else 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    if ok:
        print("[OK] product boundary contract is clean.")
        if args.audit and "audit" in report:
            print(json.dumps(report["audit"], ensure_ascii=False, indent=2))
        return 0
    print("::error::product boundary contract violations:")
    for item in report.get("problems", []):
        loc = item["file"] + (f":{item['line']}" if "line" in item else "")
        print(f"  - {loc} {item['code']}: {item['detail']}")
    if "setup_error" in report:
        err = report["setup_error"]
        print(f"  - setup_error {err['code']}: {err['detail']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
