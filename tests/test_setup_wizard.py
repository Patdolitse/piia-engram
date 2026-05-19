"""setup_wizard 辅助函数单元测试。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from engram_core.setup_wizard import (
    _find_mcp_server,
    _find_python,
    _read_mcp_config,
    _write_mcp_config,
)


def test_find_python():
    """_find_python 应能找到当前运行的 Python。"""
    result = _find_python()
    assert result is not None
    assert Path(result).is_file()


def test_find_mcp_server():
    """_find_mcp_server 应能找到已安装的 mcp_server.py。"""
    result = _find_mcp_server()
    assert result is not None
    assert Path(result).is_file()
    assert result.endswith("mcp_server.py")


def test_write_mcp_config_creates_file(tmp_path: Path):
    """_write_mcp_config 应在新路径创建配置文件。"""
    config_path = tmp_path / "test_mcp.json"
    _write_mcp_config(config_path, "/usr/bin/python3", "/path/to/mcp_server.py")
    assert config_path.is_file()
    config = json.loads(config_path.read_text())
    assert "mcpServers" in config
    assert "engram" in config["mcpServers"]
    assert config["mcpServers"]["engram"]["command"] == "/usr/bin/python3"
    assert config["mcpServers"]["engram"]["args"] == ["/path/to/mcp_server.py"]


def test_write_mcp_config_merges(tmp_path: Path):
    """_write_mcp_config 应保留文件中已有的其他工具配置。"""
    config_path = tmp_path / "mcp.json"
    existing = {"mcpServers": {"other-tool": {"command": "node", "args": ["server.js"]}}}
    config_path.write_text(json.dumps(existing), encoding="utf-8")

    _write_mcp_config(config_path, "/usr/bin/python3", "/path/to/mcp_server.py")

    config = json.loads(config_path.read_text())
    assert "other-tool" in config["mcpServers"]   # 原有配置保留
    assert "engram" in config["mcpServers"]        # engram 已添加


def test_write_mcp_config_with_data_dir(tmp_path: Path):
    """设置自定义 ENGRAM_DIR 时应写入 env 字段。"""
    config_path = tmp_path / "mcp.json"
    _write_mcp_config(
        config_path,
        "/usr/bin/python3",
        "/path/to/mcp_server.py",
        data_dir="/custom/engram",
    )
    config = json.loads(config_path.read_text())
    assert config["mcpServers"]["engram"]["env"]["ENGRAM_DIR"] == "/custom/engram"


def test_write_mcp_config_no_env_without_data_dir(tmp_path: Path):
    """data_dir 为 None 时不应写入 env 字段。"""
    config_path = tmp_path / "mcp.json"
    _write_mcp_config(
        config_path,
        "/usr/bin/python3",
        "/path/to/mcp_server.py",
        data_dir=None,
    )
    config = json.loads(config_path.read_text())
    assert "env" not in config["mcpServers"]["engram"]


def test_write_mcp_config_overwrites_existing_engram(tmp_path: Path):
    """重复运行 setup 应更新而非累加 engram 配置。"""
    config_path = tmp_path / "mcp.json"
    _write_mcp_config(config_path, "/old/python", "/old/mcp_server.py")
    _write_mcp_config(config_path, "/new/python", "/new/mcp_server.py")
    config = json.loads(config_path.read_text())
    assert config["mcpServers"]["engram"]["command"] == "/new/python"


def test_read_mcp_config_missing_file(tmp_path: Path):
    """不存在的配置文件应返回空 dict。"""
    result = _read_mcp_config(tmp_path / "nonexistent.json")
    assert result == {}


def test_engram_dir_env(tmp_path: Path, monkeypatch):
    """ENGRAM_DIR 环境变量应覆盖默认数据目录。"""
    custom = str(tmp_path / "custom_engram")
    monkeypatch.setenv("ENGRAM_DIR", custom)

    import importlib
    import engram_core.core as core_mod
    importlib.reload(core_mod)

    engram = core_mod.Engram()
    assert custom in str(engram.root)
