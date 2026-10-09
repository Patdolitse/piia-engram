"""The scriptable installer is plan-first, metadata-only and prompt-free."""
import builtins
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from piia_engram import claude_code_mcp as C
from piia_engram import setup_wizard as W


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None
            for p in root.rglob('*')}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    store = tmp_path / 'store'
    for key, path in {
        'HOME': home, 'USERPROFILE': home, 'ENGRAM_DIR': store,
        'APPDATA': home / 'AppData', 'LOCALAPPDATA': home / 'LocalAppData',
        'CLAUDE_CONFIG_DIR': home / '.claude',
    }.items():
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv('DO_NOT_TRACK', '1')
    monkeypatch.setenv('ENGRAM_NO_UPDATE_CHECK', '1')
    monkeypatch.setattr(W, '_find_python', lambda: sys.executable)
    monkeypatch.setattr(W, '_find_mcp_server', lambda: 'server.py')
    monkeypatch.setattr(W, '_configure_utf8_stdio', lambda: None)

    def forbidden(*args, **kwargs):
        raise AssertionError('no stdin, wizard, identity, memory, notice or network')

    monkeypatch.setattr(builtins, 'input', forbidden)
    monkeypatch.setattr(W, 'run_setup', forbidden)
    monkeypatch.setattr(W, '_show_usage_notice', forbidden)
    monkeypatch.setattr(W, '_start_usage_ping_cli', forbidden)
    from piia_engram.core import Engram
    from piia_engram import update_check
    monkeypatch.setattr(Engram, '__init__', forbidden)
    monkeypatch.setattr(update_check, 'maybe_print_update_notice', forbidden)
    return home, store


def cli(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, 'argv', ['engram', 'setup', *args])
    with pytest.raises(SystemExit) as exc:
        W.main()
    output = capsys.readouterr()
    return exc.value.code, output


def config(home, name='cursor', text='{"mcpServers": {}}\r\n'):
    path = home / ('.cursor/mcp.json' if name == 'cursor' else '.codex/config.toml')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode())
    return path


@pytest.mark.parametrize('existing_store', [False, True])
def test_plan_has_byte_level_zero_writes(sandbox, tmp_path, monkeypatch, capsys, existing_store):
    home, store = sandbox
    config(home)
    config(home, 'codex', 'model = "sample"\r\n')
    if existing_store:
        store.mkdir()
        (store / 'identity.json').write_bytes(b'{"name":"DO_NOT_TOUCH"}')
        (store / 'lessons.json').write_bytes(b'[{"summary":"DO_NOT_TOUCH"}]')
    before = snapshot(tmp_path)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--json')
    assert code == 0
    assert snapshot(tmp_path) == before
    assert json.loads(output.out)['mode'] == 'plan'
    assert not output.err


def test_apply_only_planned_files_backups_and_ledger(sandbox, tmp_path, monkeypatch, capsys):
    home, store = sandbox
    path = config(home, text='{"mcpServers": {"peer": {"command": "sample"}}}\r\n')
    original = path.read_bytes()
    store.mkdir()
    (store / 'identity.json').write_bytes(b'{"name":"unchanged"}')
    (store / 'lessons.json').write_bytes(b'[]')
    before = snapshot(tmp_path)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--json')
    plan = json.loads(output.out)
    row = next(r for r in plan['clients'] if r['id'] == 'cursor')
    assert row['action'] == 'write' and row['writes'] == ['~/.cursor/mcp.json']
    assert {r['kind'] for r in plan['store']['writes']} == {'backups', 'ledger'}
    from piia_engram import file_safety
    calls = []
    real = file_safety.write_external_config_text
    def write(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)
    monkeypatch.setattr(file_safety, 'write_external_config_text', write)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')
    assert code == 0 and len(calls) == 1 and calls[0][1]['authorized'] is True
    after = snapshot(tmp_path)
    changed = {p for p in after if after[p] != before.get(p)}
    assert all(p == 'home/.cursor/mcp.json' or p.startswith('store/backups/')
               or p == 'store/file_safety_ledger.jsonl' for p in changed if after[p] is not None)
    backups = list((store / 'backups/file_safety/external').glob('*.bak'))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert json.loads(path.read_text())['mcpServers']['peer'] == {'command': 'sample'}
    assert json.loads(output.out)['clients'][1]['result'] == 'written'
    before = snapshot(tmp_path)
    assert cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')[0] == 0
    assert snapshot(tmp_path) == before


