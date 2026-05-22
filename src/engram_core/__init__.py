"""DEPRECATED — `engram_core` has been renamed to `piia_engram`.

This module is a backward-compatibility shim. All imports from
`engram_core` and its sub-modules transparently resolve to `piia_engram`.

Update your code:

    # old
    from engram_core import Engram
    from engram_core.context import ContextMixin

    # new
    from piia_engram import Engram
    from piia_engram.context import ContextMixin

This shim will be removed in a future release.
"""

from __future__ import annotations

import importlib as _importlib
import sys as _sys
import warnings as _warnings

_warnings.warn(
    "`engram_core` has been renamed to `piia_engram`. "
    "Update your imports: `from piia_engram import ...`. "
    "This compatibility shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

# Replace this module in sys.modules with piia_engram. After this line,
# `engram_core` is just another name for the same module object, and
# Python's import machinery will resolve `engram_core.context`,
# `engram_core.core`, etc. by looking them up as submodules of piia_engram.
_new = _importlib.import_module("piia_engram")
_sys.modules[__name__] = _new

# Also alias every already-imported submodule so that `engram_core.foo`
# is the same object as `piia_engram.foo` (avoids accidental double-load).
for _name, _mod in list(_sys.modules.items()):
    if _name.startswith("piia_engram."):
        _alias = "engram_core" + _name[len("piia_engram"):]
        _sys.modules.setdefault(_alias, _mod)
