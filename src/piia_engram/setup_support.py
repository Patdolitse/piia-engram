"""Small shared setup helpers and a late-bound compatibility facade."""
from __future__ import annotations
import importlib
import re
from pathlib import Path, PureWindowsPath

def _module_src_dir(mcp_server_path: str) -> str:
    """Return the source root containing the piia_engram package."""
    if "\\" in mcp_server_path or re.match(r"^[A-Za-z]:[\\/]", mcp_server_path):
        return str(PureWindowsPath(mcp_server_path).parent.parent)
    return str(Path(mcp_server_path).parent.parent)

def _safe_print(text: str) -> None:
    """Print with fallback for consoles that can't handle certain Unicode chars (e.g. Windows GBK)."""
    try:
        print(text)
    except UnicodeEncodeError:
        # Strip chars the console encoding can't handle
        import sys
        enc = sys.stdout.encoding or "ascii"
        safe = text.encode(enc, errors="ignore").decode(enc)
        print(safe)


class _WizardFacade:
    def __getattr__(self, name):
        # Resolve only when a check runs, after module initialization completes.
        return getattr(importlib.import_module("piia_engram.setup_wizard"), name)

wizard = _WizardFacade()
