"""Codex migrations require a complete parser and preserve duplicate environments."""
import builtins
import sys

import pytest

from piia_engram import setup_wizard as W


def no_toml_import(monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name in {'tomllib', 'tomli'}:
            raise ImportError('parser unavailable')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', missing)


@pytest.mark.parametrize('exists', [True, False])
def test_missing_parser_requires_manual_step_without_writes(tmp_path, monkeypatch, capsys, exists):
    config = tmp_path / 'config.toml'
    original = ('[mcp_servers]\npiia-engram = { command = "python", '
                'args = ["-m", "piia_engram.mcp_server"], env = { ENGRAM_APPROVAL = "strict" } }\n')
    if exists:
        config.write_text(original, encoding='utf-8')
    no_toml_import(monkeypatch)
    tool = {'id': 'codex', 'name': 'Codex', 'format': 'toml', 'config_path': config}
    success, failed, manual = W._apply_external_configs(
        [tool], sys.executable, 'server.py', str(tmp_path / 'store'),
        interactive=False)
    assert (success, failed, manual) == ([], [], ['Codex'])
    if exists:
        assert config.read_text(encoding='utf-8') == original
    else:
        assert not config.exists()
    assert not list(tmp_path.glob('config.toml.engram-backup.*'))
    output = capsys.readouterr().out
    assert '[mcp_servers.engram]' in output and 'ENGRAM_APPROVAL' in output


def entry(name, form, env):
    key = {'bare': name, 'double': f'"{name}"', 'single': f"'{name}'"}[form]
    return (f'[mcp_servers.{key}]\ncommand = "python"\n'
            'args = ["-m", "piia_engram.mcp_server"]\n'
            f'[mcp_servers.{key}.env]\n' + ''.join(f'{k} = "{v}"\n' for k, v in env.items()))


@pytest.mark.parametrize('form', ['bare', 'double', 'single'])
@pytest.mark.parametrize('inline', [False, True])
@pytest.mark.parametrize('both', [False, True])
def test_all_key_forms_collapse_aliases_with_canonical_env_precedence(tmp_path, form, inline, both):
    legacy_env = {'ENGRAM_APPROVAL': 'strict', 'LEGACY_ONLY': 'keep', 'SHARED': 'legacy'}
    canonical_env = {'SHARED': 'canonical', 'CANONICAL_ONLY': 'keep'}
    if inline:
        def inline_entry(name, env):
            key = {'bare': name, 'double': f'"{name}"', 'single': f"'{name}'"}[form]
            body = ', '.join(f'{k} = "{v}"' for k, v in env.items())
            return (f'{key} = {{ command = "python", args = ["-m", "piia_engram.mcp_server"], '
                    f'env = {{ {body} }} }}\n')
        original = '[mcp_servers]\n' + inline_entry('piia-engram', legacy_env)
        if both:
            original += inline_entry('engram', canonical_env)
        original += 'peer = { command = "peer" }\n'
    else:
        original = entry('piia-engram', form, legacy_env)
        if both:
            original += entry('engram', form, canonical_env)
        original += '[mcp_servers.peer]\ncommand = "peer"\n'
    original = 'model = "example"\n' + original + '[features]\npreview = true\n'
    config = tmp_path / 'config.toml'
    config.write_text(original, encoding='utf-8')
    W._write_mcp_config_toml(config, sys.executable, 'server.py')
    parsed = W._parse_toml(config.read_text(encoding='utf-8'))
    assert set(parsed['mcp_servers']) == {'engram', 'peer'}
    env = parsed['mcp_servers']['engram']['env']
    assert env['ENGRAM_APPROVAL'] == 'strict' and env['LEGACY_ONLY'] == 'keep'
    assert env['SHARED'] == ('canonical' if both else 'legacy')
    assert parsed['features']['preview'] is True and parsed['model'] == 'example'
    assert next(tmp_path.glob('config.toml.engram-backup.*')).read_text(encoding='utf-8') == original
    first = config.read_bytes()
    W._write_mcp_config_toml(config, sys.executable, 'server.py')
    assert config.read_bytes() == first


def test_rewritten_toml_is_validated_before_backup_or_write(tmp_path, monkeypatch):
    config = tmp_path / 'config.toml'
    original = '[mcp_servers.peer]\ncommand = "peer"\n'
    config.write_text(original, encoding='utf-8')
    parse = W._parse_toml

    def reject_rewrite(text, **kwargs):
        if '[mcp_servers.engram]' in text:
            raise ValueError('injected validation failure')
        return parse(text, **kwargs)

    monkeypatch.setattr(W, '_parse_toml', reject_rewrite)
    with pytest.raises(ValueError, match='validation'):
        W._write_mcp_config_toml(config, sys.executable, 'server.py')
    assert config.read_text(encoding='utf-8') == original
    assert not list(tmp_path.glob('config.toml.engram-backup.*'))


def test_quoted_headers_with_whitespace_and_comments_are_recognized(tmp_path):
    config = tmp_path / 'config.toml'
    original = ("[ mcp_servers . 'piia-engram' ] # legacy\ncommand = 'python'\n"
                "args = ['-m', 'piia_engram.mcp_server']\n"
                "[ mcp_servers . 'piia-engram' . env ] # env\nENGRAM_APPROVAL = 'strict'\n"
                '[mcp_servers."engram"] # canonical\ncommand = "python"\n'
                'args = ["-m", "piia_engram.mcp_server"]\n'
                '[mcp_servers."engram".env]\nCUSTOM = "keep"\n')
    config.write_text(original, encoding='utf-8')
    W._write_mcp_config_toml(config, sys.executable, 'server.py')
    servers = W._parse_toml(config.read_text(encoding='utf-8'))['mcp_servers']
    assert set(servers) == {'engram'}
    assert servers['engram']['env']['ENGRAM_APPROVAL'] == 'strict'
    assert servers['engram']['env']['CUSTOM'] == 'keep'


def test_unrelated_legacy_named_server_is_preserved(tmp_path):
    config = tmp_path / 'config.toml'
    config.write_text('[mcp_servers."piia-engram"]\ncommand = "peer"\n', encoding='utf-8')
    W._write_mcp_config_toml(config, sys.executable, 'server.py')
    servers = W._parse_toml(config.read_text(encoding='utf-8'))['mcp_servers']
    assert servers['piia-engram']['command'] == 'peer'


@pytest.mark.parametrize('name', ['engram', 'piia-engram'])
def test_tool_specific_subtables_survive_migration(tmp_path, name):
    config = tmp_path / 'config.toml'
    original = (entry(name, 'single', {'ENGRAM_APPROVAL': 'strict'}) +
                f'[mcp_servers.{name}.tools.search_knowledge]\napproval_mode = "approve"\n')
    config.write_text(original, encoding='utf-8')
    W._write_mcp_config_toml(config, sys.executable, 'server.py')
    servers = W._parse_toml(config.read_text(encoding='utf-8'))['mcp_servers']
    assert set(servers) == {'engram'}
    assert servers['engram']['tools']['search_knowledge']['approval_mode'] == 'approve'
