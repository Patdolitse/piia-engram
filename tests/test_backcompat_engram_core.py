"""向后兼容性测试 — 旧的 `engram_core` 导入路径仍应工作并发出 DeprecationWarning.

确保 v3.x 用户的现有代码 (`from engram_core import Engram` 等)
在升级到改名版本后继续可用,只是会触发 DeprecationWarning。
"""

from __future__ import annotations

import importlib
import sys
import warnings


def _reset_imports():
    """清掉 engram_core / piia_engram 相关 sys.modules,模拟全新进程导入。"""
    for name in list(sys.modules):
        if name == "engram_core" or name.startswith("engram_core."):
            del sys.modules[name]


def test_top_level_engram_core_import_works_with_deprecation():
    """`from engram_core import Engram` 应该工作,并触发 DeprecationWarning。"""
    _reset_imports()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import engram_core  # noqa: F401
        from engram_core import Engram

    # 必须有 DeprecationWarning
    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert dep_warnings, "expected DeprecationWarning when importing engram_core"
    assert "piia_engram" in str(dep_warnings[0].message)

    # Engram 类应能正常使用
    assert callable(Engram)


def test_engram_core_is_same_module_as_piia_engram():
    """engram_core 和 piia_engram 应是同一个 module 对象。"""
    _reset_imports()
    import piia_engram
    import engram_core
    assert engram_core is piia_engram


def test_submodule_import_via_engram_core():
    """`from engram_core.context import ContextMixin` 应该可用。"""
    _reset_imports()
    from engram_core.context import ContextMixin
    from piia_engram.context import ContextMixin as NewCtx
    assert ContextMixin is NewCtx


def test_engram_core_storage_constants_still_accessible():
    """旧代码可能 from engram_core.storage import STALE_KNOWLEDGE_DAYS。"""
    _reset_imports()
    from engram_core.storage import STALE_KNOWLEDGE_DAYS
    from piia_engram.storage import STALE_KNOWLEDGE_DAYS as new
    assert STALE_KNOWLEDGE_DAYS == new


def test_engram_core_mcp_server_importable():
    """MCP server 入口的旧路径应可用,无 ImportError。"""
    _reset_imports()
    from engram_core import mcp_server  # noqa: F401
    from piia_engram import mcp_server as new
    assert mcp_server is new
