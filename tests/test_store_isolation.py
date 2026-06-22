"""Guard: tests must never resolve to the developer's real ~/.engram store.

The store root is resolved as ``ENGRAM_DIR`` or, as a fallback, ``Path.home() /
.engram`` — and on a real machine that home path can be a symlink to the active
store. A test that forgets to set ENGRAM_DIR and then writes would corrupt the
user's real memory. The autouse isolation in conftest.py forces ENGRAM_DIR to a
per-test throwaway dir; these guards prove it holds.

Deliberately write-free: they only resolve paths (never construct a default
Engram()), so even a regression can't touch the real store while being caught.
"""

from __future__ import annotations

import os
from pathlib import Path

from piia_engram import storage


def test_engram_dir_is_set_during_tests():
    # The autouse isolation must always set ENGRAM_DIR so nothing falls back to
    # the real home store.
    assert os.environ.get("ENGRAM_DIR", "").strip(), "ENGRAM_DIR not isolated in tests"


def test_engram_dir_is_not_the_real_home_store():
    value = Path(os.environ["ENGRAM_DIR"]).expanduser().resolve()
    real = (Path.home() / storage._ENGRAM_DIR_NAME).resolve()
    assert value != real, f"ENGRAM_DIR points at the real store: {value}"


def test_engram_root_resolver_is_isolated():
    # storage._engram_root() is the canonical store resolver (Engram() default).
    root = storage._engram_root().resolve()
    real = (Path.home() / storage._ENGRAM_DIR_NAME).resolve()
    assert root != real
    assert root == Path(os.environ["ENGRAM_DIR"]).expanduser().resolve()


def test_isolated_dir_is_per_test(tmp_path: Path):
    # The autouse dir lives under this test's own tmp_path (so it's unique and
    # auto-cleaned), not a shared or home location.
    value = Path(os.environ["ENGRAM_DIR"]).resolve()
    assert str(value).startswith(str(tmp_path.resolve()))