def test_filter_and_tool_mode_preservation(sandbox, monkeypatch, capsys):
    home, _ = sandbox
    cursor = config(home, text='{"mcpServers":{"engram":{"env":{"ENGRAM_TOOLS":"core","API_TOKEN":"private"}}}}')
    codex = config(home, 'codex', 'model = "sample"\n')
    before = codex.read_bytes()
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--clients', 'cursor', '--json')
    assert code == 0 and codex.read_bytes() == before
    env = json.loads(cursor.read_text())['mcpServers']['engram']['env']
    assert env['ENGRAM_TOOLS'] == 'core' and env['API_TOKEN'] == 'private'
    row = next(r for r in json.loads(output.out)['clients'] if r['id'] == 'codex')
    assert row['detected'] and not row['selected'] and row['action'] == 'none'


@pytest.mark.parametrize('apply', [False, True])
def test_missing_selected_client_is_partial_without_writes(sandbox, tmp_path, monkeypatch, capsys, apply):
    before = snapshot(tmp_path)
    args = ['--non-interactive', '--clients', 'cursor', '--json'] + (['--apply'] if apply else [])
    code, output = cli(monkeypatch, capsys, *args)
    assert code == 1 and json.loads(output.out)['result'] == 'partial'
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('args', [
    ['--apply'], ['--json'], ['--non-interactive', '--lang', 'de'],
    ['--non-interactive', '--clients', 'unknown'], ['--non-interactive', '--clients', ''],
    ['--non-interactive', '--clients', 'cursor,'], ['--non-interactive', '--wat'],
    ['--non-interactive', '--advanced'], ['--non-interactive', '--apply-external-config'],
])
def test_usage_errors_do_not_start_wizard_or_write(sandbox, tmp_path, monkeypatch, capsys, args):
    before = snapshot(tmp_path)
    code, output = cli(monkeypatch, capsys, *args, '--json')
    assert code == 2 and json.loads(output.out)['result'] == 'usage_error'
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('apply', [False, True])
@pytest.mark.parametrize('stdin', [io.StringIO(), None])
def test_stdin_never_read(sandbox, monkeypatch, capsys, apply, stdin):
    class Raising:
        def __getattr__(self, name):
            raise AssertionError('stdin must not be accessed')
    if stdin is not None:
        stdin.close()
    monkeypatch.setattr(sys, 'stdin', stdin if stdin is not None else Raising())
    code, _ = cli(monkeypatch, capsys, '--non-interactive', *(['--apply'] if apply else []))
    assert code == 0


@pytest.mark.parametrize('apply', [False, True])
def test_invalid_config_is_manual_and_retains_bytes(sandbox, tmp_path, monkeypatch, capsys, apply):
    home, _ = sandbox
    config(home, text='{broken SECRET_CONFIG_BODY')
    before = snapshot(tmp_path)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--json', *(['--apply'] if apply else []))
    assert code == 1 and snapshot(tmp_path) == before
    assert 'SECRET_CONFIG_BODY' not in output.out + output.err


def test_claude_preview_then_existing_register_with_mock_cli(sandbox, monkeypatch, capsys):
    home, _ = sandbox
    (home / '.claude').mkdir()
    monkeypatch.setattr(C, 'cli_path', lambda: 'mock-claude.exe')
    calls = []
    def run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='', stderr='')
    monkeypatch.setattr(C, 'run_cli', run)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--json')
    assert code == 0 and not calls
    assert json.loads(output.out)['clients'][0]['action'] == 'register'
    existing = W._register_claude_code
    registrations = []
    def register(*args, **kwargs):
        registrations.append(kwargs)
        return existing(*args, **kwargs)
    monkeypatch.setattr(W, '_register_claude_code', register)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')
    assert code == 0 and registrations == [{'engram_tools': None, 'interactive': False}]
    assert len(calls) == 1 and calls[0][1:6] == ['mcp', 'add', '--scope', 'user', 'engram']
    assert not C.user_config_path().exists()  # only the mock CLI was used
    assert json.loads(output.out)['clients'][0]['result'] == 'registered'


