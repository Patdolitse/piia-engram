"""Regressions for configuration, identity recovery and read-only release boundaries."""
import importlib
import io
import json
import sys
import threading

import pytest

from piia_engram import core, identity_review, setup_wizard as setup, storage, tombstones
from piia_engram.core import Engram


def preview(path):
    return setup._write_mcp_config_toml(path, 'python', 'server.py', preview=True)


@pytest.mark.parametrize('inline', [False, True])
@pytest.mark.parametrize('legacy', [False, True])
def test_codex_preserves_all_owner_controls(tmp_path, inline, legacy):
    name = 'piia-engram' if legacy else 'engram'
    values = ('enabled = false, disabled_tools = ["memory_store"], '
              'enabled_tools = ["get_profile"], startup_timeout_sec = 60, '
              'tool_timeout_sec = 75, "unknown.control" = { value = [1, true] }')
    launch = 'command = "old", args = ["-m", "piia_engram.mcp_server"]'
    text = (f'[mcp_servers]\n{name} = {{ {launch}, {values}, env = {{ CUSTOM = "keep" }} }}\n'
            if inline else f'[mcp_servers.{name}]\n' + launch.replace(', args', '\nargs')
            + '\n' + values.replace(', disabled', '\ndisabled').replace(', enabled', '\nenabled')
            .replace(', startup', '\nstartup').replace(', tool', '\ntool')
            .replace(', "unknown', '\n"unknown') + f'\n[mcp_servers.{name}.env]\nCUSTOM = "keep"\n')
    path = tmp_path / 'config.toml'
    path.write_text(text, encoding='utf-8')
    before = setup._parse_toml(text)['mcp_servers'][name]
    after = setup._parse_toml(preview(path))['mcp_servers']['engram']
    for key in set(before) - {'command', 'args', 'env'}:
        assert after.get(key) == before[key], key
    assert after['env']['CUSTOM'] == 'keep'
    assert path.read_text(encoding='utf-8') == text


def test_codex_legacy_merge_keeps_controls_and_refuses_conflict(tmp_path, capsys):
    path = tmp_path / 'config.toml'
    text = ('[mcp_servers.engram]\ncommand = "old"\nenabled = false\n'
            '[mcp_servers.piia-engram]\ncommand = "python"\n'
            'args = ["-m", "piia_engram.mcp_server"]\ndisabled_tools = ["memory_store"]\n')
    path.write_text(text, encoding='utf-8')
    candidate = setup._parse_toml(preview(path))['mcp_servers']
    assert candidate['engram']['enabled'] is False
    assert candidate['engram']['disabled_tools'] == ['memory_store']
    assert 'piia-engram' not in candidate
    text += 'enabled = true\n'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(setup._ManualTomlStep):
        preview(path)
    assert path.read_text(encoding='utf-8') == text
    assert '[mcp_servers.engram]' in capsys.readouterr().out


def test_codex_unrelated_guard_includes_owner_keys():
    old = {'mcp_servers': {'engram': {'command': 'old', 'enabled': False}}}
    new = {'mcp_servers': {'engram': {'command': 'new', 'enabled': True}}}
    assert not setup._toml_values_identical(setup._toml_unrelated_values(old, {'engram'}),
                                          setup._toml_unrelated_values(new, {'engram'}))


