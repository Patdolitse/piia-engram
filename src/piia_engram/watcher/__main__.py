"""CLI entry: ``python -m piia_engram.watcher``.

Modes:

- ``--once``      one scan cycle, then exit (cron / scheduled-task friendly);
- default loop    scan every ``--interval`` seconds (default 30) until
                  interrupted; designed to run as a startup task.

Examples::

    python -m piia_engram.watcher --once
    python -m piia_engram.watcher --interval 60
    python -m piia_engram.watcher --adapters codex
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .core import ADAPTERS, scan_once


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m piia_engram.watcher",
        description="Engram universal session watcher (read-only on tool data; writes contexts/ only).",
    )
    parser.add_argument("--once", action="store_true", help="run one scan cycle and exit")
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="seconds between scan cycles in loop mode (default: 30)",
    )
    parser.add_argument(
        "--adapters",
        default="",
        help=f"comma-separated adapter names (default: all = {','.join(ADAPTERS)})",
    )
    args = parser.parse_args(argv)

    adapters = [a.strip() for a in args.adapters.split(",") if a.strip()] or None
    interval = max(5.0, args.interval)

    if args.once:
        counters = scan_once(adapters)
        print(json.dumps(counters, ensure_ascii=True))
        return 0

    try:
        while True:
            scan_once(adapters)
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
