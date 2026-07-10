"""Tests for the canonical public product-boundary guard."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_product_boundary.py"


def _load():
    spec = importlib.util.spec_from_file_location("_product_boundary", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def guard():
    return _load()


def test_product_boundary_guard_passes_current_repo(guard):
    ok, report = guard.run(ROOT)

    assert ok is True, report["problems"]
    assert report["source_doc"] == "docs/product-boundary.md"
    assert report["audit"]["tracked_files"] > 0
    assert "package_source" in report["audit"]["categories"]


def test_product_boundary_contract_is_in_public_facts():
    data = json.loads((ROOT / "docs" / "public-facts.json").read_text(encoding="utf-8"))
    contract = data["product_boundary_contract"]

    assert contract["status"] == "canonical_public_product_boundary"
    assert "identity" in contract["public_core"]
    assert "reader" in contract["public_advanced_adapters"]
    assert contract["optional_extensions"]["reader"]
    assert "docs/product-boundary.md" in contract["public_surface_files"]
    assert "docs/tool-surface-analysis.md" in contract["public_surface_files"]
    assert "glama.yaml" in contract["public_surface_files"]


def test_synthetic_private_module_and_import_are_blocked_without_raw_echo(guard, tmp_path):
    contract = guard.load_contract(ROOT)
    raw_module = "src/piia_engram/private_research_payload.py"
    raw_import = "from piia_engram.private_research_payload import secret\n"

    problems = guard.check_package_surface(
        tmp_path,
        contract,
        tracked_files=[raw_module, "src/piia_engram/public_adapter.py"],
        text_by_rel={
            raw_module: "VALUE = 1\n",
            "src/piia_engram/public_adapter.py": raw_import,
        },
    )
    rendered = json.dumps(problems, ensure_ascii=False)

    assert {p["code"] for p in problems} == {
        "package_module_private_marker",
        "package_import_private_marker",
    }
    assert "private_research_payload" not in rendered
    assert raw_import not in rendered


@pytest.mark.parametrize(
    "source",
    [
        "from .private_extension import secret\n",
        "from . import private_extension\n",
        "import piia_engram_private_extension\n",
        "from piia_engram_private_extension import secret\n",
        "import experimental_research_adapter\n",
    ],
)
def test_private_import_forms_are_blocked_without_raw_echo(guard, tmp_path, source):
    contract = guard.load_contract(ROOT)

    problems = guard.check_package_surface(
        tmp_path,
        contract,
        tracked_files=["src/piia_engram/public_adapter.py"],
        text_by_rel={"src/piia_engram/public_adapter.py": source},
    )
    rendered = json.dumps(problems, ensure_ascii=False)

    assert [p["code"] for p in problems] == ["package_import_private_marker"]
    assert source.strip() not in rendered
    assert "private_extension" not in rendered
    assert "experimental_research_adapter" not in rendered


def test_public_imports_are_not_flagged(guard, tmp_path):
    contract = guard.load_contract(ROOT)
    source = "\n".join([
        "import json",
        "import piia_engram.context",
        "from .context import EngramContext",
        "from piia_engram.export_redaction import scan_export_text",
        "",
    ])

    problems = guard.check_package_surface(
        tmp_path,
        contract,
        tracked_files=["src/piia_engram/public_adapter.py"],
        text_by_rel={"src/piia_engram/public_adapter.py": source},
    )

    assert problems == []


def test_synthetic_public_surface_failure_does_not_echo_unsafe_text(guard):
    contract = guard.load_contract(ROOT)
    raw_path = r"C:\Users\victim\secret\notes.txt"
    raw_term = "private research branch codename should never print"

    problems = guard.check_public_surfaces(
        ROOT,
        contract,
        text_by_rel={
            "README.md": f"oops {raw_path}\n",
            "docs/product-boundary.md": f"oops {raw_term}\n",
        },
    )
    rendered = json.dumps(problems, ensure_ascii=False)

    assert any(p["code"] == "public_surface_private_path" for p in problems)
    assert any(p["code"] == "public_surface_private_term" for p in problems)
    assert raw_path not in rendered
    assert raw_term not in rendered
    assert "victim" not in rendered


def test_setup_error_for_missing_absolute_facts_path_is_path_free_json(guard, tmp_path, capsys):
    missing = tmp_path / "very-private-token" / "facts.json"

    rc = guard.main(["--facts", str(missing), "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert rc == 1
    assert payload == {
        "ok": False,
        "setup_error": {
            "code": "json_missing",
            "detail": "configured JSON input is missing",
        },
    }
    assert str(missing) not in out
    assert "very-private-token" not in out


def test_setup_error_for_missing_absolute_facts_path_is_path_free_text(guard, tmp_path, capsys):
    missing = tmp_path / "very-private-token" / "facts.json"

    rc = guard.main(["--facts", str(missing)])
    out = capsys.readouterr().out

    assert rc == 1
    assert "setup_error json_missing" in out
    assert str(missing) not in out
    assert "very-private-token" not in out


def test_setup_error_api_is_path_free(guard, tmp_path):
    missing = tmp_path / "very-private-token" / "facts.json"

    with pytest.raises(guard.SetupError) as exc_info:
        guard.run(ROOT, facts_path=str(missing))

    exc = exc_info.value
    assert exc.code == "json_missing"
    assert exc.detail == "configured JSON input is missing"
    assert str(missing) not in str(exc)
    assert "very-private-token" not in str(exc)


def test_malformed_contract_reports_stable_codes_without_path_echo(guard, tmp_path):
    private_manifest = tmp_path / "very-private-token" / "facts.json"
    private_manifest.parent.mkdir()
    private_manifest.write_text(
        json.dumps({
            "product_boundary_contract": {
                "schema_version": 1,
                "status": "drifted",
                "source_doc": "docs/product-boundary.md",
                "public_core": "identity",
                "public_advanced_adapters": ["reader"],
                "optional_extensions": ["reader"],
                "public_package_roots": ["piia_engram"],
                "public_export_surfaces": ["identity_card"],
                "public_surface_files": ["README.md"],
                "forbidden_package_path_markers": ["private", 1],
                "forbidden_public_surface_terms": ["dogfood"],
                "non_claims": ["ok"],
            }
        }),
        encoding="utf-8",
    )

    ok, report = guard.run(ROOT, facts_path=str(private_manifest))
    rendered = json.dumps(report, ensure_ascii=False)

    assert ok is False
    assert {p["code"] for p in report["problems"]} >= {
        "contract_status",
        "contract_bad_list",
        "contract_bad_extensions",
    }
    assert report["contract"] == "configured_public_facts"
    assert str(private_manifest) not in rendered
    assert "very-private-token" not in rendered


def test_mcp_and_cli_surface_counts_remain_unchanged(guard):
    count_script = ROOT / "scripts" / "count_mcp_tools.py"
    spec = importlib.util.spec_from_file_location("_count_mcp_tools", count_script)
    assert spec and spec.loader
    counter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(counter)

    facts = json.loads((ROOT / "docs" / "public-facts.json").read_text(encoding="utf-8"))
    counts = counter.derive(ROOT)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert counts == {
        "total": facts["facts"]["mcp_tools_total"],
        "core": facts["facts"]["mcp_tools_core"],
        "advanced": facts["facts"]["mcp_tools_advanced"],
    }
    assert "check_product_boundary" not in pyproject