@pytest.mark.parametrize('failure', ['missing', 'error', 'conflict', 'malformed'])
def test_claude_manual_failures_never_leak(sandbox, tmp_path, monkeypatch, capsys, failure):
    home, _ = sandbox
    (home / '.claude').mkdir()
    secret = 'SECRET_DO_NOT_PRINT'
    if failure != 'missing':
        monkeypatch.setattr(C, 'cli_path', lambda: 'mock-claude.exe')
    if failure == 'conflict':
        C.user_config_path().write_text(json.dumps({'mcpServers': {'engram': {'command': secret}}}))
    if failure == 'malformed':
        C.user_config_path().write_text('{broken ' + secret)
    calls = []
    def run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, stdout=secret, stderr=secret)
    monkeypatch.setattr(C, 'run_cli', run)
    monkeypatch.setenv('ENGRAM_SEARCH', secret)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')
    assert code == 1 and secret not in output.out + output.err
    row = json.loads(output.out)['clients'][0]
    assert row['manual_command'] and 'mcp add --scope user engram' in row['manual_command']
    assert len(calls) == (1 if failure == 'error' else 0)


@pytest.mark.parametrize('apply', [False, True])
def test_json_schema_snapshot(sandbox, monkeypatch, capsys, apply):
    monkeypatch.delenv('ENGRAM_DIR')
    monkeypatch.setattr(W, '_tool_configs', lambda: {
        'cursor': {'name': 'Cursor', 'config_paths': [Path.home() / '.cursor/mcp.json']}})
    expected = json.loads((Path(__file__).parent / 'fixtures/agent_setup_plan.json').read_text())
    if apply:
        expected['mode'] = 'apply'
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--json', *(['--apply'] if apply else []))
    assert code == 0 and json.loads(output.out) == expected


def test_module_cli_closed_stdin_without_opt_out(sandbox, tmp_path):
    # Exercise the real module entry with a closed input pipe. The fixture
    # above separately proves notices/ping are bypassed at dispatch.
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    before = snapshot(tmp_path)
    result = subprocess.run([sys.executable, '-m', 'piia_engram.setup_wizard',
                             'setup', '--non-interactive', '--json'],
                            env=env, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, encoding='utf-8', timeout=30, cwd=tmp_path)
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout)['mode'] == 'plan'
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('latched', [False, True])
def test_strict_is_preserved_without_identity_knowledge_or_approval(sandbox, tmp_path, monkeypatch, capsys, latched):
    home, store = sandbox
    path = config(home)
    if latched:
        store.mkdir()
        (store / 'approval_mode.json').write_bytes(b'{"mode":"strict"}')
    else:
        monkeypatch.setenv('ENGRAM_APPROVAL', 'strict')
    code, _ = cli(monkeypatch, capsys, '--non-interactive', '--apply')
    assert code == 0
    assert json.loads(path.read_text())['mcpServers']['engram']['env']['ENGRAM_APPROVAL'] == 'strict'
    assert not (store / 'identity.json').exists()
    assert not (store / 'lessons.json').exists()
    if latched:
        assert (store / 'approval_mode.json').read_bytes() == b'{"mode":"strict"}'


def test_changed_target_before_apply_is_not_overwritten(sandbox, monkeypatch, capsys):
    home, _ = sandbox
    path = config(home)
    from piia_engram import agent_setup as A
    real = A._apply
    edited = b'{"mcpServers":{"peer":{"command":"edited-locally"}}}'
    def changed(store, plans):
        path.write_bytes(edited)
        return real(store, plans)
    monkeypatch.setattr(A, '_apply', changed)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')
    assert code == 1 and path.read_bytes() == edited
    row = next(r for r in json.loads(output.out)['clients'] if r['id'] == 'cursor')
    assert row['reason'] == 'config_changed'