@pytest.mark.parametrize('field,approved,local', [
    ('work_style', {'communication': 'approved'}, {'preferences': {'pace': 'new'}}),
    ('profile', {'role': 'approved'}, {'description': 'local'}),
    ('preferences', {'communication': 'approved'}, {'tool_preferences': {'editor': 'local'}}),
    ('quality_standards', {'rules': ['approved']}, {'review_checklist': ['local']}),
    ('trust_boundaries', {'restricted_fields': ['role']}, {'notes': 'local'}),
])
def test_local_unrelated_identity_update_cannot_lose_approval(tmp_path, monkeypatch, field, approved, local):
    eng = Engram(tmp_path / 'store')
    row = eng.propose_identity(field, approved)
    ready, resume = threading.Event(), threading.Event()
    errors = []
    # Pause immediately before the local writer's persistence operation. A
    # split read/write has already captured stale state here; atomic RMW has not.
    for name in ('_write_json', '_update_json'):
        original = getattr(core, name)
        def paused(*args, _original=original, **kwargs):
            if threading.current_thread().name == 'local-identity-writer':
                ready.set()
                assert resume.wait(5)
            return _original(*args, **kwargs)
        monkeypatch.setattr(core, name, paused)
    def writer():
        try:
            getattr(eng, 'update_' + field)(local)
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=writer, name='local-identity-writer')
    thread.start()
    try:
        assert ready.wait(5)
        assert eng.review_identity_proposal(row['id'], 'approve')['status'] == 'applied'
    finally:
        resume.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    result = getattr(eng, 'get_' + field)()
    assert all(result.get(k) == v for k, v in approved.items())
    assert all(result.get(k) == v for k, v in local.items())


@pytest.mark.parametrize('after_write', [False, True])
def test_native_backup_roundtrip_preserves_identity_queue_and_recovery(tmp_path, monkeypatch, after_write):
    source = Engram(tmp_path / 'source')
    pending = source.propose_identity('profile', {'role': 'pending'})
    rejected = source.propose_identity('work_style', {'communication': 'rejected'})
    source.review_identity_proposal(rejected['id'], 'reject')
    recovering = source.propose_identity('quality_standards', {'rules': ['recovering']})
    original = source.update_quality_standards
    def interrupted(updates):
        if after_write:
            original(updates)
        raise OSError('injected interruption')
    with monkeypatch.context() as patch:
        patch.setattr(source, 'update_quality_standards', interrupted)
        with pytest.raises(OSError):
            source.review_identity_proposal(recovering['id'], 'approve')
    backup = tmp_path / 'backup.json'
    source.export_all(str(backup))
    data = json.loads(backup.read_text(encoding='utf-8'))
    assert data['identity_review']['schema_version'] == 1
    target = Engram(tmp_path / 'target')
    monkeypatch.setattr(target, 'recover_identity_proposals', lambda: pytest.fail('import must not recover'))
    for merge in (True, True, False):
        assert 'error' not in target.import_all(str(backup), merge=merge)
        rows = target.get_identity_proposals(include_decided=True)
        assert len(rows) == 3
        assert {r['id']: r['status'] for r in rows} == {
            pending['id']: 'pending', rejected['id']: 'rejected', recovering['id']: 'applying'}
        assert target.get_profile().get('role') != 'pending'
        assert tombstones.by_id(target.root, rejected['id'])
    assert target.propose_identity('work_style', {'communication': 'rejected'})['status'] == 'rejected_before'
    assert target.review_identity_proposal(recovering['id'], 'approve')['status'] == 'applied'
    assert target.get_quality_standards()['rules'] == ['recovering']


@pytest.mark.parametrize('relative,method', [
    ('identity/profile.json', 'get_profile'),
    ('identity/quality_standards.json', 'get_quality_standards'),
    ('knowledge/lessons.json', 'get_lessons'),
])
def test_read_only_corruption_never_copies_or_writes(tmp_path, monkeypatch, relative, method):
    root = tmp_path / 'store'
    Engram(root)
    path = root / relative
    path.write_text('{damaged', encoding='utf-8')
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    copies = []
    monkeypatch.setattr(storage.shutil, 'copy2', lambda *a, **k: copies.append(a))
    with pytest.raises(storage.DataCorruptionError):
        getattr(Engram(root, read_only=True), method)()
    assert copies == []
    assert before == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('module', ['auto_inject_resume_brief', 'cursor_inject_resume_brief'])
