"""Static guard for public Cloudflare Worker configuration."""

from __future__ import annotations

from pathlib import Path

import importlib.util
import sys


_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "check_worker_public_config.py"
_WRANGLER = _ROOT / "worker" / "wrangler.toml"


def _load_module():
    spec = importlib.util.spec_from_file_location("_worker_public_config", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_worker_wrangler_toml_uses_placeholder_database_id():
    text = _WRANGLER.read_text(encoding="utf-8")
    assert 'database_id = "<cloudflare-d1-database-id>"' in text
    assert "e06d9bea-2353-4434-9ce7-7a81717db3ef" not in text


def test_worker_public_config_guard_flags_real_d1_id_without_echoing_value(tmp_path):
    mod = _load_module()
    real_id = "e06d9bea-2353-4434-9ce7-7a81717db3ef"
    cfg = tmp_path / "wrangler.toml"
    cfg.write_text(
        "\n".join([
            'name = "example"',
            "[[d1_databases]]",
            'binding = "DB"',
            'database_id = "' + real_id + '"',
        ]),
        encoding="utf-8",
    )

    findings = mod.scan_paths([cfg])

    assert len(findings) == 1
    assert findings[0].key == "database_id"
    assert real_id not in findings[0].message()


def test_worker_public_config_guard_messages_use_repo_relative_paths():
    mod = _load_module()
    finding = mod.Finding(path=_WRANGLER, line=8, key="database_id")
    message = finding.message()

    assert message.startswith("worker/wrangler.toml:8:")
    assert str(_ROOT) not in message
    assert str(_ROOT).replace("\\", "/") not in message


def test_worker_public_config_guard_allows_placeholders(tmp_path):
    mod = _load_module()
    cfg = tmp_path / "wrangler.toml"
    cfg.write_text(
        "\n".join([
            'name = "example"',
            "[[d1_databases]]",
            'binding = "DB"',
            'database_id = "<cloudflare-d1-database-id>"',
        ]),
        encoding="utf-8",
    )

    assert mod.scan_paths([cfg]) == []
