"""Launcher for the isolated, admission-gated memory store.

Everything that touches the isolated store runs in a child process whose
environment this launcher builds BEFORE the child imports piia_engram:

- every ENGRAM_* variable inherited from the parent (the Owner's shell carries
  ENGRAM_DIR, ENGRAM_APPROVAL=strict and queue limits) is dropped;
- ENGRAM_DIR is the isolated store, reconcile is off, audit on, no update check, the
  cache and the home directories (USERPROFILE, HOME, APPDATA, LOCALAPPDATA) point
  into the caller's own area, and the capacity limits come from the pinned config;
- nothing is written to user or system environment variables.

Commands:
    python -m piia_engram.isolated_store_launch --config CFG init
    python -m piia_engram.isolated_store_launch --config CFG run -- <program> [args...]
    python -m piia_engram.isolated_store_launch --config CFG reconcile
    python -m piia_engram.isolated_store_launch --config CFG veto retire|reject <id> --operator NAME --yes
    python -m piia_engram.isolated_store_launch --config CFG veto clear-latch --operator NAME --yes
    python -m piia_engram.isolated_store_launch --config CFG rebind --operator NAME --yes
    python -m piia_engram.isolated_store_launch pin-deny-list --out FILE --owner-home DIR
        --owner-engram-dir DIR [--extra PATH ...]

``pin-deny-list`` is the one-time step the Owner starts in person: it resolves the
paths it is GIVEN (the Owner's home .engram/.piia, the Owner's ENGRAM_DIR, every --extra
path) and writes them to a file whose sha256 goes into the config. It reads no
environment variable and has no default path; no automated script runs it. The
runtime never resolves the deny list again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from .isolated_store import CONFIG_ENV, Config, limits_env

# The only parent variables the child keeps: what Python and Windows need to run.
# Everything else (other tools' cache paths, API keys, HOMEDRIVE/HOMEPATH, XDG_*,
# CODEX_HOME, FASTEMBED_CACHE_PATH, ENGRAM_*, ...) is dropped.
PASSTHROUGH = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "OS",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER",
    "PYTHONPATH", "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONHASHSEED", "VIRTUAL_ENV",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
})


def build_child_env(parent_env: dict, cfg: Config) -> dict[str, str]:
    """The child's whole environment: a minimal passthrough plus the pinned settings."""
    env = {k: v for k, v in parent_env.items() if k.upper() in PASSTHROUGH}
    home = Path(cfg.fake_home)
    drive, tail = os.path.splitdrive(str(home))
    env.update({
        "ENGRAM_DIR": str(cfg.root),
        "ENGRAM_RECONCILE": "0",
        "ENGRAM_AUDIT": "1",
        "ENGRAM_NO_UPDATE_CHECK": "1",
        # The launched process never sends the daily usage ping.
        "DO_NOT_TRACK": "1",
        "ENGRAM_CACHE_DIR": str(cfg.cache_dir),
        "USERPROFILE": str(home),
        "HOME": str(home),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        # Temporary files stay in the caller's area too (tempfile probes and writes here).
        "TEMP": str(home / "Temp"),
        "TMP": str(home / "Temp"),
        "TMPDIR": str(home / "Temp"),
        "HOMEDRIVE": drive,
        "HOMEPATH": tail or str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        CONFIG_ENV: str(cfg.path),
    })
    env.update(limits_env(cfg.limits))
    return env


def _run_child(cfg: Config, argv: list[str]) -> int:
    for d in (cfg.fake_home / "AppData" / "Roaming", cfg.fake_home / "AppData" / "Local",
              cfg.fake_home / "Temp", cfg.cache_dir):
        d.mkdir(parents=True, exist_ok=True)
    return subprocess.call(argv, env=build_child_env(dict(os.environ), cfg))


def _child_module(cfg: Config, *args: str) -> int:
    return _run_child(cfg, [sys.executable, "-m", "piia_engram.isolated_store", *args])


def pin_deny_list(out: Path, *, owner_home: str, owner_engram_dir: str, extra: list[str] = ()) -> str:
    """Resolve the given Owner paths once and write the deny list; returns its sha256.

    Every path is passed explicitly: nothing comes from the environment or a default.
    Resolving touches those paths' metadata, which is why only the Owner starts it.
    """
    if not owner_home or not owner_engram_dir:
        raise ValueError("owner_home and owner_engram_dir are required")
    home = str(owner_home)
    candidates = [os.path.join(home, ".engram"), os.path.join(home, ".piia"), str(owner_engram_dir), *extra]
    deny = []
    for c in candidates:
        for form in (os.path.normpath(c), os.path.realpath(c)):
            if form not in deny:
                deny.append(form)
    data = json.dumps({"deny": deny, "pinned_from_home": home}, ensure_ascii=False, indent=2).encode("utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="isolated_store_launch")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("reconcile")
    run = sub.add_parser("run")
    run.add_argument("program", nargs=argparse.REMAINDER)
    veto = sub.add_parser("veto")
    veto.add_argument("action", choices=("retire", "reject", "clear-latch"))
    veto.add_argument("item_id", nargs="?")
    veto.add_argument("--operator", required=True)
    veto.add_argument("--yes", action="store_true")
    rebind = sub.add_parser("rebind")
    rebind.add_argument("--operator", required=True)
    rebind.add_argument("--yes", action="store_true")
    pin = sub.add_parser("pin-deny-list")
    pin.add_argument("--out", required=True)
    pin.add_argument("--owner-home", required=True)
    pin.add_argument("--owner-engram-dir", required=True)
    pin.add_argument("--extra", action="append", default=[])
    args = parser.parse_args(argv)

    if args.cmd == "pin-deny-list":
        print(pin_deny_list(Path(args.out), owner_home=args.owner_home,
                            owner_engram_dir=args.owner_engram_dir, extra=args.extra))
        return 0
    if not args.config:
        parser.error("--config is required")
    cfg = Config.load(args.config)
    if args.cmd == "init":
        return _child_module(cfg, "init")
    if args.cmd == "reconcile":
        return _child_module(cfg, "reconcile")
    if args.cmd == "run":
        program = [a for a in args.program if a != "--"]
        if not program:
            parser.error("run needs a program")
        return _run_child(cfg, program)
    if args.cmd in ("veto", "rebind") and not args.yes:
        print("dry run: add --yes to apply", file=sys.stderr)
        return 2
    if args.cmd == "rebind":
        return _child_module(cfg, "rebind", args.operator)
    if args.action == "clear-latch":
        return _child_module(cfg, "clear-latch", args.operator)
    if not args.item_id:
        parser.error("veto retire/reject needs an id")
    return _child_module(cfg, f"veto-{args.action}", args.item_id, args.operator)


if __name__ == "__main__":
    sys.exit(main())