def test_session_start_worker_corruption_does_not_quarantine(tmp_path, monkeypatch, capsys, module):
    root = tmp_path / 'store'
    Engram(root)
    (root / 'identity' / 'profile.json').write_text('{damaged', encoding='utf-8')
    monkeypatch.setenv('ENGRAM_DIR', str(root))
    monkeypatch.setenv('ENGRAM_HOOK_READ_TIMEOUT_SECONDS', '3')
    monkeypatch.delenv('CLAUDE_INVOKED_BY', raising=False)
    monkeypatch.setattr(sys, 'argv', ['hook'])
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    from piia_engram.hooks import _log
    monkeypatch.setattr(_log, 'log_failure', lambda *a, **k: None)
    hook = importlib.import_module('piia_engram.hooks.' + module)
    monkeypatch.setattr(hook, 'log_failure', lambda *a, **k: None)
    copies = []
    monkeypatch.setattr(storage.shutil, 'copy2', lambda *a, **k: copies.append(a))
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out)['continue'] is True
    assert copies == []
    assert before == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('args', [['migrate-project', 'fixture'], ['preview'], ['status'],
                                  ['--help'], ['capabilities'], ['review', 'export']])
def test_read_only_cli_dispatch_skips_startup_side_effects(monkeypatch, args):
    from piia_engram import update_check
    hits = []
    monkeypatch.setattr(update_check, 'maybe_print_update_notice', lambda: hits.append('update'))
    monkeypatch.setattr(setup, '_show_usage_notice', lambda *a: hits.append('notice'))
    monkeypatch.setattr(setup, '_start_usage_ping_cli', lambda: hits.append('ping'))
    monkeypatch.setattr(setup, '_configure_utf8_stdio', lambda: None)
    for name in ('run_preview', 'run_status', 'run_review', '_run_capabilities_cli'):
        monkeypatch.setattr(setup, name, lambda *a: 0)
    monkeypatch.setattr(sys, 'argv', ['engram', *args])
    try:
        setup.main()
    except SystemExit:
        pass
    assert hits == []


@pytest.mark.parametrize('mutation', ['version', 'status', 'keys', 'duplicate', 'missing', 'version_bool'])
def test_invalid_identity_backup_refused_before_any_store_write(tmp_path, mutation):
    source = Engram(tmp_path / 'source')
    row = source.propose_identity('profile', {'role': 'pending'})
    section = {'schema_version': 1, 'proposals': [row]}
    if mutation == 'version':
        section['schema_version'] = 2
    elif mutation == 'status':
        row['status'] = 'verified'
    elif mutation == 'keys':
        row['after']['owner_override'] = True
    elif mutation == 'duplicate':
        section['proposals'].append(row)
    elif mutation == 'missing':
        row.pop('before')
    else:
        row['version'] = True
    backup = tmp_path / 'invalid.json'
    backup.write_text(json.dumps({'schema_version': '2.0', 'identity_review': section,
                                 'identity': {'profile': {'role': 'must not apply'}}}), encoding='utf-8')
    target = Engram(tmp_path / 'target')
    before = {str(p.relative_to(target.root)): p.read_bytes()
              for p in target.root.rglob('*') if p.is_file()}
    for dry_run in (True, False):
        result = target.import_all(str(backup), dry_run=dry_run)
        assert result.get('error') == 'invalid_identity_review'
        assert before == {str(p.relative_to(target.root)): p.read_bytes()
                          for p in target.root.rglob('*') if p.is_file()}


def test_old_backup_cannot_resurrect_local_identity_rejection(tmp_path):
    eng = Engram(tmp_path / 'store')
    row = eng.propose_identity('profile', {'role': 'pending'})
    backup = tmp_path / 'pending.json'
    eng.export_all(str(backup))
    eng.review_identity_proposal(row['id'], 'reject')
    for merge in (True, False):
        assert 'error' not in eng.import_all(str(backup), merge=merge)
        assert eng.get_identity_proposals() == []
        assert eng.get_identity_proposals(include_decided=True)[0]['status'] == 'rejected'
        assert tombstones.by_id(eng.root, row['id'])


