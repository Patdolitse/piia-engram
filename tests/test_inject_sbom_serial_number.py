"""Tests for scripts/inject_sbom_serial_number.py."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "inject_sbom_serial_number.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("_inject_sbom_serial", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def injector():
    return _load_module()


def _reproducible_sbom() -> dict[str, object]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "metadata": {"component": {"type": "library", "name": "piia-engram"}},
        "components": [{"type": "library", "name": "requests"}],
    }


def _write_sbom(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_injects_valid_serial_number(injector, tmp_path):
    path = _write_sbom(tmp_path / "sbom.json", _reproducible_sbom())

    code, message = injector.inject_serial_number(path)
    document = json.loads(path.read_text(encoding="utf-8"))

    assert code == 0
    assert "injected" in message
    assert injector.SERIAL_NUMBER_RE.match(document["serialNumber"])


def test_injection_is_deterministic_for_same_content(injector, tmp_path):
    path_a = _write_sbom(tmp_path / "a.json", _reproducible_sbom())
    path_b = _write_sbom(tmp_path / "b.json", _reproducible_sbom())

    injector.inject_serial_number(path_a)
    injector.inject_serial_number(path_b)

    serial_a = json.loads(path_a.read_text(encoding="utf-8"))["serialNumber"]
    serial_b = json.loads(path_b.read_text(encoding="utf-8"))["serialNumber"]
    assert serial_a == serial_b


def test_different_content_gets_different_serial(injector, tmp_path):
    payload_b = _reproducible_sbom()
    payload_b["components"].append({"type": "library", "name": "rich"})
    path_a = _write_sbom(tmp_path / "a.json", _reproducible_sbom())
    path_b = _write_sbom(tmp_path / "b.json", payload_b)

    injector.inject_serial_number(path_a)
    injector.inject_serial_number(path_b)

    serial_a = json.loads(path_a.read_text(encoding="utf-8"))["serialNumber"]
    serial_b = json.loads(path_b.read_text(encoding="utf-8"))["serialNumber"]
    assert serial_a != serial_b


def test_existing_valid_serial_is_preserved(injector, tmp_path):
    payload = _reproducible_sbom()
    payload["serialNumber"] = "urn:uuid:1f2e3d4c-5b6a-5798-8123-456789abcdef"
    path = _write_sbom(tmp_path / "sbom.json", payload)

    code, message = injector.inject_serial_number(path)
    document = json.loads(path.read_text(encoding="utf-8"))

    assert code == 0
    assert "already present" in message
    assert document["serialNumber"] == "urn:uuid:1f2e3d4c-5b6a-5798-8123-456789abcdef"


def test_rerun_is_idempotent(injector, tmp_path):
    path = _write_sbom(tmp_path / "sbom.json", _reproducible_sbom())

    injector.inject_serial_number(path)
    first = json.loads(path.read_text(encoding="utf-8"))["serialNumber"]
    code, message = injector.inject_serial_number(path)
    second = json.loads(path.read_text(encoding="utf-8"))["serialNumber"]

    assert code == 0
    assert "already present" in message
    assert first == second


def test_invalid_json_is_setup_error(injector, tmp_path):
    path = tmp_path / "sbom.json"
    path.write_text("{not json", encoding="utf-8")

    code, message = injector.inject_serial_number(path)

    assert code == 2
    assert "cannot load" in message
