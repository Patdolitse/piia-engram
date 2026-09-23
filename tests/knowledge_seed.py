"""Test-only helper: write a knowledge file directly, bypassing the capacity core.

Production code writes lessons.json and decisions.json only through
``Engram._update_entries``. Tests that need an exact on-disk state (legacy
rows, stale timestamps, duplicates) use this helper, so the knowledge write
guard stays strict for everything else a test exercises.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from piia_engram import storage


def raw_write_json(path: Path, data: Any) -> None:
    with storage.knowledge_write_allowed():
        storage._write_json(path, data)