def test_restricted_backup_does_not_export_pending_identity(tmp_path):
    eng = Engram(tmp_path / 'store')
    row = eng.propose_identity('profile', {'role': 'pending secret'})
    backup = tmp_path / 'restricted.json'
    eng.export_all(str(backup), exclude_pending=True)
    data = json.loads(backup.read_text(encoding='utf-8'))
    assert 'identity_review' not in data
    assert row['id'] not in backup.read_text(encoding='utf-8')


def test_backup_queue_is_portable_between_encryption_keys(tmp_path, monkeypatch):
    pytest.importorskip('cryptography')
    monkeypatch.setenv('ENGRAM_SECRET', 'source-key')
    source = Engram(tmp_path / 'source')
    row = source.propose_identity('profile', {'role': 'private pending role'})
    assert 'private pending role' not in (source._identity_dir / 'proposals.json').read_text(encoding='utf-8')
    backup = tmp_path / 'portable.json'
    source.export_all(str(backup))
    monkeypatch.setenv('ENGRAM_SECRET', 'destination-key')
    target = Engram(tmp_path / 'target')
    assert 'error' not in target.import_all(str(backup))
    assert target.get_identity_proposals()[0] == row
    assert 'private pending role' not in (target._identity_dir / 'proposals.json').read_text(encoding='utf-8')
    assert target.get_profile().get('role') != 'private pending role'


def test_identity_backup_semantic_dedup_and_conflicting_id(tmp_path):
    source = Engram(tmp_path / 'source')
    row = source.propose_identity('profile', {'role': 'pending'})
    backup = tmp_path / 'backup.json'
    source.export_all(str(backup))
    target = Engram(tmp_path / 'target')
    local = target.propose_identity('profile', {'role': 'pending'})
    assert local['id'] != row['id']
    assert 'error' not in target.import_all(str(backup))
    assert [r['id'] for r in target.get_identity_proposals()] == [local['id']]
    data = json.loads(backup.read_text(encoding='utf-8'))
    data['identity_review']['proposals'][0]['id'] = local['id']
    data['identity_review']['proposals'][0]['before']['role'] = 'conflicting base'
    data['identity_review']['proposals'][0]['missing_before'] = []
    backup.write_text(json.dumps(data), encoding='utf-8')
    before = {str(p.relative_to(target.root)): p.read_bytes()
              for p in target.root.rglob('*') if p.is_file()}
    assert target.import_all(str(backup)).get('error') == 'invalid_identity_review'
    assert before == {str(p.relative_to(target.root)): p.read_bytes()
                      for p in target.root.rglob('*') if p.is_file()}


def test_codex_preserves_unknown_toml_types_and_nested_controls(tmp_path):
    path = tmp_path / 'config.toml'
    text = ('[mcp_servers.engram]\ncommand="old"\n'
            'owner_date=2026-10-10\nowner_time=12:34:56\nowner_float=nan\n'
            '[mcp_servers.engram.future]\nflag=false\n'
            '[[mcp_servers.engram.future.records]]\nvalue=1\n'
            '[mcp_servers.engram.env]\nCUSTOM_INT=3\nCUSTOM_BOOL=false\n')
    path.write_text(text, encoding='utf-8')
    before = setup._toml_unrelated_values(setup._parse_toml(text), {'engram'})
    after = setup._toml_unrelated_values(setup._parse_toml(preview(path)), {'engram'})
    assert setup._toml_values_identical(before, after)


def test_codex_guard_refuses_accidental_owner_serialization_change(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'config.toml'
    text = '[mcp_servers.engram]\ncommand="old"\nenabled=false\n'
    path.write_text(text, encoding='utf-8')
    original = setup._toml_value
    monkeypatch.setattr(setup, '_toml_value', lambda value: 'true' if value is False else original(value))
    with pytest.raises(setup._ManualTomlStep):
        preview(path)
    assert path.read_text(encoding='utf-8') == text
    assert '[mcp_servers.engram]' in capsys.readouterr().out
