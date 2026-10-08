"""Native backups must never replace store-owned data."""
import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from piia_engram import mcp_server as M
from piia_engram.cli_commands import _run_dock_export
from piia_engram.core import Engram


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('target', ['identity/profile.json', 'identity/proposals.json',
                                   'knowledge/lessons.json', 'knowledge/new/backup.json'])
@pytest.mark.parametrize('entry', ['core', 'summary', 'mcp', 'cli'])
def test_export_refuses_store_destination_before_any_write(tmp_path, monkeypatch, target, entry):
    eng = Engram(root=tmp_path / 'store')
    eng.update_profile({'role': 'old'})
    eng.propose_identity('profile', {'role': 'new'})
    monkeypatch.setattr(M, '_engram', eng)
    monkeypatch.setattr(M, '_session', M._SessionTracker())
    monkeypatch.setenv('ENGRAM_DIR', str(eng.root))
    # Keep this check about export writes, including audit, rather than CLI init.
    monkeypatch.setattr('piia_engram.core.Engram', lambda **kwargs: eng)
    out = str(eng.root / target)
    before = snapshot(eng.root)
    if entry in {'core', 'summary'}:
        with pytest.raises(ValueError, match='store'):
            (eng.export_all if entry == 'core' else eng.export_all_with_summary)(out)
    elif entry == 'mcp':
        result = asyncio.run(M.export_engram(output_path=out))
        assert 'store' in result and '导出成功' not in result
    else:
        assert _run_dock_export(['--output', out, '--json']) == 1
    assert snapshot(eng.root) == before


def test_export_refuses_resolved_directory_alias(tmp_path):
    eng = Engram(root=tmp_path / 'store')
    alias = tmp_path / 'alias'
    if os.name == 'nt':
        # Directory junctions work without the symlink privilege on Windows.
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(eng.root)],
                       check=True, capture_output=True)
    else:
        alias.symlink_to(eng.root, target_is_directory=True)
    before = snapshot(eng.root)
    with pytest.raises(ValueError, match='store'):
        eng.export_all(str(alias / 'identity' / 'profile.json'))
    assert snapshot(eng.root) == before


def test_default_backup_is_outside_store(tmp_path):
    eng = Engram(root=tmp_path / 'store')
    output = Path(eng.export_all())
    assert not output.resolve().is_relative_to(eng.root.resolve())
    assert json.loads(output.read_text(encoding='utf-8'))['schema_version']


@pytest.mark.parametrize('entry', ['core', 'mcp'])
def test_openclaw_export_refuses_store_directory(tmp_path, monkeypatch, entry):
    from piia_engram.compat import export_to_openclaw
    eng = Engram(root=tmp_path / 'store')
    monkeypatch.setattr(M, '_engram', eng)
    before = snapshot(eng.root)
    if entry == 'core':
        with pytest.raises(ValueError, match='store'):
            export_to_openclaw(eng, str(eng.root / 'identity'))
    else:
        result = asyncio.run(M.export_engram(format='openclaw', output_dir=str(eng.root / 'identity')))
        assert 'store' in result and '失败' in result
    assert snapshot(eng.root) == before
