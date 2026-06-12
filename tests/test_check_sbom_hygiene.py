"""Tests for scripts/check_sbom_hygiene.py."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_sbom_hygiene.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("_check_sbom_hygiene", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sbom_hygiene():
    return _load_module()


def _write_sbom(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "sbom.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _clean_sbom() -> dict[str, object]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:1f2e3d4c-5b6a-5798-8123-456789abcdef",
        "metadata": {
            "component": {
                "type": "library",
                "name": "piia-engram",
            },
        },
        "components": [
            {"type": "library", "name": "requests"},
            {"type": "library", "name": "typing-extensions"},
        ],
    }


def test_clean_cyclonedx_sbom_passes(sbom_hygiene, tmp_path):
    path = _write_sbom(tmp_path, _clean_sbom())

    code, problems = sbom_hygiene.check_sbom_hygiene(path)

    assert code == 0
    assert problems == []


def test_windows_private_path_is_blocked(sbom_hygiene, tmp_path):
    payload = _clean_sbom()
    payload["metadata"]["properties"] = [
        {"name": "source", "value": r"C:\Users\runneradmin\work\artifact"}
    ]
    path = _write_sbom(tmp_path, payload)

    code, problems = sbom_hygiene.check_sbom_hygiene(path)

    assert code == 1
    assert any("private or runner path" in problem for problem in problems)


def test_linux_runner_path_is_blocked(sbom_hygiene, tmp_path):
    payload = _clean_sbom()
    payload["components"][0]["externalReferences"] = [
        {"type": "other", "url": "file:///home/runner/work/piia-engram"}
    ]
    path = _write_sbom(tmp_path, payload)

    code, problems = sbom_hygiene.check_sbom_hygiene(path)

    assert code == 1
    assert any("/home/runner" in problem for problem in problems)


def test_toolchain_component_pollution_is_blocked(sbom_hygiene, tmp_path):
    payload = _clean_sbom()
    payload["components"].append({"type": "library", "name": "cyclonedx-bom"})
    path = _write_sbom(tmp_path, payload)

    code, problems = sbom_hygiene.check_sbom_hygiene(path)

    assert code == 1
    assert any("toolchain component" in problem for problem in problems)


def test_missing_serial_number_is_setup_error(sbom_hygiene, tmp_path):
    payload = _clean_sbom()
    del payload["serialNumber"]
    path = _write_sbom(tmp_path, payload)

    code, problems = sbom_hygiene.check_sbom_hygiene(path)

    assert code == 2
    assert any("serialNumber" in problem for problem in problems)


def test_malformed_serial_number_is_setup_error(sbom_hygiene, tmp_path):
    payload = _clean_sbom()
    payload["serialNumber"] = "not-a-urn"
    path = _write_sbom(tmp_path, payload)

    code, problems = sbom_hygiene.check_sbom_hygiene(path)

    assert code == 2
    assert any("serialNumber" in problem for problem in problems)


def test_missing_spec_version_is_setup_error(sbom_hygiene, tmp_path):
    payload = _clean_sbom()
    del payload["specVersion"]
    path = _write_sbom(tmp_path, payload)

    code, problems = sbom_hygiene.check_sbom_hygiene(path)

    assert code == 2
    assert any("specVersion" in problem for problem in problems)


def test_invalid_json_is_setup_error(sbom_hygiene, tmp_path):
    path = tmp_path / "sbom.json"
    path.write_text("{not json", encoding="utf-8")

    code, problems = sbom_hygiene.check_sbom_hygiene(path)

    assert code == 2
    assert any("invalid JSON" in problem for problem in problems)
