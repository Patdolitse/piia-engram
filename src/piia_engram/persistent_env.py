"""Environment variables every new process of this account starts with.

On Windows those are kept in two registry keys: the account's and the
machine's (System Properties, ``setx``). A program started by the desktop,
such as Claude Code, gets them, and so does everything it starts.
Other platforms have no single place to ask: a variable may come from a
shell profile, a login manager or the service that started the program, so
``read`` answers ``None`` there.

Read-only.
"""

from __future__ import annotations

import os

_ACCOUNT_KEY = "Environment"
_MACHINE_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"


def _read_from(registry, name: str) -> str | None:
    """``name`` from the account's key, else the machine's; ``registry`` is ``winreg``."""
    for hive, key in (
        (registry.HKEY_CURRENT_USER, _ACCOUNT_KEY),
        (registry.HKEY_LOCAL_MACHINE, _MACHINE_KEY),
    ):
        try:
            with registry.OpenKey(hive, key) as handle:
                value, kind = registry.QueryValueEx(handle, name)
        except OSError:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        if kind == registry.REG_EXPAND_SZ:
            value = registry.ExpandEnvironmentStrings(value)
        return value.strip()
    return None


def _read(name: str, *, windows: bool | None = None) -> str | None:
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return None
    try:
        import winreg

        return _read_from(winreg, name)
    except Exception:
        return None


def read(name: str) -> str | None:
    """Value of ``name`` in the persistent environment, or ``None``.

    ``None`` also when it is not set, cannot be read, or the platform has no
    such store. Never raises.
    """
    return _read(name)