def test_apply_error_is_partial_and_does_not_disclose_exception(sandbox, monkeypatch, capsys):
    home, _ = sandbox
    config(home)
    def failure(*args, **kwargs):
        raise PermissionError('SECRET_EXCEPTION_BODY')
    monkeypatch.setattr(W, '_write_config_text_with_backup', failure)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')
    assert code == 1 and 'SECRET_EXCEPTION_BODY' not in output.out + output.err
    assert json.loads(output.out)['clients'][1]['result'] == 'failed'


def test_json_hides_environment_config_and_managed_env_values(sandbox, monkeypatch, capsys):
    home, _ = sandbox
    config(home, text='{"mcpServers":{"engram":{"env":{"ENGRAM_TOOLS":"SECRET_MODE",'
                      '"ENGRAM_SEARCH":"SECRET_SEARCH","TOKEN":"SECRET_TOKEN"}}},"other":"SECRET_BODY"}')
    monkeypatch.setenv('UNRELATED_PRIVATE_ENV', 'SECRET_ENV')
    for args in [[], ['--apply']]:
        code, output = cli(monkeypatch, capsys, '--non-interactive', '--json', *args)
        assert code == 0
        assert not any(secret in output.out + output.err for secret in
                       ['SECRET_MODE', 'SECRET_SEARCH', 'SECRET_TOKEN', 'SECRET_BODY', 'SECRET_ENV',
                        os.environ['HOME'], os.environ['ENGRAM_DIR'], os.environ['APPDATA']])


def test_language_does_not_change_identity_or_interactive_language(sandbox, monkeypatch, capsys):
    before = W._get_lang()
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--lang', 'zh', '--json')
    assert code == 0 and json.loads(output.out)['language'] == 'zh'
    assert W._get_lang() == before


def test_codex_apply_uses_preview_text_and_keeps_custom_mode(sandbox, monkeypatch, capsys):
    home, store = sandbox
    original = ('model = "sample"\n[mcp_servers.engram]\ncommand = "python"\n'
                'args = ["-m", "piia_engram.mcp_server"]\n'
                '[mcp_servers.engram.env]\nENGRAM_TOOLS = "core"\nTOKEN = "SECRET_TOKEN"\n')
    path = config(home, 'codex', original)
    from piia_engram import agent_setup as A
    real = A._apply
    rendered = []
    def apply(store, plans):
        rendered.extend(p.text for p in plans if p.row['id'] == 'codex')
        return real(store, plans)
    monkeypatch.setattr(A, '_apply', apply)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--clients', 'codex', '--json')
    assert code == 0 and path.read_text() == rendered[0]
    parsed = W._parse_toml(path.read_text(), require_complete=True)
    assert parsed['model'] == 'sample'
    assert parsed['mcp_servers']['engram']['env']['ENGRAM_TOOLS'] == 'core'
    assert parsed['mcp_servers']['engram']['env']['TOKEN'] == 'SECRET_TOKEN'
    assert 'SECRET_TOKEN' not in output.out + output.err
    backups = list((store / 'backups/file_safety/external').glob('*.bak'))
    assert len(backups) == 1 and backups[0].read_bytes() == original.encode()


def test_missing_toml_parser_is_manual_and_does_not_print_config(sandbox, tmp_path, monkeypatch, capsys):
    home, _ = sandbox
    config(home, 'codex', 'model = "SECRET_MODEL"\n')
    before = snapshot(tmp_path)
    real = builtins.__import__
    def missing(name, *args, **kwargs):
        if name in ('tomllib', 'tomli'):
            raise ImportError('SECRET_IMPORT_ERROR')
        return real(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')
    assert code == 1 and snapshot(tmp_path) == before
    assert 'SECRET_' not in output.out + output.err


def test_invalid_store_is_partial_without_writes(sandbox, tmp_path, monkeypatch, capsys):
    home, store = sandbox
    config(home)
    store.write_bytes(b'not a directory')
    before = snapshot(tmp_path)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')
    assert code == 1 and snapshot(tmp_path) == before
    assert json.loads(output.out)['store']['status'] == 'invalid'


def test_claude_identical_and_differing_entries_remain_untouched(sandbox, tmp_path, monkeypatch, capsys):
    home, store = sandbox
    (home / '.claude').mkdir()
    entry = W._engram_server_entry(sys.executable, 'server.py', str(store), engram_tools=None)
    path = C.user_config_path()
    path.write_text(json.dumps({'mcpServers': {'engram': entry}}))
    before = snapshot(tmp_path)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')
    assert code == 0 and snapshot(tmp_path) == before
    assert json.loads(output.out)['clients'][0]['result'] == 'unchanged'
    entry['command'] = 'another-python'
    path.write_text(json.dumps({'mcpServers': {'engram': entry}}))
    before = snapshot(tmp_path)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')
    assert code == 1 and snapshot(tmp_path) == before
    assert json.loads(output.out)['clients'][0]['action'] == 'manual'


def test_apply_continues_safe_clients_when_another_is_manual(sandbox, monkeypatch, capsys):
    home, _ = sandbox
    cursor = config(home, text='{broken')
    codex = config(home, 'codex', 'model = "sample"\n')
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')
    assert code == 1 and cursor.read_bytes() == b'{broken'
    assert 'mcp_servers' in W._parse_toml(codex.read_text(), require_complete=True)
    rows = {r['id']: r for r in json.loads(output.out)['clients']}
    assert rows['cursor']['result'] == 'manual' and rows['codex']['result'] == 'written'


def test_usage_errors_do_not_echo_supplied_secrets(sandbox, monkeypatch, capsys):
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--json', '--lang', 'SECRET_INPUT')
    assert code == 2 and 'SECRET_INPUT' not in output.out + output.err


def test_config_resolving_inside_store_is_refused(sandbox, tmp_path, monkeypatch, capsys):
    _, store = sandbox
    store.mkdir()
    identity = store / 'identity.json'
    identity.write_bytes(b'{"name":"PRIVATE_IDENTITY"}')
    # The same resolution occurs for a client config symlink into the store.
    monkeypatch.setattr(W, '_tool_configs', lambda: {
        'cursor': {'name': 'Cursor', 'config_paths': [identity]}})
    before = snapshot(tmp_path)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')
    assert code == 1 and snapshot(tmp_path) == before
    assert json.loads(output.out)['clients'][0]['reason'] == 'unsafe_config_target'
    assert 'PRIVATE_IDENTITY' not in output.out + output.err


def test_strict_latch_change_before_apply_requires_new_plan(sandbox, monkeypatch, capsys):
    home, store = sandbox
    path = config(home)
    before = path.read_bytes()
    from piia_engram import agent_setup as A
    real = A._apply
    def apply(store, plans):
        store.mkdir()
        (store / 'approval_mode.json').write_bytes(b'{}')
        return real(store, plans)
    monkeypatch.setattr(A, '_apply', apply)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')
    assert code == 1 and path.read_bytes() == before
    assert json.loads(output.out)['clients'][1]['reason'] == 'config_changed'


def test_claude_size_cap_is_checked_before_body_read(sandbox, monkeypatch, capsys):
    home, _ = sandbox
    (home / '.claude').mkdir()
    C.user_config_path().write_bytes(b'oversized private body')
    monkeypatch.setattr(C, 'MAX_USER_CONFIG_BYTES', 4)
    from piia_engram import agent_setup as A
    def forbidden(path):
        raise AssertionError('oversized body must not be read')
    monkeypatch.setattr(A, '_bytes', forbidden)
    code, output = cli(monkeypatch, capsys, '--non-interactive', '--apply', '--json')
    assert code == 1 and 'oversized private body' not in output.out + output.err
