"""engram CLI subcommands (sessions/review/telemetry/backup/…).

Split out of setup_wizard.py. Helpers that tests monkeypatch on
``piia_engram.setup_wizard`` are accessed late-bound via ``W.<name>`` so
existing patches keep intercepting.
"""

from __future__ import annotations

import json
import os
import platform
import re
from pathlib import Path

from . import setup_wizard as W

def _format_session_size(size_bytes: int) -> str:
    """Human-readable byte count for the small sessions table."""
    try:
        size = int(size_bytes)
    except (TypeError, ValueError):
        size = 0
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def _parse_sessions_limit(raw: str | None, *, default: int = 20) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, 200))


_SESSION_SCAN_LIMIT = 100_000


def _run_continuity_checks(eng) -> int:
    """Doctor section for cross-tool session continuity.

    This is intentionally informational for the empty-state case: a clean
    fresh install may have no saved sessions yet, so that must not make doctor
    exit nonzero.
    """
    print()
    W._safe_print("  -- Continuity --\n")
    problems = 0

    try:
        all_sessions = eng.list_agent_sessions(limit=_SESSION_SCAN_LIMIT)
        recent = all_sessions[:1]
    except Exception as exc:
        print(f"    [!!] Agent session listing failed: {exc}")
        return 1

    if not recent:
        print("    [--] No saved agent sessions yet")
        print("         Run an AI session, then wrap up or stop the tool to create one.")
    else:
        latest = recent[0]
        tools = sorted({str(s.get("tool", "")) for s in all_sessions if s.get("tool")})
        W._safe_print(
            "    [ok] Agent sessions: "
            f"{len(all_sessions)} saved across {len(tools)} tool(s); "
            f"latest {latest.get('tool', '?')}/{latest.get('session_id', '?')} "
            f"at {latest.get('modified_at', '?')}"
        )

    try:
        brief = eng.get_resume_brief(token_budget=400)
        included = brief.get("sections_included", []) if isinstance(brief, dict) else []
        print(f"    [ok] Resume brief builds ({len(included)} section(s))")
    except Exception as exc:
        print(f"    [!!] Resume brief failed: {exc}")
        problems += 1

    return problems


def _print_sessions_usage() -> None:
    print(
        "Usage:\n"
        "  engram sessions [--tool TOOL] [--limit N]\n"
        "  engram sessions show <session_id> [--tool TOOL]\n"
    )


def run_sessions(argv: list[str] | None = None) -> int:
    """List or show saved cross-tool agent sessions."""
    W._configure_utf8_stdio()
    args = list(argv or [])
    if args and args[0] in ("-h", "--help"):
        _print_sessions_usage()
        return 0

    from piia_engram.core import Engram  # local import keeps setup startup light

    eng = Engram()

    if args and args[0] == "show":
        if len(args) < 2:
            _print_sessions_usage()
            return 2
        session_id = args[1]
        tool = ""
        i = 2
        while i < len(args):
            if args[i] == "--tool" and i + 1 < len(args):
                tool = args[i + 1]
                i += 2
            else:
                print(f"Unknown sessions option: {args[i]}")
                _print_sessions_usage()
                return 2

        metadata = eng.list_agent_sessions(tool=tool, limit=_SESSION_SCAN_LIMIT)
        match = next((s for s in metadata if s.get("session_id") == session_id), None)
        if match is None:
            print(f"Session not found: {session_id}")
            return 1

        session_path = eng.root / "contexts" / str(match.get("tool", "")) / f"{session_id}.md"
        try:
            content = session_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Session not readable: {session_id} ({exc})")
            return 1

        W._safe_print(f"# Session {match.get('tool', '?')}/{session_id}")
        W._safe_print(f"Modified: {match.get('modified_at', '?')}\n")
        W._safe_print(content)
        return 0

    tool = ""
    limit = 20
    i = 0
    while i < len(args):
        if args[i] == "--tool" and i + 1 < len(args):
            tool = args[i + 1]
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = _parse_sessions_limit(args[i + 1])
            i += 2
        else:
            print(f"Unknown sessions option: {args[i]}")
            _print_sessions_usage()
            return 2

    sessions = eng.list_agent_sessions(tool=tool, limit=limit)
    if not sessions:
        if tool:
            print(f"No saved agent sessions found for tool: {tool}")
        else:
            print("No saved agent sessions yet.")
        print("Run an AI session, then wrap up or stop the tool to create one.")
        return 0

    title = f"Recent agent sessions ({len(sessions)})"
    if tool:
        title += f" for {tool}"
    print(title)
    print("modified_at           tool          session_id                 size")
    print("-------------------   -----------   ------------------------   --------")
    for s in sessions:
        W._safe_print(
            f"{s.get('modified_at', '?'):<21} "
            f"{s.get('tool', '?'):<13} "
            f"{s.get('session_id', '?'):<26} "
            f"{_format_session_size(s.get('size_bytes', 0))}"
        )
    print("\nUse 'engram sessions show <session_id>' to print a session.")
    return 0


def _print_review_usage() -> None:
    print(
        "Usage:\n"
        "  engram review [--limit N] [--sort recent|quality|quality-desc] [--low-quality]\n"
        "  engram review show <id>\n"
        "  engram review approve <id> --yes\n"
        "  engram review archive <id> --yes\n"
    )


def _print_confirm_usage() -> None:
    print(
        "Usage:\n"
        "  engram confirm <id> --by human|test|anchor [--anchor <ref>] [--json]\n"
    )


def _print_anchors_usage() -> None:
    print(
        "Usage:\n"
        "  engram anchors check [--root PATH] [--adopt-legacy] [--json]\n"
    )


def _review_title(item_type: str, item: dict) -> str:
    if item_type == "decision":
        title = item.get("question") or item.get("title") or ""
        choice = item.get("choice") or ""
        return f"{title} -> {choice}" if choice else str(title)
    return str(item.get("summary") or item.get("title") or "")


def _review_quality_summary(item: dict) -> str:
    extraction = item.get("extraction")
    if not isinstance(extraction, dict) or not extraction:
        return "-"
    parts: list[str] = []
    score = extraction.get("quality_score")
    if isinstance(score, (int, float)):
        parts.append(f"q={score:.2f}")
    method = str(extraction.get("method") or "").strip()
    if method:
        parts.append(_truncate_review_text(method, 16))
    return " ".join(parts) if parts else "-"


def _review_quality_score(item: dict) -> float | None:
    extraction = item.get("extraction")
    if not isinstance(extraction, dict):
        return None
    score = extraction.get("quality_score")
    if isinstance(score, (int, float)):
        return float(score)
    return None


def _clean_review_inline(value: object) -> str:
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value or ""))
    return " ".join(text.split())


def _truncate_review_text(value: str, limit: int = 180) -> str:
    text = _clean_review_inline(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _print_review_quality_detail(item: dict) -> None:
    extraction = item.get("extraction")
    if not isinstance(extraction, dict) or not extraction:
        return
    method = str(extraction.get("method") or "").strip()
    source_tool = str(extraction.get("source_tool") or "").strip()
    source = _truncate_review_text(method, 48) if method else "unknown"
    if source_tool:
        source = f"{source} via {_truncate_review_text(source_tool, 48)}"
    W._safe_print(f"source: {source}")

    quality_parts: list[str] = []
    score = extraction.get("quality_score")
    if isinstance(score, (int, float)):
        quality_parts.append(f"q={score:.2f}")
    signals = extraction.get("quality_signals")
    if isinstance(signals, list) and signals:
        quality_parts.append("signals=" + ",".join(_truncate_review_text(s, 32) for s in signals[:6]))
    flags = extraction.get("quality_flags")
    if isinstance(flags, list) and flags:
        quality_parts.append("flags=" + ",".join(_truncate_review_text(f, 32) for f in flags[:6]))
    if quality_parts:
        W._safe_print("quality: " + "; ".join(quality_parts))

    evidence = str(extraction.get("evidence_span") or "").strip()
    if evidence:
        W._safe_print(f"evidence: {_truncate_review_text(evidence)}")


def _review_items(
    eng,
    *,
    limit: int = 20,
    sort: str = "recent",
    low_quality_only: bool = False,
) -> list[dict]:
    """Return staging lessons/decisions for the terminal review queue.

    The explicit ``_update_access=False`` is part of the contract: listing the
    queue must not mutate access counters or timestamps.
    """
    rows: list[dict] = []
    for item in eng.get_lessons(limit=None, _update_access=False):
        if item.get("tier") == "staging":
            rows.append({"type": "lesson", "item": item})
    for item in eng.get_decisions(limit=None, _update_access=False):
        if item.get("tier") == "staging":
            rows.append({"type": "decision", "item": item})

    if low_quality_only:
        rows = [
            row for row in rows
            if (score := _review_quality_score(row.get("item") or {})) is None or score < 0.70
        ]

    def sort_key(row: dict) -> str:
        item = row.get("item") or {}
        return str(item.get("timestamp") or item.get("created_at") or item.get("id") or "")

    def quality_sort_key(row: dict, missing_score: float) -> tuple[float, str]:
        score = _review_quality_score(row.get("item") or {})
        return (score if score is not None else missing_score, sort_key(row))

    if sort == "quality":
        rows.sort(key=lambda row: quality_sort_key(row, 99.0))
    elif sort == "quality-desc":
        rows.sort(key=lambda row: quality_sort_key(row, -1.0), reverse=True)
    else:
        rows.sort(key=sort_key, reverse=True)
    return rows[:limit]


def _print_review_list(rows: list[dict]) -> None:
    if not rows:
        print("No staging knowledge needs review.")
        return
    print(f"Staging knowledge review queue ({len(rows)})")
    print("type       id                         domain        quality          title")
    print("---------  -------------------------  ------------  ---------------  ------------------------------")
    for row in rows:
        item_type = row["type"]
        item = row["item"]
        title = _review_title(item_type, item)
        if len(title) > 70:
            title = title[:67] + "..."
        quality = _review_quality_summary(item)
        W._safe_print(
            f"{item_type:<9}  "
            f"{str(item.get('id', '?')):<25}  "
            f"{str(item.get('domain', ''))[:12]:<12}  "
            f"{quality:<15}  "
            f"{title}"
        )
    print("\nUse 'engram review show <id>' to inspect one item.")
    print("Use 'engram review approve <id> --yes' or 'engram review archive <id> --yes'.")


def _print_review_item(item_type: str, item: dict) -> None:
    print(f"type: {item_type}")
    print(f"id: {item.get('id', '')}")
    print(f"tier: {item.get('tier', '')}")
    print(f"status: {item.get('status', '')}")
    if item.get("domain"):
        print(f"domain: {item.get('domain')}")
    if item_type == "decision":
        W._safe_print(f"question: {item.get('question') or item.get('title') or ''}")
        W._safe_print(f"choice: {item.get('choice', '')}")
        if item.get("reasoning"):
            W._safe_print(f"reasoning: {item.get('reasoning')}")
    else:
        W._safe_print(f"summary: {item.get('summary') or item.get('title') or ''}")
        if item.get("detail"):
            W._safe_print(f"detail: {item.get('detail')}")
    _print_review_quality_detail(item)


def _require_yes(args: list[str], action: str) -> bool:
    if "--yes" in args:
        return True
    print(f"Refusing to {action} without explicit --yes.")
    return False


def _print_playbook_usage() -> None:
    print(
        "Engram Playbook CLI\n\n"
        "Usage:\n"
        "  engram playbook install <builtin-name> [--yes] [--project <folder>]\n"
        "  engram playbook scope classify [--project-folders a,b]\n"
        "  engram playbook scope apply [--project-folders a,b] [--playbook-ids x,y]\n"
        "                              [--min-confidence 0.7] [--apply --yes]\n"
        "  engram playbook scope rollback [--playbook-ids x,y] [--apply --yes]\n"
        "  engram playbook scope queue [--include-resolved] [--limit 50]\n"
        "  engram playbook scope resolve <playbook-id>\n"
        "                              --action accept_global|accept_project|accept_shared|skip\n"
        "                              [--project-folder X | --project-folders a,b]\n"
        "                              [--note TEXT] [--apply --yes]\n\n"
        "Default is dry-run. install needs --yes to write; scope apply/rollback/resolve\n"
        "need both --apply and --yes to mutate."
    )


def _arg_value(args: list[str], *names: str) -> str:
    for name in names:
        if name in args:
            idx = args.index(name)
            if idx + 1 >= len(args):
                return ""
            return args[idx + 1]
    return ""


def _csv_list(value: str) -> list[str]:
    """Split a comma/semicolon-separated CLI value into a clean list."""
    return [part.strip() for part in re.split(r"[,;]+", value or "") if part.strip()]


def _run_playbook_scope(args: list[str]) -> int:
    """``engram playbook scope <cmd>`` — legacy Playbook scope migration toolkit.

    v4.0.0 moved these five owner-only migration surfaces out of MCP
    (classify_legacy_playbooks / apply_legacy_playbook_scope_suggestions /
    rollback_playbook_scope_migration / get_playbook_scope_review_queue /
    resolve_playbook_scope_review) into this local CLI: the CLI process is by
    definition the local owner (private-self), so no MCP governance gate runs
    here. Mutations stay double-locked: ``--apply`` flips dry_run off and
    ``--yes`` confirms; core keeps dry-run unless both are given.
    """
    if args and args[0] in ("-h", "--help"):
        _print_playbook_usage()
        return 0
    if not args or args[0] not in {"classify", "apply", "rollback", "queue", "resolve"}:
        if args:
            print(f"Unknown playbook scope command: {args[0]}")
        _print_playbook_usage()
        return 2

    cmd, rest = args[0], args[1:]
    folders = _csv_list(_arg_value(rest, "--project-folders"))
    Engram = W._get_engram_class()
    eng = Engram()

    if cmd == "classify":
        result = eng.classify_legacy_playbooks(project_folders=folders or None)
    elif cmd == "queue":
        result = eng.get_playbook_scope_review_queue(
            project_folders=folders or None,
            include_resolved="--include-resolved" in rest,
            limit=_parse_sessions_limit(_arg_value(rest, "--limit"), default=50),
        )
    else:
        # Mutating commands: --apply flips dry_run off and must be paired
        # with an explicit --yes (same refusal style as `engram review`).
        apply_flag = "--apply" in rest
        if apply_flag and not _require_yes(rest, f"execute playbook scope {cmd}"):
            return 2
        dry_run = not apply_flag
        confirm = "--yes" in rest
        if cmd == "apply":
            min_confidence = 0.7
            raw_conf = _arg_value(rest, "--min-confidence")
            if raw_conf:
                try:
                    min_confidence = float(raw_conf)
                except ValueError:
                    print(f"Invalid --min-confidence: {raw_conf}")
                    return 2
            result = eng.apply_legacy_playbook_scope_suggestions(
                project_folders=folders or None,
                playbook_ids=_csv_list(_arg_value(rest, "--playbook-ids")) or None,
                min_confidence=min_confidence,
                dry_run=dry_run,
                confirm=confirm,
            )
        elif cmd == "rollback":
            result = eng.rollback_playbook_scope_migration(
                playbook_ids=_csv_list(_arg_value(rest, "--playbook-ids")) or None,
                dry_run=dry_run,
                confirm=confirm,
            )
        else:  # resolve
            if not rest or rest[0].startswith("--"):
                print("resolve needs a positional <playbook-id> before options")
                _print_playbook_usage()
                return 2
            action = _arg_value(rest, "--action")
            if not action:
                print("--action is required: accept_global|accept_project|accept_shared|skip")
                return 2
            result = eng.resolve_playbook_scope_review(
                rest[0],
                action,
                project_folder=_arg_value(rest, "--project-folder") or None,
                project_folders=folders or None,
                note=_arg_value(rest, "--note"),
                dry_run=dry_run,
                confirm=confirm,
            )

    W._safe_print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if isinstance(result, dict) and result.get("error") else 0


def run_playbook(argv: list[str] | None = None) -> int:
    """Local CLI for built-in Playbook templates and legacy scope migration."""
    W._configure_utf8_stdio()
    args = list(argv or [])
    if not args or args[0] in ("-h", "--help"):
        _print_playbook_usage()
        return 0
    if args[0] == "scope":
        return _run_playbook_scope(args[1:])
    if args[0] != "install" or len(args) < 2:
        _print_playbook_usage()
        return 2

    project_folder = _arg_value(args, "--project", "--project-folder")
    if ("--project" in args or "--project-folder" in args) and not project_folder:
        print("--project requires a folder path")
        return 2

    Engram = W._get_engram_class()
    eng = Engram()
    confirm = "--yes" in args
    result = eng.install_builtin_playbook(
        args[1],
        project_folder=project_folder or None,
        dry_run=not confirm,
        confirm=confirm,
    )
    W._safe_print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("error") else 0


def run_review(argv: list[str] | None = None) -> int:
    """Terminal review queue for staging lessons and decisions."""
    W._configure_utf8_stdio()
    args = list(argv or [])
    if args and args[0] in ("-h", "--help"):
        _print_review_usage()
        return 0

    from piia_engram.core import Engram

    eng = Engram()

    if args and args[0] == "show":
        if len(args) < 2:
            _print_review_usage()
            return 2
        item_type, item = eng._find_item_by_id(args[1])
        if item is None or item_type not in {"lesson", "decision"}:
            print(f"Review item not found: {args[1]}")
            return 1
        _print_review_item(item_type, item)
        return 0

    if args and args[0] == "approve":
        if len(args) < 2:
            _print_review_usage()
            return 2
        item_id = args[1]
        if not _require_yes(args[2:], "approve review item"):
            return 2
        item_type, item = eng._find_item_by_id(item_id)
        if item is None or item_type not in {"lesson", "decision"}:
            print(f"Review item not found: {item_id}")
            return 1
        if item.get("tier") != "staging":
            print(f"Review item is not staging: {item_id}")
            return 1
        result = eng.promote_knowledge(item_id)
        if result.get("status") != "promoted":
            print(f"Review item could not be promoted: {item_id}")
            return 1
        print(f"Promoted review item: {item_id}")
        return 0

    if args and args[0] == "archive":
        if len(args) < 2:
            _print_review_usage()
            return 2
        item_id = args[1]
        if not _require_yes(args[2:], "archive review item"):
            return 2
        item_type, item = eng._find_item_by_id(item_id)
        if item is None or item_type not in {"lesson", "decision"}:
            print(f"Review item not found: {item_id}")
            return 1
        result = eng.archive_knowledge(item_id)
        if result.get("error"):
            print(result["error"])
            return 1
        print(f"Archived review item: {item_id}")
        return 0

    limit = 20
    sort = "recent"
    low_quality_only = False
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            limit = _parse_sessions_limit(args[i + 1])
            i += 2
        elif args[i] == "--sort" and i + 1 < len(args):
            sort = args[i + 1]
            if sort not in {"recent", "quality", "quality-desc"}:
                print(f"Invalid review sort: {sort}")
                _print_review_usage()
                return 2
            i += 2
        elif args[i] == "--low-quality":
            low_quality_only = True
            i += 1
        else:
            print(f"Unknown review option: {args[i]}")
            _print_review_usage()
            return 2

    _print_review_list(_review_items(
        eng, limit=limit, sort=sort, low_quality_only=low_quality_only,
    ))
    return 0


def run_confirm(argv: list[str] | None = None) -> int:
    """Owner confirmation stamping CLI for one knowledge item."""
    W._configure_utf8_stdio()
    args = list(argv or [])
    if args and args[0] in ("-h", "--help"):
        _print_confirm_usage()
        return 0
    if not args or args[0].startswith("--"):
        _print_confirm_usage()
        return 2

    item_id = args[0]
    json_output = "--json" in args
    by = ""
    anchor_ref = ""
    i = 1
    while i < len(args):
        arg = args[i]
        if arg == "--json":
            i += 1
            continue
        if arg == "--by":
            if i + 1 >= len(args):
                print("--by requires human|test|anchor")
                return 2
            by = args[i + 1]
            i += 2
            continue
        if arg == "--anchor":
            if i + 1 >= len(args):
                print("--anchor requires a reference string")
                return 2
            anchor_ref = args[i + 1]
            i += 2
            continue
        print(f"Unknown confirm option: {arg}")
        _print_confirm_usage()
        return 2

    if not by:
        print("--by is required: human|test|anchor")
        return 2

    from piia_engram.core import Engram

    anchor_project_id = None
    if by.strip().lower() == "anchor":
        from piia_engram import freshness_anchors

        anchor_project_id = freshness_anchors.read_project_id(os.getcwd())

    result = Engram().confirm_knowledge(
        item_id,
        by=by,
        anchor_ref=anchor_ref or None,
        anchor_project_id=anchor_project_id,
    )
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("error"):
        print(f"确认失败: {result['error']}")
    else:
        print(f"已确认知识: {item_id} (by={by})")
    return 1 if result.get("error") else 0


def _print_onboard_usage() -> None:
    print("Usage: engram onboard [--root PATH] [--json]")
    print("  Scan the repo's npm/Python/file anchors and create staging candidate")
    print("  repo-facts. Review with `engram review`; accept with `engram onboard-accept <id>`.")


def run_onboard(argv: list[str] | None = None) -> int:
    """Scan the current repo and create STAGING candidate facts (owner accepts later)."""
    W._configure_utf8_stdio()
    args = list(argv or [])
    if args and args[0] in ("-h", "--help"):
        _print_onboard_usage()
        return 0
    root = os.getcwd()
    json_output = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--json":
            json_output = True
            i += 1
            continue
        if arg == "--root":
            if i + 1 >= len(args):
                print("--root requires a path")
                return 2
            root = args[i + 1]
            i += 2
            continue
        print(f"Unknown onboard option: {arg}")
        _print_onboard_usage()
        return 2

    from piia_engram.core import Engram

    result = Engram().onboard_repo(root)
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"Onboarded {root}: scanned {result.get('anchors_scanned', 0)} anchor(s), "
            f"created {result.get('created', 0)}, existing {result.get('existing', 0)}, "
            f"updated {result.get('updated', 0)} staging candidate fact(s)."
        )
        print("Review with `engram review`; accept with `engram onboard-accept <id>`.")
    return 0


def _print_onboard_accept_usage() -> None:
    print("Usage: engram onboard-accept <item_id> [--root PATH] [--json]")
    print("       engram onboard-accept --all [--yes] [--root PATH] [--json]")
    print("  Owner-accept onboard candidate(s): verify the anchor against the repo and")
    print("  promote to a confirmed fact. A single accept refuses if the anchor is invalid.")
    print("  --all batch-accepts every staging candidate for this repo; it previews by")
    print("  default (count + repo + skipped cross-repo) and needs --yes to write.")


def _run_onboard_accept_all(args: list[str]) -> int:
    """Batch owner-accept (engram onboard-accept --all): preview unless --yes.

    Owner-explicit only. Reuses the per-item accept path so each candidate is
    anchor-verified, cross-repo-skipped, and invalid-anchor-refused; the batch
    can partially succeed. Without --yes it is a zero-write dry-run preview.
    """
    json_output = "--json" in args
    confirm = "--yes" in args
    root = os.getcwd()
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--all", "--yes", "--json"):
            i += 1
            continue
        if arg == "--root":
            if i + 1 >= len(args):
                print("--root requires a path")
                return 2
            root = args[i + 1]
            i += 2
            continue
        print(f"Unknown onboard-accept option: {arg}")
        _print_onboard_accept_usage()
        return 2

    from piia_engram.core import Engram

    result = Engram().accept_onboard_candidates(project_root=root, dry_run=not confirm)
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("dry_run"):
        print(
            f"将接受 {result.get('would_accept', 0)} 条候选 (repo: {result.get('repo_id')})；"
            f"跳过 {result.get('skipped', 0)} 条跨仓库候选。加 --yes 执行。"
        )
    else:
        print(
            f"批量接受完成 (repo: {result.get('repo_id')}): "
            f"已接受 {result.get('accepted', 0)}，拒绝 {result.get('rejected', 0)}，"
            f"跳过 {result.get('skipped', 0)} 条。"
        )
    return 0


def run_onboard_accept(argv: list[str] | None = None) -> int:
    """Owner-accept an onboard candidate (promote to verified + stamp the anchor)."""
    W._configure_utf8_stdio()
    args = list(argv or [])
    if args and args[0] in ("-h", "--help"):
        _print_onboard_accept_usage()
        return 0
    if "--all" in args:
        return _run_onboard_accept_all(args)
    if not args or args[0].startswith("--"):
        _print_onboard_accept_usage()
        return 2
    item_id = args[0]
    json_output = False
    root = os.getcwd()
    i = 1
    while i < len(args):
        arg = args[i]
        if arg == "--json":
            json_output = True
            i += 1
            continue
        if arg == "--root":
            if i + 1 >= len(args):
                print("--root requires a path")
                return 2
            root = args[i + 1]
            i += 2
            continue
        print(f"Unknown onboard-accept option: {arg}")
        _print_onboard_accept_usage()
        return 2

    from piia_engram.core import Engram

    result = Engram().accept_onboard_candidate(item_id, project_root=root)
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("error"):
        print(f"接受失败: {result['error']}")
    else:
        print(f"已接受 onboard 候选: {item_id} (tier=verified, anchor stamped)")
    return 1 if result.get("error") else 0


def run_anchors(argv: list[str] | None = None) -> int:
    """Owner-run anchor maintenance CLI."""
    W._configure_utf8_stdio()
    args = list(argv or [])
    if args and args[0] in ("-h", "--help"):
        _print_anchors_usage()
        return 0
    if not args or args[0] != "check":
        if args:
            print(f"Unknown anchors command: {args[0]}")
        _print_anchors_usage()
        return 2

    root = os.getcwd()
    json_output = False
    adopt_legacy = False
    i = 1
    while i < len(args):
        arg = args[i]
        if arg == "--json":
            json_output = True
            i += 1
            continue
        if arg == "--adopt-legacy":
            adopt_legacy = True
            i += 1
            continue
        if arg == "--root":
            if i + 1 >= len(args):
                print("--root requires a path")
                return 2
            root = args[i + 1]
            i += 2
            continue
        print(f"Unknown anchors check option: {arg}")
        _print_anchors_usage()
        return 2

    from piia_engram.core import Engram

    result = Engram().revalidate_anchors(root, adopt_legacy=adopt_legacy)
    missing_project = result.get("project_id") is None
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if missing_project:
            print("当前目录不是 git 仓库或没有 origin 远程，无法校验 anchor。")
        print(
            "Anchor 校验完成: "
            f"project_id={result.get('project_id') or '-'}, "
            f"checked={result.get('checked', 0)}, "
            f"valid={result.get('valid', 0)}, "
            f"invalid={result.get('invalid', 0)}, "
            f"unknown={result.get('unknown', 0)}, "
            f"skipped_mismatch={result.get('skipped_mismatch', 0)}, "
            f"skipped_legacy={result.get('skipped_legacy', 0)}"
        )
    return 1 if missing_project else 0


def _run_telemetry_cli(sub_args: list[str]) -> None:
    """Handle `engram telemetry <subcommand>`."""
    from piia_engram.telemetry import (
        get_status, is_enabled, preview_payload, set_enabled,
        set_remote_enabled,
    )

    sub = sub_args[0] if sub_args else "status"

    if sub == "status":
        status = get_status()
        state = "ON" if status["enabled"] else "OFF"
        remote_state = "ON" if status.get("remote_enabled") else "OFF"
        print(f"\n  Anonymous usage statistics: {state}")
        print(f"  Remote sending: {remote_state}")
        print(f"  Phase: {status['phase']}")
        print(f"  Config: {status['config_path']}")
        print(f"  Log: {status['log_path']}")
        if status["enabled"]:
            print(f"  Opted in: {status['opted_in_at']}")
        if status.get("remote_enabled"):
            print(f"  Remote opted in: {status.get('remote_opted_in_at', '(unknown)')}")
            print(f"  Endpoint: {status.get('endpoint', '(unknown)')}")
        print()

    elif sub == "preview":
        print("\n  Next payload (if enabled):\n")
        print(preview_payload())
        print()

    elif sub in ("off", "disable"):
        set_enabled(False)
        set_remote_enabled(False)
        print("\n  ✅ Anonymous usage statistics disabled (local + remote).")
        print("  No data will be logged or sent.\n")

    elif sub in ("on", "enable"):
        set_enabled(True)
        print("\n  ✅ Anonymous usage statistics enabled.")
        print("  Run 'engram telemetry preview' to see what will be logged.")
        print("  Run 'engram telemetry remote on' to also enable remote sending.\n")

    elif sub == "remote":
        remote_sub = sub_args[1] if len(sub_args) > 1 else "status"
        if remote_sub in ("on", "enable"):
            if not is_enabled():
                set_enabled(True)
                print("\n  ✅ Local statistics also enabled (required for remote).")
            set_remote_enabled(True)
            print("  ✅ Remote anonymous statistics enabled.")
            print("  Data will be sent via HTTPS to Cloudflare Worker.\n")
        elif remote_sub in ("off", "disable"):
            set_remote_enabled(False)
            print("\n  ✅ Remote sending disabled. Local logging continues if enabled.\n")
        else:
            status = get_status()
            remote_state = "ON" if status.get("remote_enabled") else "OFF"
            print(f"\n  Remote sending: {remote_state}")
            if status.get("remote_enabled"):
                print(f"  Endpoint: {status.get('endpoint', '(unknown)')}")
            print()

    elif sub == "feedback":
        from piia_engram.telemetry import is_feedback_enabled, set_feedback_enabled
        fb_sub = sub_args[1] if len(sub_args) > 1 else "status"
        if fb_sub in ("on", "enable"):
            if not is_enabled():
                set_enabled(True)
                print("\n  ✅ Local statistics also enabled (required for feedback).")
            set_remote_enabled(True)
            set_feedback_enabled(True)
            print("  ✅ Weekly anonymous feedback reports enabled.")
            print("  Reports are sent automatically during wrap_up_session.\n")
        elif fb_sub in ("off", "disable"):
            set_feedback_enabled(False)
            print("\n  ✅ Feedback reports disabled. Other telemetry settings unchanged.\n")
        else:
            fb_state = "ON" if is_feedback_enabled() else "OFF"
            print(f"\n  Weekly feedback reports: {fb_state}")
            print("  Toggle: engram telemetry feedback on/off\n")

    elif sub == "--show-payload":
        print("\n  Next payload (if enabled):\n")
        print(preview_payload())
        print()

    else:
        print(
            "\nUsage:\n"
            "  engram telemetry status         Show current status\n"
            "  engram telemetry preview        Show what data will be logged\n"
            "  engram telemetry on             Enable anonymous usage statistics\n"
            "  engram telemetry off            Disable anonymous usage statistics\n"
            "  engram telemetry remote on      Enable remote sending (Phase 2)\n"
            "  engram telemetry remote off     Disable remote sending\n"
            "  engram telemetry feedback on    Enable weekly feedback reports\n"
            "  engram telemetry feedback off   Disable weekly feedback reports\n"
        )


def _run_privacy_report() -> None:
    """Handle `engram privacy` — show what data Engram stores and where."""
    import os as _os
    data_dir = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")

    print("\n========================================")
    print("  Engram Privacy Report")
    print("========================================\n")

    # 1. Data directory
    print(f"  [DIR] Data directory: {data_dir}")
    if data_dir.exists():
        files = list(data_dir.iterdir())
        total_size = sum(f.stat().st_size for f in files if f.is_file())
        print(f"        Files: {len([f for f in files if f.is_file()])}")
        print(f"        Total size: {total_size / 1024:.1f} KB")
    else:
        print("        (not created yet)")
    print()

    # 2. Identity data
    identity_file = data_dir / "identity.json"
    print("  [ID]  Identity data:")
    if identity_file.is_file():
        size = identity_file.stat().st_size
        print(f"        {identity_file} ({size / 1024:.1f} KB)")
        print("        Contains: profile, preferences, work_style, quality_standards, trust_boundaries")
        try:
            raw = identity_file.read_text(encoding="utf-8")
            encrypted_count = raw.count("enc:v")
            if encrypted_count > 0:
                print(f"        [ENCRYPTED] {encrypted_count} fields encrypted")
            else:
                print("        [PLAIN] No encrypted fields (set ENGRAM_KEY to enable)")
        except Exception:
            pass
    else:
        print("        (not created yet)")
    print()

    # 3. Knowledge base
    knowledge_file = data_dir / "knowledge.json"
    print("  [KB]  Knowledge base:")
    if knowledge_file.is_file():
        size = knowledge_file.stat().st_size
        print(f"        {knowledge_file} ({size / 1024:.1f} KB)")
        try:
            import json as _j
            kdata = _j.loads(knowledge_file.read_text(encoding="utf-8"))
            lessons = kdata.get("lessons", [])
            decisions = kdata.get("decisions", [])
            print(f"        Lessons: {len(lessons)}")
            print(f"        Decisions: {len(decisions)}")
        except Exception:
            pass
    else:
        print("        (not created yet)")
    print()

    # 4. Telemetry
    print("  [STAT] Anonymous usage statistics:")
    try:
        from piia_engram.telemetry import get_status
        status = get_status()
        state = "ON" if status["enabled"] else "OFF"
        remote_state = "ON" if status.get("remote_enabled") else "OFF"
        print(f"        Local: {state}")
        print(f"        Remote: {remote_state}")
        print(f"        Phase: {status['phase']}")
        print(f"        Config: {status['config_path']}")
        log_path = Path(status["log_path"])
        if log_path.is_file():
            log_size = log_path.stat().st_size
            log_lines = len(log_path.read_text(encoding="utf-8").strip().splitlines())
            print(f"        Log: {log_path} ({log_size / 1024:.1f} KB, {log_lines} entries)")
        else:
            print("        Log: (no entries yet)")
        print("        Collected: tool names + counts, knowledge totals, version, daily anonymous ID")
        print("        NOT collected: text content, prompts, file paths, PII, IP")
        if status.get("remote_enabled"):
            print(f"        Endpoint: {status.get('endpoint', '(unknown)')}")
        print("        Optional: telemetry Phase 2 (remote to Cloudflare Worker, requires re-consent)")
    except ImportError:
        print("        (telemetry module not available)")
    print()

    # 5. Reconcile
    print("  [SYNC] Cross-tool sync:")
    try:
        from piia_engram.reconcile import ReconcileMixin
        authorized = ReconcileMixin._reconcile_authorized()
        print(f"        Status: {'ON' if authorized else 'OFF'}")
        print("        Scans: ~/.claude/projects/*/memory/*.md, CLAUDE.md, .cursorrules, etc.")
        print("        Control: ENGRAM_RECONCILE=0 to disable")
    except ImportError:
        print("        (reconcile module not available)")
    print()

    # 6. Network
    print("  [NET]  Network requests:")
    print("        Core Engram: ZERO network requests (local files only)")
    print("        Optional: read_web_content (user-initiated only, via local Reader service)")
    print("        Optional: telemetry Phase 2 (NOT implemented, requires re-consent)")
    print()

    # 7. How to delete
    print("  [DEL]  Delete all data:")
    print(f"        rm -rf {data_dir}")
    print("        (This removes ALL Engram data permanently)")
    print()


# ---------------------------------------------------------------------------
# engram feedback — 内测反馈报告
# ---------------------------------------------------------------------------


def _build_feedback_report(data_dir: str | None = None) -> dict:
    """Build an anonymous usage/governance report from local Engram data.

    Reads knowledge files and computes governance metrics without any
    network calls. No lesson/decision content is included — only counts,
    distributions, and timing statistics.

    Returns a dict suitable for JSON export.
    """
    from datetime import datetime, timezone

    root = Path(data_dir) if data_dir else Path(os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    knowledge_dir = root / "knowledge"
    playbooks_dir = root / "playbooks"
    contexts_dir = root / "contexts"

    report: dict = {
        "report_type": "engram_beta_feedback",
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Version
    try:
        from importlib.metadata import version as _pkg_version
        report["engram_version"] = _pkg_version("piia-engram")
    except Exception:
        report["engram_version"] = "unknown"

    report["os"] = platform.system()
    report["python"] = platform.python_version()

    # Lessons
    lessons_path = knowledge_dir / "lessons.json"
    lessons: list[dict] = []
    if lessons_path.is_file():
        try:
            lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    staging_lessons = [l for l in lessons if l.get("tier") == "staging"]
    verified_lessons = [l for l in lessons if l.get("tier") != "staging"]

    # Decisions
    decisions_path = knowledge_dir / "decisions.json"
    decisions: list[dict] = []
    if decisions_path.is_file():
        try:
            decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    staging_decisions = [d for d in decisions if d.get("tier") == "staging"]
    verified_decisions = [d for d in decisions if d.get("tier") != "staging"]

    # Playbooks
    playbooks_index = playbooks_dir / "_index.json"
    playbooks: list[dict] = []
    if playbooks_index.is_file():
        try:
            playbooks = json.loads(playbooks_index.read_text(encoding="utf-8"))
        except Exception:
            pass
    staging_playbooks = [p for p in playbooks if p.get("tier") == "staging"]
    verified_playbooks = [p for p in playbooks if p.get("tier") != "staging"]

    total_staging = len(staging_lessons) + len(staging_decisions) + len(staging_playbooks)
    total_verified = len(verified_lessons) + len(verified_decisions) + len(verified_playbooks)
    total = total_staging + total_verified

    report["knowledge"] = {
        "total": total,
        "staging": total_staging,
        "verified": total_verified,
        "promotion_rate": round(total_verified / total, 2) if total > 0 else None,
        "lessons": {"staging": len(staging_lessons), "verified": len(verified_lessons)},
        "decisions": {"staging": len(staging_decisions), "verified": len(verified_decisions)},
        "playbooks": {"staging": len(staging_playbooks), "verified": len(verified_playbooks)},
    }

    # Domain distribution (top 10, no content)
    domain_counts: dict[str, int] = {}
    for item in lessons + decisions:
        domain = item.get("domain", "")
        if domain:
            for d in domain.split(","):
                d = d.strip()
                if d:
                    domain_counts[d] = domain_counts.get(d, 0) + 1
    top_domains = sorted(domain_counts.items(), key=lambda x: -x[1])[:10]
    report["top_domains"] = {k: v for k, v in top_domains}

    # Source tool distribution
    tool_counts: dict[str, int] = {}
    for item in lessons + decisions:
        src = item.get("source_tool", "unknown")
        tool_counts[src] = tool_counts.get(src, 0) + 1
    report["source_tools"] = tool_counts

    # Timing: days since first knowledge, avg staging age
    now = datetime.now(timezone.utc)
    all_items = lessons + decisions + playbooks
    created_dates: list[datetime] = []
    staging_ages: list[float] = []
    for item in all_items:
        ts = item.get("created_at", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            # Ensure timezone-aware for comparison
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            created_dates.append(dt)
            if item.get("tier") == "staging":
                staging_ages.append((now - dt).total_seconds() / 86400)
        except Exception:
            pass

    if created_dates:
        report["first_knowledge_date"] = min(created_dates).strftime("%Y-%m-%d")
        report["days_with_knowledge"] = (now - min(created_dates)).days
    report["avg_staging_age_days"] = round(sum(staging_ages) / len(staging_ages), 1) if staging_ages else None

    # Session contexts count
    session_count = 0
    if contexts_dir.is_dir():
        try:
            session_count = sum(1 for f in contexts_dir.iterdir() if f.suffix == ".json")
        except Exception:
            pass
    report["session_count"] = session_count

    # MCP tool call log (from telemetry.log if exists)
    telemetry_log = root / "telemetry.log"
    tool_call_totals: dict[str, int] = {}
    if telemetry_log.is_file():
        try:
            for line in telemetry_log.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                    for tool_name, counts in entry.get("tool_calls", {}).items():
                        if isinstance(counts, dict):
                            n = counts.get("success", 0) + counts.get("error", 0)
                        else:
                            n = int(counts)
                        tool_call_totals[tool_name] = tool_call_totals.get(tool_name, 0) + n
                except Exception:
                    continue
        except Exception:
            pass
    if tool_call_totals:
        top_tools = sorted(tool_call_totals.items(), key=lambda x: -x[1])[:15]
        report["top_mcp_tools"] = {k: v for k, v in top_tools}

    # Configured AI tools (from setup_report)
    setup_report = root / "setup_report.jsonl"
    if setup_report.is_file():
        try:
            lines = setup_report.read_text(encoding="utf-8").strip().split("\n")
            if lines:
                last = json.loads(lines[-1])
                report["configured_tools"] = last.get("tools_configured", [])
        except Exception:
            pass

    # Beta event tracking aggregate
    try:
        from piia_engram.beta_tracker import aggregate_events
        beta = aggregate_events()
        if beta:
            report["beta_events"] = beta
    except Exception:
        pass

    return report


def run_feedback(*, dry_run: bool = False) -> None:
    """Generate and display an anonymous beta feedback report.

    The report contains only counts and distributions — no knowledge content,
    no file paths, no personal information. Users can copy-paste it.

    Args:
        dry_run: If True, show the exact payload that would be sent but do not send.
    """
    W._configure_utf8_stdio()

    print("\n  ========================================")
    print("  Piia Engram 内测反馈报告 / Beta Feedback Report")
    print("  ========================================\n")

    report = _build_feedback_report()

    # Pretty print
    k = report.get("knowledge", {})
    print(f"  Engram 版本: {report.get('engram_version', '?')}")
    print(f"  OS: {report.get('os', '?')} | Python: {report.get('python', '?')}")
    print(f"  使用天数: {report.get('days_with_knowledge', '?')} 天")
    print(f"  会话数: {report.get('session_count', 0)}")
    print()

    print("  ── 知识治理 ──")
    print(f"  总知识数: {k.get('total', 0)} (staging: {k.get('staging', 0)}, verified: {k.get('verified', 0)})")
    pr = k.get("promotion_rate")
    if pr is not None:
        print(f"  确认率 (promotion rate): {pr:.0%}")
    avg_age = report.get("avg_staging_age_days")
    if avg_age is not None:
        print(f"  Staging 平均滞留: {avg_age} 天")
    print(f"    Lessons:   staging={k.get('lessons', {}).get('staging', 0)}, verified={k.get('lessons', {}).get('verified', 0)}")
    print(f"    Decisions: staging={k.get('decisions', {}).get('staging', 0)}, verified={k.get('decisions', {}).get('verified', 0)}")
    print(f"    Playbooks: staging={k.get('playbooks', {}).get('staging', 0)}, verified={k.get('playbooks', {}).get('verified', 0)}")
    print()

    if report.get("top_domains"):
        print("  ── 领域分布 ──")
        for d, c in report["top_domains"].items():
            print(f"    {d}: {c}")
        print()

    if report.get("source_tools"):
        print("  ── 来源工具 ──")
        for t, c in report["source_tools"].items():
            print(f"    {t}: {c}")
        print()

    if report.get("configured_tools"):
        print(f"  ── 已配置工具 ──")
        print(f"    {', '.join(report['configured_tools'])}")
        print()

    beta = report.get("beta_events", {})
    if beta:
        print("  ── 行为埋点 ──")
        print(f"  总事件数: {beta.get('total_events', 0)}")
        if beta.get("tracking_days"):
            print(f"  追踪天数: {beta['tracking_days']} 天")
        ec = beta.get("event_counts", {})
        if ec:
            for ev_name, ev_count in sorted(ec.items(), key=lambda x: -x[1]):
                print(f"    {ev_name}: {ev_count}")
        prom = beta.get("promotions", {})
        if prom:
            print(f"  晋升总数: {prom.get('total', 0)}")
            for m, c in prom.get("methods", {}).items():
                print(f"    方式 {m}: {c}")
        cs = beta.get("cold_starts", {})
        if cs:
            print(f"  冷启动级别: {cs}")
        rec = beta.get("reconcile", {})
        if rec:
            print(f"  跨工具同步: {rec.get('sync_count', 0)} 次, 导入 {rec.get('total_imported', 0)} 条")
        print()

    # --dry-run: show exactly what would be sent, then stop
    if dry_run:
        print("  ── Dry-run: 以下是将要发送的完整 payload ──")
        print("  (实际运行时不会发送，仅展示)\n")
        preview = report.copy()
        try:
            from piia_engram.telemetry import _daily_id, _load_config
            cfg = _load_config()
            local_uuid = cfg.get("local_uuid", "")
            if local_uuid:
                preview["daily_id"] = _daily_id(local_uuid)
            else:
                preview["daily_id"] = "<would be generated at send time>"
        except Exception:
            preview["daily_id"] = "<would be generated at send time>"
        preview_json = json.dumps(preview, ensure_ascii=False, indent=2)
        print(f"  ```json\n{preview_json}\n  ```\n")
        print("  此 payload 只包含计数和分布，不含任何知识内容或个人信息。")
        print("  确认无误后，运行 engram feedback（不加 --dry-run）即可发送。")
        return

    # Auto-send if feedback reporting is opted in
    try:
        from piia_engram.telemetry import is_feedback_enabled, send_feedback
        if is_feedback_enabled():
            print("  ── 自动上报 ──")
            ok = send_feedback(report)
            if ok:
                print("  ✅ 反馈已匿名发送到 Engram 开发团队。")
                print("     关闭自动上报: engram telemetry feedback off\n")
            else:
                print("  ⚠️  自动上报失败（网络问题？），报告仅保留在本地。\n")
        else:
            print("  ── 自动上报未开启 ──")
            print("  开启后每周自动发送: engram telemetry feedback on\n")
    except Exception:
        pass

    # JSON for copy-paste
    report_json = json.dumps(report, ensure_ascii=False, indent=2)
    print("  ── 可复制 JSON（备用，粘贴到反馈帖即可）──")
    print(f"  ```json\n{report_json}\n  ```")
    print()
    print("  此报告不含任何知识内容、文件路径或个人信息。")
    print("  This report contains no knowledge content, file paths, or personal info.\n")


def _run_reindex() -> None:
    """Rebuild the v4.0 hybrid search index from the JSON knowledge store."""
    from piia_engram.core import Engram

    eng = Engram()
    result = eng.rebuild_index()
    # Corpus encryption refuses to persist a plaintext index. Say so explicitly
    # instead of the misleading "[ok] reindexed 0 entries" (Codex a5 round-3 O3).
    if result.get("skipped") == "corpus_encrypted":
        tail = " (existing plaintext index purged)" if result.get("purged") else ""
        print("[ok] corpus encryption enabled; persistent search index "
              f"skipped{tail}.")
        return
    vec = "on" if result.get("vector_enabled") else "off (install piia-engram[vector] for semantic search)"
    print(f"[ok] reindexed {result.get('indexed', 0)} entries — vector layer: {vec}")


def _run_repair_encoding(args: list[str]) -> int:
    """Scan or repair high-confidence mojibake in the active Engram root."""
    from piia_engram.core import Engram
    from piia_engram.encoding_repair import (
        repair_engram_root,
        scan_engram_root,
        summarize_findings,
    )

    apply = "--apply" in args or "--fix" in args
    no_backup = "--no-backup" in args
    summary_only = "--summary" in args
    eng = Engram()

    if summary_only and not apply:
        report = scan_engram_root(eng.root)
        summary = summarize_findings(report)
        # Metadata-only output: counts and generic reason codes, never bodies
        # or paths — safe to paste into an audit/report.
        print("Encoding scan summary (metadata-only, no bodies/paths):")
        print(f"  files_with_findings: {summary['files_with_findings']}")
        print(f"  repairable: {summary['repairable_count']}  "
              f"suspect: {summary['suspect_count']}  "
              f"total: {summary['total_findings']}")
        if summary["reasons"]:
            print("  reasons:")
            for reason, count in summary["reasons"].items():
                print(f"    {reason}: {count}")
        return 0 if summary["suspect_count"] == 0 else 1

    if apply:
        if no_backup:
            print(
                "[!!] Encoding repair: --no-backup disables automatic backup; "
                "use only if you already have a separate backup."
            )
        report = repair_engram_root(eng.root, apply=True, backup=not no_backup)
        if not report.findings:
            print("[ok] Encoding repair: no mojibake detected.")
            return 0
        if report.repairable_count:
            print(
                "[fixed] Encoding repair: repaired "
                f"{report.repairable_count} field(s) in {len(report.changed_files)} file(s)."
            )
            if report.backup_dir is not None:
                print(f"        Backup: {report.backup_dir}")
        if report.suspect_count:
            print(f"[!!] {report.suspect_count} suspect field(s) need manual review.")
            return 1
        return 0

    report = scan_engram_root(eng.root)
    if not report.findings:
        print("[ok] Encoding repair dry-run: no mojibake detected.")
        print(
            "     This confirms stored Engram data is clean. If text still looks "
            "garbled in a terminal, check display encoding instead."
        )
        print("     PowerShell tip: use Get-Content -Encoding utf8 for UTF-8 files.")
        return 0

    print(
        "[!!] Encoding repair dry-run: found "
        f"{report.repairable_count} repairable mojibake field(s) "
        f"and {report.suspect_count} suspect field(s)."
    )
    for finding in report.findings[:20]:
        print(f"  - {finding.relative_path}:{finding.json_path} ({finding.reason})")
    print("Run 'engram repair-encoding --apply' to repair with a backup.")
    return 1


def _run_recover_json(args: list[str]) -> int:
    """Dry-run recovery scan for JSON files backed up as ``*.corrupt``."""
    import os as _os
    from piia_engram.recovery import (
        analyze_json_recovery_candidates,
        analyze_recovery_retention_plan,
        write_recovery_candidate,
    )

    if not args or args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram recover-json lessons\n"
            "  engram recover-json lessons --write-candidate PATH\n"
        )
        return 0

    dataset = args[0]
    output_path = None
    if "--write-candidate" in args:
        idx = args.index("--write-candidate")
        if idx + 1 >= len(args):
            print("ERROR: --write-candidate requires a destination path")
            return 2
        output_path = args[idx + 1]

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    report = analyze_json_recovery_candidates(root, dataset=dataset)
    active = report.get("active") or {}
    best = report.get("best_candidate")

    print(f"JSON recovery dry-run: dataset={dataset}")
    print(
        "Active: "
        f"{active.get('file_name', f'{dataset}.json')} "
        f"status={active.get('json_status')} "
        f"entries={active.get('entries')} "
        f"bom={active.get('starts_bom')}"
    )
    print("Candidates:")
    for item in report["files"]:
        if item.get("role") != "backup":
            continue
        marker = "*" if best and item["file_name"] == best["file_name"] else "-"
        print(
            f"  {marker} {item['file_name']} "
            f"status={item['json_status']} "
            f"entries={item['entries']} "
            f"date_max={item['date_max']} "
            f"sha256={item['sha256_12']}"
        )
    if best:
        print(f"Best candidate: {best['file_name']} entries={best['entries']}")
    else:
        print("Best candidate: none")
    retention = analyze_recovery_retention_plan(root, dataset=dataset)
    if retention.get("primary_candidate"):
        print(
            "Retention plan: "
            f"union_ids={retention['union_ids']} "
            f"overlap_ids={retention['overlap_ids']} "
            f"primary_only_ids={retention['primary_only_ids']} "
            f"secondary_only_ids={retention['secondary_only_ids']} "
            f"overflow_ids={retention['overflow_ids']} "
            f"active_merge_safe={str(retention['active_merge_safe']).lower()}"
        )
        print(f"Recommendation: {retention['recommendation']}")
    print("Live store modified: false")

    if output_path:
        result = write_recovery_candidate(root, dataset=dataset, output_path=output_path)
        print(
            "Wrote recovery candidate: "
            f"{result['output_path']} "
            f"entries={result['entries']} "
            "live_store_modified=false"
        )
    return 0


def _run_backup_plan(args: list[str]) -> int:
    """Print a metadata-only local backup plan (what to copy before upgrading).

    Read-only and local-only: it enumerates Engram-owned files under the active
    root, never reads stored knowledge bodies, and never touches files outside
    the Engram directory. Pass ``--json`` for machine-readable output.
    """
    import os as _os
    from piia_engram.recovery import build_backup_plan

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    plan = build_backup_plan(root)

    if "--json" in args:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    print(f"Engram backup plan (metadata only): {plan['root']}")
    if not plan["exists"]:
        print("  (no Engram root found at this path yet — nothing to back up)")
        return 0
    print(f"  total: {plan['total_files']} files, {plan['total_bytes']} bytes")
    print("  groups:")
    for group in plan["groups"]:
        print(f"    - {group['name']}: {group['files']} files, {group['bytes']} bytes")
    if plan["knowledge_datasets"]:
        print("  knowledge datasets:")
        for ds in plan["knowledge_datasets"]:
            print(
                f"    - {ds['file_name']}: entries={ds['entries']} "
                f"bytes={ds['bytes']} sha256={ds['sha256_12']}"
            )
    print(f"  external files included: {plan['external_files_included']} "
          f"(excluded: {plan['external_paths_excluded']})")
    print(f"  {plan['restore_hint']}")
    print("  live store modified: false")
    return 0


def _render_import_result_text(payload: dict) -> str:
    """Render import preview/apply output without exposing stored values."""
    status = payload.get("status", "unknown")
    mode = payload.get("mode", "")
    dry_run = bool(payload.get("dry_run"))
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    conflicts = payload.get("conflicts", []) if isinstance(payload.get("conflicts"), list) else []

    title = "Engram import preview" if dry_run else "Engram import apply"
    lines = [
        f"{title} - {status}",
        f"  mode: {mode or 'merge'}",
        f"  dry_run: {'true' if dry_run else 'false'}",
        "  metadata_only: true",
    ]
    if payload.get("requires_confirmation"):
        lines.append("  requires_confirmation: true")
        lines.append("  re-run with --apply --yes to mutate the local store")
    if summary:
        lines.append("  summary:")
        for section, counts in sorted(summary.items()):
            lines.append(
                f"    - {section}: incoming={counts.get('incoming', 0)} "
                f"add={counts.get('would_add', 0)} "
                f"skip={counts.get('would_skip', 0)} "
                f"conflicts={counts.get('conflicts', 0)}"
            )
    if conflicts:
        lines.append(f"  conflicts: {len(conflicts)} (metadata only; values withheld)")
    if payload.get("error"):
        lines.append(f"  error: {payload['error']}")
    if dry_run and not payload.get("requires_confirmation"):
        lines.append("  run 'engram import <backup.json> --apply --yes' to apply")
    return "\n".join(lines)


def _run_import_backup(args: list[str]) -> int:
    """Preview/apply a full Engram JSON backup import.

    Default is read-only preview. Mutation requires both ``--apply`` and
    ``--yes``; overwrite mode is explicit via ``--overwrite``.
    """
    import os as _os
    from piia_engram.core import Engram

    if not args or args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram import <backup.json> [--json]\n"
            "  engram import <backup.json> --apply --yes [--json]\n"
            "  engram import <backup.json> --apply --yes --materialize-version-chain [--json]\n"
            "  engram import <backup.json> --overwrite --apply --yes [--json]\n\n"
            "Default is metadata-only preview. --overwrite maps to merge=False."
        )
        return 0 if args and args[0] in {"-h", "--help"} else 2

    json_output = "--json" in args
    apply = "--apply" in args
    confirm = "--yes" in args
    overwrite = "--overwrite" in args
    materialize_version_chain = "--materialize-version-chain" in args
    known_flags = {
        "--json",
        "--apply",
        "--yes",
        "--overwrite",
        "--materialize-version-chain",
    }
    paths = []
    for arg in args:
        if arg in known_flags:
            continue
        if arg.startswith("--"):
            payload = {"error": f"Unknown import option: {arg}"}
            if json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(_render_import_result_text(payload))
            return 2
        paths.append(arg)

    if len(paths) != 1:
        payload = {"error": "Usage: engram import <backup.json> [--json]"}
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_render_import_result_text(payload))
        return 2

    backup_path = paths[0]
    merge = not overwrite
    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)

    if apply and not confirm:
        payload = eng.import_all(backup_path, merge=merge, dry_run=True)
        payload["requires_confirmation"] = True
        payload["confirmation_hint"] = "re-run with --apply --yes to mutate the local store"
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_render_import_result_text(payload))
        return 1

    payload = eng.import_all(
        backup_path,
        merge=merge,
        dry_run=not apply,
        materialize_version_chain=materialize_version_chain and merge,
    )
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_render_import_result_text(payload))
    return 1 if payload.get("error") else 0


def _run_export_agents_md(args: list[str]) -> int:
    """Export verified, non-sensitive knowledge as an AGENTS.md / CLAUDE.md block.

    Local + owner-run (CLI): loads the user's own store and renders the curated,
    committable digest via ``agents_md_export.build_agents_md_export`` — which is
    verified-only, sensitivity-screened, and summary/metadata-only by
    construction. Prints to stdout by default; ``--out PATH`` writes the block to
    an explicit destination and REFUSES to overwrite an existing file (so it can
    never clobber a hand-maintained AGENTS.md).
    """
    import os as _os
    from piia_engram.agents_md_export import build_agents_md_export
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram export-agents-md [--scope global|project] [--project NAME]\n"
            "                          [--max-sensitivity public|personal|work]\n"
            "                          [--out PATH]\n"
        )
        return 0

    def _opt(flag: str, default: str = "") -> str:
        if flag in args:
            idx = args.index(flag)
            if idx + 1 < len(args):
                return args[idx + 1]
        return default

    scope = _opt("--scope", "global")
    if scope not in {"global", "project"}:
        print("ERROR: --scope must be 'global' or 'project'")
        return 2
    project = _opt("--project", "")
    if scope == "project" and not project:
        print("ERROR: --scope project requires --project NAME")
        return 2
    max_sensitivity = _opt("--max-sensitivity", "work")
    out_path = _opt("--out", "")

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)
    lessons = eng.get_lessons(limit=None, _update_access=False)
    decisions = eng.get_decisions(limit=None, _update_access=False)
    block = build_agents_md_export(
        lessons=lessons,
        decisions=decisions,
        scope=scope,
        project=project,
        max_sensitivity=max_sensitivity,
    )

    if out_path:
        dest = Path(out_path).expanduser().resolve()
        if dest.exists():
            print(f"ERROR: refusing to overwrite an existing file: {dest}")
            return 2
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(block, encoding="utf-8")
        print(f"Wrote AGENTS.md export: {dest}")
        return 0
    print(block)
    return 0


def _run_recall(args: list[str]) -> int:
    """Print a single-call recall digest for the owner (engram recall).

    Local + owner-run (CLI = ``private-self``): composes existing governed read
    methods (profile slice, recent context, relevant lessons, optional keyword
    search) via ``recall_service.gather_recall``, collapses superseded knowledge
    to its current head, and renders a metadata/summary-only digest. It adds no
    new agent-facing surface — the MCP recall tool stays deferred per
    docs/specs/recall-surface-v1.md §6.
    """
    import os as _os
    from piia_engram.core import Engram
    from piia_engram.recall_service import gather_recall, render_recall_text

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram recall [--project NAME] [--query TEXT] [--budget N]\n"
            "                [--no-freshness] [--no-collapse] [--no-trust] [--json]\n"
        )
        return 0

    def _opt(flag: str, default: str = "") -> str:
        if flag in args:
            idx = args.index(flag)
            if idx + 1 < len(args):
                return args[idx + 1]
        return default

    project = _opt("--project", "")
    query = _opt("--query", "")
    try:
        budget = int(_opt("--budget", "2000"))
    except ValueError:
        print("ERROR: --budget must be an integer")
        return 2

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)
    payload = gather_recall(
        eng,
        project_folder=project,
        query=query,
        token_budget=budget,
        include_freshness="--no-freshness" not in args,
        include_trust="--no-trust" not in args,  # CLI = owner/private-self: show why-trustworthy
        collapse_versions="--no-collapse" not in args,
    )

    if "--json" in args:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(render_recall_text(payload))
    return 0


_DOCK_CONTRACT_VERSION = "M1"
_DOCK_ZERO_WRITE_ACTIONS = (
    "dock-status",
    "dock-resume",
    "dock-search",
    "dock-list",
    "dock-portrait",
    "dock-archived",
    "dock-get-lang",
    "dock-onboard-scan",
)
_DOCK_OWNER_WRITE_ACTIONS = (
    "dock-archive",
    "dock-restore",
    "dock-onboard-commit",
    "dock-update",
    "dock-set-lang",
)
_DOCK_EXPORT_ACTIONS = ("dock-export",)


def _dock_capabilities() -> dict:
    return {
        "zero_write": list(_DOCK_ZERO_WRITE_ACTIONS),
        "owner_write": list(_DOCK_OWNER_WRITE_ACTIONS),
        "export_write": list(_DOCK_EXPORT_ACTIONS),
    }


def _dock_labeling_projection(item: dict) -> dict:
    labeling = item.get("labeling") if isinstance(item, dict) else None
    if not isinstance(labeling, dict):
        return {}
    out = {}
    for key in ("source_kind", "annotation_quality", "validation_state"):
        value = labeling.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    signals = labeling.get("signals")
    if isinstance(signals, list):
        clean = [str(signal) for signal in signals if str(signal)]
        if clean:
            out["signals"] = clean[:12]
    return out


def _run_dock_resume(args: list[str]) -> int:
    """Emit a zero-write, paste-ready resume brief for a local desktop client.

    Local + owner-run. Opens the store ``read_only`` (guaranteed zero writes to
    the store root: no session stamp, no audit, no migration, no structure/index
    creation) and returns the cross-tool resume brief from ``get_resume_brief``.
    Text by default (paste-ready markdown); ``--json`` emits the structured dict
    a client parses. ``ENGRAM_DIR`` selects the store (a client passes it
    explicitly); the brief is identity-only when no ``--project`` is given.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-resume [--project PATH] [--budget N] [--json]\n\n"
            "  Zero-write resume brief for a local desktop client.\n"
            "  Opens the store read-only — never mutates the store root.\n"
        )
        return 0

    project = ""
    budget = 2000
    want_json = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            want_json = True
        elif a == "--project":
            # A following flag-like token is a missing value, not the value —
            # never swallow "--json"/"--bogus" as the project path.
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                print("ERROR: --project requires a value")
                return 2
            i += 1
            project = args[i]
        elif a == "--budget":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                print("ERROR: --budget requires a value")
                return 2
            i += 1
            try:
                budget = int(args[i])
            except ValueError:
                print("ERROR: --budget must be an integer")
                return 2
            if budget <= 0:
                print("ERROR: --budget must be a positive integer")
                return 2
        else:
            print(f"ERROR: unknown option: {a}")
            return 2
        i += 1

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    try:
        eng = Engram(root=root, read_only=True)
        brief = eng.get_resume_brief(project_folder=project, token_budget=budget)
    except Exception as exc:  # never crash the Dock spawn — emit a usable error
        if want_json:
            print(json.dumps(
                {"ok": False, "error": str(exc),
                 "engram_dir": str(root), "markdown": ""},
                ensure_ascii=False,
            ))
        else:
            print(f"ERROR: could not build resume brief: {exc}")
        return 1

    markdown = brief.get("markdown", "") if isinstance(brief, dict) else str(brief)
    if want_json:
        out = dict(brief) if isinstance(brief, dict) else {"markdown": markdown}
        out.setdefault("ok", True)
        out["engram_dir"] = str(root)
        out["read_only"] = True
        print(json.dumps(out, ensure_ascii=False))
        return 0
    print(markdown)
    return 0


def _run_dock_search(args: list[str]) -> int:
    """Zero-write keyword search over knowledge for a local desktop client.

    Local + owner-run. Opens the store ``read_only`` and runs ``search_knowledge``
    with ``allow_hybrid_index=False`` so it never builds/persists the FTS/vector
    index (``search_index.db``) — guaranteed zero writes to the store root. Emits
    flattened JSON results (one list across lessons/decisions/playbooks) that a
    client renders; ``--json`` is the structured form, text is a readable fallback.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-search --query TEXT [--scope all|lessons|decisions|playbooks]\n"
            "                     [--limit N] [--json]\n\n"
            "  Zero-write keyword search for a local desktop client.\n"
            "  Opens the store read-only — never mutates the store root.\n"
        )
        return 0

    query = ""
    scope = "all"
    limit = 8
    want_json = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            want_json = True
        elif a == "--query":
            # A following flag-like token is a missing value, not the value.
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                print("ERROR: --query requires a value")
                return 2
            i += 1
            query = args[i]
        elif a == "--scope":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                print("ERROR: --scope requires a value")
                return 2
            i += 1
            scope = args[i]
            if scope not in {"all", "lessons", "decisions", "playbooks"}:
                print("ERROR: --scope must be all|lessons|decisions|playbooks")
                return 2
        elif a == "--limit":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                print("ERROR: --limit requires a value")
                return 2
            i += 1
            try:
                limit = int(args[i])
            except ValueError:
                print("ERROR: --limit must be an integer")
                return 2
            if limit <= 0:
                print("ERROR: --limit must be a positive integer")
                return 2
        else:
            print(f"ERROR: unknown option: {a}")
            return 2
        i += 1

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")

    if not query.strip():
        if want_json:
            print(json.dumps(
                {"ok": False, "error": "empty query",
                 "engram_dir": str(root), "results": [], "count": 0},
                ensure_ascii=False,
            ))
        else:
            print("ERROR: --query is required and must be non-empty")
        return 2

    try:
        eng = Engram(root=root, read_only=True)
        # allow_hybrid_index=False keeps this zero-write: it never builds or
        # persists the FTS/vector index to <root>/search_index.db.
        raw = eng.search_knowledge(
            query, scope=scope, limit=limit, allow_hybrid_index=False,
        )
    except Exception as exc:  # never crash the Dock spawn — emit a usable error
        if want_json:
            print(json.dumps(
                {"ok": False, "error": str(exc),
                 "engram_dir": str(root), "results": [], "count": 0},
                ensure_ascii=False,
            ))
        else:
            print(f"ERROR: search failed: {exc}")
        return 1

    def _title(kind: str, it: dict) -> str:
        if kind == "decisions":
            q = (it.get("question") or it.get("title") or "").strip()
            c = (it.get("choice") or "").strip()
            return f"{q} → {c}" if q and c else (q or c or "(decision)")
        if kind == "playbooks":
            return (it.get("title") or it.get("name") or "(playbook)").strip()
        return (it.get("summary") or "(lesson)").strip()

    def _copy(kind: str, it: dict) -> str:
        if kind == "decisions":
            parts = [_title(kind, it)]
            reasoning = (it.get("reasoning") or "").strip()
            if reasoning:
                parts.append(reasoning)
            return "\n".join(parts)
        if kind == "playbooks":
            return _title(kind, it)
        parts = [(it.get("summary") or "").strip()]
        detail = (it.get("detail") or "").strip()
        if detail:
            parts.append(detail)
        return "\n".join([p for p in parts if p])

    results = []
    for kind in ("lessons", "decisions", "playbooks"):
        for it in raw.get(kind, []):
            entry = {
                "kind": kind[:-1],  # lesson / decision / playbook
                "title": _title(kind, it),
                "tier": it.get("tier", ""),
                "id": it.get("id", ""),
                "copy": _copy(kind, it),
            }
            labeling = _dock_labeling_projection(it)
            if labeling:
                entry["labeling"] = labeling
            # raw editable fields for the dock's inline edit (lesson/decision only)
            if kind == "lessons":
                entry["fields"] = {
                    "summary": it.get("summary", "") or "",
                    "detail": it.get("detail", "") or "",
                }
            elif kind == "decisions":
                entry["fields"] = {
                    # extraction-written decisions keep primary text in `title`
                    "question": it.get("question") or it.get("title") or "",
                    "choice": it.get("choice", "") or "",
                    "reasoning": it.get("reasoning", "") or "",
                }
            results.append(entry)

    if want_json:
        print(json.dumps(
            {"ok": True, "read_only": True, "engram_dir": str(root),
             "query": query, "count": len(results), "results": results},
            ensure_ascii=False,
        ))
        return 0

    if not results:
        print(f"(no matches for: {query})")
        return 0
    for r in results:
        tier = f" [{r['tier']}]" if r["tier"] else ""
        print(f"- ({r['kind']}{tier}) {r['title']}")
    return 0


def _run_dock_export(args: list[str]) -> int:
    """One-click full export for a local desktop client (engram dock-export).

    Local + owner-run. Writes the entire store (identity + knowledge + projects)
    to a single JSON backup via ``export_all`` and emits the path. This is an
    explicit WRITE action — it produces a backup file (handle as sensitive) — and
    is NOT zero-write like dock-resume/dock-search. ``--output`` overrides the
    default location (``<engram>/exports/engram_backup_<date>.json``).
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-export [--output PATH] [--json]\n\n"
            "  One-click full JSON backup for a local desktop client.\n"
            "  Writes a backup file (treat as sensitive); does not mutate memory.\n"
        )
        return 0

    # Compute --json up front so even arg-parse errors honor the JSON contract.
    want_json = "--json" in args

    def _arg_err(msg: str) -> int:
        if want_json:
            print(json.dumps({"ok": False, "error": msg, "path": ""},
                             ensure_ascii=False))
        else:
            print(f"ERROR: {msg}")
        return 2

    output = ""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            pass  # already accounted for above
        elif a == "--output":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _arg_err("--output requires a value")
            i += 1
            output = args[i]
        else:
            return _arg_err(f"unknown option: {a}")
        i += 1

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    try:
        eng = Engram(root=root)
        path = eng.export_all(output or None)
    except Exception as exc:  # never crash the Dock spawn — emit a usable error
        if want_json:
            print(json.dumps(
                {"ok": False, "error": str(exc), "engram_dir": str(root), "path": ""},
                ensure_ascii=False,
            ))
        else:
            print(f"ERROR: export failed: {exc}")
        return 1

    if want_json:
        print(json.dumps(
            {"ok": True, "engram_dir": str(root), "path": str(path)},
            ensure_ascii=False,
        ))
        return 0
    print(f"导出成功: {path}")
    return 0


def _run_dock_portrait(args: list[str]) -> int:
    """Zero-write user portrait ("查看画像") for a local desktop client.

    Local + owner-run. Opens the store ``read_only`` and builds the lean user
    portrait in memory (identity + aggregate stats), plus the growth delta vs the
    latest saved snapshot when one exists — but NEVER saves a new snapshot, so it
    is a guaranteed zero-write. ``--json`` wraps the rendered markdown; text is the
    readable fallback.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-portrait [--json]\n"
            "  engram dock-portrait --html --output PATH\n\n"
            "  Zero-write user portrait for a local desktop client. --html renders a\n"
            "  full styled HTML page to PATH; default emits text/markdown.\n"
            "  Opens the store read-only and never saves a snapshot.\n"
        )
        return 0

    want_json = "--json" in args
    want_html = "--html" in args

    def _err(msg: str, code: int = 2) -> int:
        if want_json:
            print(json.dumps(
                {"ok": False, "error": msg, "markdown": "", "path": ""},
                ensure_ascii=False,
            ))
        else:
            print(f"ERROR: {msg}")
        return code

    output = ""
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--json", "--html"):
            pass
        elif a == "--output":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _err("--output requires a value")
            i += 1
            output = args[i]
        else:
            return _err(f"unknown option: {a}")
        i += 1
    if want_html and not output:
        return _err("--html requires --output")

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    try:
        eng = Engram(root=root, read_only=True)
        portrait = eng.build_user_portrait_rich() if want_html else eng.build_user_portrait()
        previous = eng.get_latest_portrait()
        growth = eng.compare_user_portraits(previous, portrait) if previous else None
    except Exception as exc:  # never crash the Dock spawn — emit a usable error
        return _err(str(exc), 1)

    if want_html:
        try:
            page = eng.render_user_portrait_html(portrait, growth)
            Path(output).write_text(page, encoding="utf-8")
        except Exception as exc:
            return _err(f"could not write portrait HTML: {exc}", 1)
        if want_json:
            print(json.dumps(
                {"ok": True, "read_only": True, "path": str(output)},
                ensure_ascii=False,
            ))
        else:
            print(f"已生成画像: {output}")
        return 0

    text = eng.render_user_portrait(portrait)
    if growth:
        text = f"{text}\n{eng.render_portrait_growth(growth)}"
    if want_json:
        print(json.dumps(
            {"ok": True, "read_only": True, "engram_dir": str(root),
             "markdown": text},
            ensure_ascii=False,
        ))
        return 0
    print(text, end="")
    return 0


def _run_dock_archive(args: list[str]) -> int:
    """Owner-confirmed reversible archive of one entry (engram dock-archive).

    Local + owner-run. Soft-archives a lesson/decision by id into the ``archived``
    tier — a deliberate, REVERSIBLE write (recover via ``dock-restore``). The owner
    confirmed in the dock UI, so ``allow_verified=True`` lets even a verified entry
    be archived (still fully reversible; nothing is deleted). ``--id`` is required.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-archive --id ID [--json]\n\n"
            "  Owner-confirmed reversible archive; recover via `engram dock-restore`.\n"
        )
        return 0

    want_json = "--json" in args

    def _err(msg: str, code: int = 2) -> int:
        if want_json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        else:
            print(f"ERROR: {msg}")
        return code

    item_id = ""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            pass
        elif a == "--id":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _err("--id requires a value")
            i += 1
            item_id = args[i]
        else:
            return _err(f"unknown option: {a}")
        i += 1
    if not item_id.strip():
        return _err("--id is required")

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    try:
        eng = Engram(root=root)
        result = eng.soft_archive_knowledge_tier(item_id, allow_verified=True)
    except Exception as exc:
        return _err(str(exc), 1)
    if isinstance(result, dict) and result.get("error"):
        return _err(str(result["error"]), 1)
    if want_json:
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    print(f"已归档（可恢复）: {item_id}")
    return 0


def _run_dock_restore(args: list[str]) -> int:
    """Restore one archived entry (engram dock-restore).

    Local + owner-run. Undoes a soft archive: moves an ``archived`` lesson/decision
    back to its prior tier. A deliberate write. ``--id`` is required.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-restore --id ID [--json]\n\n"
            "  Undo a dock-archive: move an archived entry back to its prior tier.\n"
        )
        return 0

    want_json = "--json" in args

    def _err(msg: str, code: int = 2) -> int:
        if want_json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        else:
            print(f"ERROR: {msg}")
        return code

    item_id = ""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            pass
        elif a == "--id":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _err("--id requires a value")
            i += 1
            item_id = args[i]
        else:
            return _err(f"unknown option: {a}")
        i += 1
    if not item_id.strip():
        return _err("--id is required")

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    try:
        eng = Engram(root=root)
        result = eng.restore_lifecycle_archive(item_id)
    except Exception as exc:
        return _err(str(exc), 1)
    if isinstance(result, dict) and result.get("error"):
        return _err(str(result["error"]), 1)
    if want_json:
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    print(f"已恢复: {item_id}")
    return 0


def _run_dock_archived(args: list[str]) -> int:
    """Zero-write list of archived entries for a local desktop client.

    Local + owner-run. Opens the store ``read_only`` and lists lessons/decisions in
    the ``archived`` tier (id + kind + title) so a client can offer one-click
    restore. Guaranteed zero-write.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-archived [--json]\n\n"
            "  Zero-write list of archived entries (id/kind/title) for restore.\n"
        )
        return 0

    want_json = "--json" in args
    for a in args:
        if a != "--json":
            if want_json:
                print(json.dumps(
                    {"ok": False, "error": f"unknown option: {a}",
                     "results": [], "count": 0},
                    ensure_ascii=False,
                ))
            else:
                print(f"ERROR: unknown option: {a}")
            return 2

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")

    def _title(kind: str, it: dict) -> str:
        if kind == "decision":
            q = (it.get("question") or it.get("title") or "").strip()
            c = (it.get("choice") or "").strip()
            return f"{q} → {c}" if q and c else (q or c or "(decision)")
        return (it.get("summary") or "(lesson)").strip()

    try:
        eng = Engram(root=root, read_only=True)
        results = []
        for kind, fname in (("lesson", "lessons.json"), ("decision", "decisions.json")):
            for it in eng._read_entries(eng._knowledge_dir / fname, kind):
                if it.get("tier") == "archived":
                    results.append({
                        "kind": kind,
                        "title": _title(kind, it),
                        "id": it.get("id", ""),
                    })
    except Exception as exc:
        if want_json:
            print(json.dumps(
                {"ok": False, "error": str(exc), "results": [], "count": 0},
                ensure_ascii=False,
            ))
        else:
            print(f"ERROR: list archived failed: {exc}")
        return 1

    if want_json:
        print(json.dumps(
            {"ok": True, "read_only": True, "engram_dir": str(root),
             "count": len(results), "results": results},
            ensure_ascii=False,
        ))
        return 0
    if not results:
        print("(no archived entries)")
        return 0
    for r in results:
        print(f"- ({r['kind']}) {r['title']}")
    return 0


def _collect_folder_signals(folder: str) -> tuple[str, list[str]]:
    """Read a project folder's recent git commit subjects + README as text.

    Best-effort and read-only: returns ("", []) when the folder is missing or has
    nothing usable. git failures (not a repo / git absent / timeout) are swallowed
    so onboarding degrades to whatever else was provided.
    """
    import subprocess

    try:
        p = Path(folder)
        if not p.is_dir():
            return "", []
    except Exception:
        return "", []
    chunks: list[str] = []
    used: list[str] = []

    # recent commit subjects (best-effort; ignore if not a git repo / git missing)
    try:
        creation = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW on Windows
        proc = subprocess.run(
            ["git", "-C", str(p), "log", "--no-merges",
             "--pretty=format:%s", "-n", "200"],
            capture_output=True, text=True, timeout=15, creationflags=creation,
            encoding="utf-8", errors="replace",  # git messages are UTF-8, not locale (gbk)
        )
        if proc.returncode == 0 and proc.stdout.strip():
            chunks.append(proc.stdout)
            used.append("git-log")
    except Exception:
        pass

    # README (first match found, truncated to keep extraction fast)
    for name in ("README.md", "README.MD", "readme.md", "Readme.md",
                 "README.txt", "README"):
        rp = p / name
        if rp.is_file():
            try:
                with rp.open("r", encoding="utf-8", errors="replace") as fh:
                    chunks.append(fh.read(16000))  # bounded read, not read-all-then-slice
                used.append("readme")
            except Exception:
                pass
            break

    return "\n".join(chunks), used


def _run_dock_onboard_scan(args: list[str]) -> int:
    """Zero-write onboarding scan + candidate preview (engram dock-onboard-scan).

    Local + owner-run. Gathers free-form text from ``--text`` / ``--text-file``
    and, when ``--folder`` is given, that project's recent git commit subjects +
    README, then runs a DRY-RUN extraction (:meth:`Engram.extract_candidates`) so
    the desktop client can preview lesson/decision candidates for the owner to
    tick before anything is written. Opens the store ``read_only`` — guaranteed
    zero-write. Confirm via ``dock-onboard-commit``.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-onboard-scan [--text TEXT] [--text-file PATH] "
            "[--folder DIR] [--source TOOL] [--json]\n\n"
            "  Zero-write: collect text (and a folder's git log + README) and "
            "preview\n"
            "  lesson/decision candidates. Nothing is saved; confirm via "
            "dock-onboard-commit.\n"
        )
        return 0

    want_json = "--json" in args

    def _err(msg: str, code: int = 2) -> int:
        if want_json:
            print(json.dumps(
                {"ok": False, "error": msg, "candidates": [], "count": 0},
                ensure_ascii=False,
            ))
        else:
            print(f"ERROR: {msg}")
        return code

    text = ""
    text_file = ""
    folder = ""
    source = "onboarding"
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            pass
        elif a == "--text":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _err("--text requires a value")
            i += 1
            text = args[i]
        elif a == "--text-file":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _err("--text-file requires a value")
            i += 1
            text_file = args[i]
        elif a == "--folder":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _err("--folder requires a value")
            i += 1
            folder = args[i]
        elif a == "--source":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _err("--source requires a value")
            i += 1
            source = args[i]
        else:
            return _err(f"unknown option: {a}")
        i += 1

    parts: list[str] = []
    sources: list[str] = []
    if text.strip():
        parts.append(text)
        sources.append("text")
    if text_file:
        try:
            parts.append(Path(text_file).read_text(encoding="utf-8", errors="replace"))
            sources.append("text-file")
        except Exception as exc:
            return _err(f"could not read --text-file: {exc}", 1)
    if folder:
        collected, used = _collect_folder_signals(folder)
        if collected:
            parts.append(collected)
            sources.extend(used)

    combined = "\n".join(s for s in parts if s and s.strip())
    if not combined.strip():
        return _err(
            "no input — provide --text, --text-file, or a --folder with git/README",
            2,
        )

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    try:
        eng = Engram(root=root, read_only=True)
        out = eng.extract_candidates(combined, source_tool=source)
    except Exception as exc:
        return _err(str(exc), 1)

    cands = out.get("candidates", [])
    if want_json:
        print(json.dumps(
            {"ok": True, "read_only": True, "sources": sources,
             "count": len(cands), "candidates": cands,
             "skipped": out.get("skipped", 0)},
            ensure_ascii=False,
        ))
        return 0
    if not cands:
        print("(no candidates found)")
        return 0
    for c in cands:
        print(f"- [{c.get('type')}] {c.get('text')}")
    return 0


def _run_dock_onboard_commit(args: list[str]) -> int:
    """Write owner-confirmed onboarding candidates (engram dock-onboard-commit).

    Local + owner-run. Reads a JSON array of candidates the owner ticked in the
    dock from ``--candidates-file`` (written by the client to a temp file, which
    avoids command-line length limits), then writes them via
    :meth:`Engram.commit_candidates`. A DELIBERATE write — the onboarding confirm
    step; everything written is reversible via ``dock-archive``. Returns counts.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-onboard-commit --candidates-file PATH "
            "[--source TOOL] [--json]\n\n"
            "  Writes owner-confirmed onboarding candidates (a JSON array of\n"
            "  {type,text,domain}). A deliberate write; recover via dock-archive.\n"
        )
        return 0

    want_json = "--json" in args

    def _err(msg: str, code: int = 2) -> int:
        if want_json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        else:
            print(f"ERROR: {msg}")
        return code

    cand_file = ""
    source = "onboarding"
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            pass
        elif a == "--candidates-file":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _err("--candidates-file requires a value")
            i += 1
            cand_file = args[i]
        elif a == "--source":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _err("--source requires a value")
            i += 1
            source = args[i]
        else:
            return _err(f"unknown option: {a}")
        i += 1

    if not cand_file:
        return _err("--candidates-file is required")
    try:
        candidates = json.loads(Path(cand_file).read_text(encoding="utf-8"))
    except Exception as exc:
        return _err(f"could not read --candidates-file: {exc}", 1)
    if not isinstance(candidates, list):
        return _err("--candidates-file must contain a JSON array")
    if not candidates:
        return _err("no candidates to write", 2)

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    try:
        eng = Engram(root=root)
        result = eng.commit_candidates(candidates, source_tool=source)
    except Exception as exc:
        return _err(str(exc), 1)

    if want_json:
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    print(
        f"已写入: 经验 {result.get('saved_lessons', 0)}、"
        f"决策 {result.get('saved_decisions', 0)}、"
        f"重复跳过 {result.get('duplicates', 0)}"
    )
    return 0


def _run_dock_update(args: list[str]) -> int:
    """Edit one entry's content by id (engram dock-update).

    Local + owner-run. A DELIBERATE write: updates the allowed text fields of a
    lesson/decision via :meth:`Engram.update_knowledge`. The edited fields come
    from ``--updates-file`` (a JSON object the dock writes to a temp file). Only a
    whitelist of content fields is honored; a primary field (summary/question/
    choice) may be edited but not blanked. ``--id`` and ``--updates-file`` required.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-update --id ID --updates-file PATH [--json]\n\n"
            "  Edit a lesson/decision's content (summary/detail or\n"
            "  question/choice/reasoning). A deliberate write.\n"
        )
        return 0

    want_json = "--json" in args

    def _err(msg: str, code: int = 2) -> int:
        if want_json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        else:
            print(f"ERROR: {msg}")
        return code

    item_id = ""
    upd_file = ""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            pass
        elif a == "--id":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _err("--id requires a value")
            i += 1
            item_id = args[i]
        elif a == "--updates-file":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _err("--updates-file requires a value")
            i += 1
            upd_file = args[i]
        else:
            return _err(f"unknown option: {a}")
        i += 1

    if not item_id.strip():
        return _err("--id is required")
    if not upd_file:
        return _err("--updates-file is required")
    try:
        raw_updates = json.loads(Path(upd_file).read_text(encoding="utf-8"))
    except Exception as exc:
        return _err(f"could not read --updates-file: {exc}", 1)
    if not isinstance(raw_updates, dict):
        return _err("--updates-file must contain a JSON object")

    allowed = {"summary", "detail", "question", "choice", "reasoning"}
    updates = {
        k: v.strip() for k, v in raw_updates.items()
        if k in allowed and isinstance(v, str)
    }
    if not updates:
        return _err("no valid fields to update")
    # a primary field may be edited but never blanked (would gut the entry)
    for k in ("summary", "question", "choice"):
        if k in updates and not updates[k]:
            return _err(f"{k} cannot be empty")
    # legacy compat: extraction-written decisions keep their primary text in
    # `title` (question is null). When the owner edits `question`, sync `title`
    # too so identity/dedup/report code (which prefers `title`) isn't left stale.
    if updates.get("question"):
        updates["title"] = updates["question"]

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    try:
        eng = Engram(root=root)
        result = eng.update_knowledge(item_id, updates)
    except Exception as exc:
        return _err(str(exc), 1)
    if isinstance(result, dict) and result.get("error"):
        return _err(str(result["error"]), 1)
    if want_json:
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    print(f"已更新: {item_id}")
    return 0


def _run_dock_list(args: list[str]) -> int:
    """Zero-write list of ALL active entries for the dock's "我的记忆" view.

    Local + owner-run. Opens the store ``read_only`` and lists every active
    lesson/decision (id/kind/tier/title + the same editable ``fields`` shape as
    ``dock-search``) so the desktop client can show the whole memory at once and
    filter it client-side — no query required. Excludes the ``archived`` tier
    (those live in ``dock-archived``/restore) and any non-active ``status``
    (superseded/outdated decisions, which keep their tier but drop out of the
    active set). Guaranteed zero-write. ``--limit`` caps the count (most-recent
    leaning) to protect a very large store.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-list [--limit N] [--json]\n\n"
            "  Zero-write list of all active lessons/decisions (id/kind/tier/\n"
            "  title + editable fields) for a local desktop client. Opens the\n"
            "  store read-only — never mutates the store root.\n"
        )
        return 0

    want_json = "--json" in args
    limit = 0  # 0 = no cap

    def _err(msg: str, code: int = 2) -> int:
        if want_json:
            print(json.dumps(
                {"ok": False, "error": msg, "results": [], "count": 0},
                ensure_ascii=False,
            ))
        else:
            print(f"ERROR: {msg}")
        return code

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            pass
        elif a == "--limit":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _err("--limit requires a value")
            i += 1
            try:
                limit = int(args[i])
            except ValueError:
                return _err("--limit must be an integer")
            if limit <= 0:
                return _err("--limit must be a positive integer")
        else:
            return _err(f"unknown option: {a}")
        i += 1

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")

    def _title(kind: str, it: dict) -> str:
        if kind == "decision":
            # extraction-written decisions keep their primary text in `title`
            # (question is null) — fall back so they never render as "(decision)".
            q = (it.get("question") or it.get("title") or "").strip()
            c = (it.get("choice") or "").strip()
            return f"{q} → {c}" if q and c else (q or c or "(decision)")
        return (it.get("summary") or "(lesson)").strip()

    def _copy(kind: str, it: dict) -> str:
        if kind == "decision":
            parts = [_title(kind, it)]
            reasoning = (it.get("reasoning") or "").strip()
            if reasoning:
                parts.append(reasoning)
            return "\n".join(parts)
        parts = [(it.get("summary") or "").strip()]
        detail = (it.get("detail") or "").strip()
        if detail:
            parts.append(detail)
        return "\n".join([p for p in parts if p])

    try:
        eng = Engram(root=root, read_only=True)
        results = []
        for kind, fname in (("lesson", "lessons.json"), ("decision", "decisions.json")):
            for it in eng._read_entries(eng._knowledge_dir / fname, kind):
                if it.get("tier") == "archived":
                    continue  # belongs to dock-archived / restore
                if (it.get("status") or "active") != "active":
                    continue  # superseded / outdated — not part of active memory
                entry = {
                    "kind": kind,
                    "title": _title(kind, it),
                    "tier": it.get("tier", "") or "",
                    "id": it.get("id", ""),
                    "copy": _copy(kind, it),
                }
                labeling = _dock_labeling_projection(it)
                if labeling:
                    entry["labeling"] = labeling
                if kind == "lesson":
                    entry["fields"] = {
                        "summary": it.get("summary", "") or "",
                        "detail": it.get("detail", "") or "",
                    }
                else:
                    entry["fields"] = {
                        # mirror dock-search: question falls back to legacy title
                        "question": it.get("question") or it.get("title") or "",
                        "choice": it.get("choice", "") or "",
                        "reasoning": it.get("reasoning", "") or "",
                    }
                results.append(entry)
    except Exception as exc:  # never crash the Dock spawn — emit a usable error
        return _err(str(exc), 1)

    if limit and len(results) > limit:
        results = results[-limit:]  # keep the most-recent N (entries append-ordered)

    if want_json:
        print(json.dumps(
            {"ok": True, "read_only": True, "engram_dir": str(root),
             "count": len(results), "results": results},
            ensure_ascii=False,
        ))
        return 0
    if not results:
        print("(no entries)")
        return 0
    for r in results:
        tier = f" [{r['tier']}]" if r["tier"] else ""
        print(f"- ({r['kind']}{tier}) {r['title']}")
    return 0


def _run_dock_set_lang(args: list[str]) -> int:
    """Set the owner's preferred language (engram dock-set-lang --lang zh|en).

    Local + owner-run. A small DELIBERATE write: updates ``language`` in
    ``identity/profile.json`` via :meth:`Engram.update_profile`, so every Engram
    surface that honors the profile language (``i18n.get_lang`` → portrait /
    preview / CLI text) follows the dock's language toggle. ``--lang`` is required
    and must be ``zh`` or ``en``. Stores the same human-readable value
    setup_wizard writes (``中文``/``English``); ``get_lang`` reads it back through
    the same ``"en" in value`` test.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-set-lang --lang zh|en [--json]\n\n"
            "  Set the owner's preferred language in identity/profile.json so\n"
            "  Engram's portrait / preview / CLI text follow the dock toggle.\n"
        )
        return 0

    want_json = "--json" in args

    def _err(msg: str, code: int = 2) -> int:
        if want_json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        else:
            print(f"ERROR: {msg}")
        return code

    lang = ""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            pass
        elif a == "--lang":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _err("--lang requires a value")
            i += 1
            lang = args[i]
        else:
            return _err(f"unknown option: {a}")
        i += 1

    lang = lang.strip().lower()
    if lang not in {"zh", "en"}:
        return _err("--lang must be zh or en")
    profile_value = "中文" if lang == "zh" else "English"

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    try:
        eng = Engram(root=root)
        eng.update_profile({"language": profile_value}, source_tool="dock")
    except Exception as exc:  # never crash the Dock spawn — emit a usable error
        return _err(str(exc), 1)

    if want_json:
        print(json.dumps(
            {"ok": True, "lang": lang, "language": profile_value,
             "engram_dir": str(root)},
            ensure_ascii=False,
        ))
        return 0
    print(f"已切换语言: {profile_value}")
    return 0


def _run_dock_get_lang(args: list[str]) -> int:
    """Zero-write read of the owner's current language (engram dock-get-lang).

    Local + owner-run. Resolves the language via :func:`i18n.get_lang` (reads
    ``identity/profile.json`` honoring ``ENGRAM_DIR``) so the desktop dock can
    render its own UI in the owner's language on startup and follow a later
    toggle. Returns ``"zh"`` or ``"en"``. Guaranteed zero-write — never opens the
    store for writing.
    """
    from piia_engram.i18n import get_lang

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-get-lang [--json]\n\n"
            "  Zero-write read of the owner's language (zh|en) from\n"
            "  identity/profile.json, for a desktop client to follow.\n"
        )
        return 0

    want_json = "--json" in args
    for a in args:
        if a != "--json":
            if want_json:
                print(json.dumps(
                    {"ok": False, "error": f"unknown option: {a}", "lang": "zh"},
                    ensure_ascii=False,
                ))
            else:
                print(f"ERROR: unknown option: {a}")
            return 2

    try:
        lang = get_lang()
    except Exception:
        lang = "zh"
    lang = "en" if str(lang).strip().lower().startswith("en") else "zh"
    if want_json:
        print(json.dumps({"ok": True, "lang": lang}, ensure_ascii=False))
        return 0
    print(lang)
    return 0


def _run_dock_status(args: list[str]) -> int:
    """Zero-write owner-console status for a desktop Dock client.

    This is the Dock home-screen contract: metadata-only health, counts,
    governance visibility, and Dock action capabilities. It never probes
    external executables and never opens the Engram store for writing.
    """
    import os as _os
    from piia_engram.status_report import build_status, render_status_text

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram dock-status [--json]\n\n"
            "  Zero-write metadata-only status for a local desktop client.\n"
            "  Includes knowledge counts, governance visibility, and Dock M1\n"
            "  action capabilities. Does not probe or mutate the store root.\n"
        )
        return 0

    want_json = "--json" in args

    def _err(msg: str, code: int = 2) -> int:
        if want_json:
            print(json.dumps(
                {"ok": False, "error": msg, "status": {}},
                ensure_ascii=False,
            ))
        else:
            print(f"ERROR: {msg}")
        return code

    for a in args:
        if a != "--json":
            return _err(f"unknown option: {a}")

    source_was_set = "ENGRAM_CALLER_SOURCE" in _os.environ
    initiation_was_set = "ENGRAM_INITIATION_SOURCE" in _os.environ
    if not source_was_set:
        _os.environ["ENGRAM_CALLER_SOURCE"] = "desktop_dock"
    if not initiation_was_set:
        _os.environ["ENGRAM_INITIATION_SOURCE"] = "unknown"

    try:
        status = build_status(probe=False)
    except Exception as exc:
        return _err(str(exc), 1)
    finally:
        if not source_was_set:
            _os.environ.pop("ENGRAM_CALLER_SOURCE", None)
        if not initiation_was_set:
            _os.environ.pop("ENGRAM_INITIATION_SOURCE", None)

    root = str(
        status.get("root")
        or Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    )
    payload = {
        "ok": True,
        "read_only": True,
        "engram_dir": root,
        "dock_contract_version": _DOCK_CONTRACT_VERSION,
        "dock_capabilities": _dock_capabilities(),
        "status": status,
    }
    if want_json:
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    print(
        f"Dock contract: {_DOCK_CONTRACT_VERSION}\n"
        f"Zero-write actions: {', '.join(_DOCK_ZERO_WRITE_ACTIONS)}\n"
        f"Owner-write actions: {', '.join(_DOCK_OWNER_WRITE_ACTIONS)}\n\n"
        f"{render_status_text(status, redact_paths=False)}",
        end="",
    )
    return 0


def _run_portrait(args: list[str]) -> int:
    """Build, store, and compare a lean user portrait (engram portrait).

    Local + owner-run. Composes ``build_user_portrait`` (identity + aggregate
    stats, no raw knowledge text), persists a timestamped snapshot under
    ``<engram>/portraits/``, and — if an earlier snapshot exists — prints the
    growth delta since the previous one. ``--no-save`` builds without writing,
    ``--list`` shows stored snapshots, ``--json`` emits raw structures.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram portrait            Build + save a snapshot, show growth since last\n"
            "  engram portrait --no-save  Build + show without writing a snapshot\n"
            "  engram portrait --list     List stored snapshots (newest first)\n"
            "  engram portrait --json     Emit raw JSON instead of Markdown\n"
        )
        return 0

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)
    want_json = "--json" in args

    if "--list" in args:
        items = eng.list_user_portraits()
        if want_json:
            print(json.dumps(items, ensure_ascii=False, indent=2))
        elif not items:
            print("(no portraits stored yet / 尚无已保存的写照)")
        else:
            for it in items:
                stats = it.get("stats", {})
                print(
                    f"- {it.get('generated_at', '')}  "
                    f"lessons={stats.get('lesson_count', 0)} "
                    f"decisions={stats.get('decision_count', 0)} "
                    f"domains={stats.get('domain_count', 0)}"
                )
        return 0

    # Capture the prior snapshot BEFORE writing the new one (growth baseline).
    previous = eng.get_latest_portrait()
    portrait = eng.build_user_portrait()
    if "--no-save" not in args:
        eng.save_user_portrait(portrait)

    if want_json:
        out: dict = {"portrait": portrait}
        if previous:
            out["growth"] = eng.compare_user_portraits(previous, portrait)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(eng.render_user_portrait(portrait), end="")
    if previous:
        diff = eng.compare_user_portraits(previous, portrait)
        print()
        print(eng.render_portrait_growth(diff), end="")
    return 0


def _run_telemetry_validate(args: list[str]) -> int:
    """Validate telemetry payload/schema/migration consistency (read-only, no network).

    Static local check: confirms the client payload contract, worker schema, and
    v1.1 migration agree, the migration is additive/forward-only, and no
    content-bearing field exists on either side. Performs NO remote action.
    """
    from piia_engram.telemetry_validation import (
        render_readiness_text,
        render_validation_text,
        validate_remote_readiness,
        validate_telemetry_contract,
    )

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram telemetry-validate [--json]\n"
            "  engram telemetry-validate --remote-readiness [--json]\n"
            "\n"
            "  --remote-readiness  Pre-deploy checklist (payload/schema/migration\n"
            "                      sequencing, dashboard wording, opt-in defaults,\n"
            "                      no content fields). Read-only; performs no remote\n"
            "                      D1/worker action.\n"
        )
        return 0

    worker_dir = Path.cwd() / "worker"
    if "--remote-readiness" in args:
        report = validate_remote_readiness(worker_dir)
        if "--json" in args:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(render_readiness_text(report))
        return 0 if report.get("ok") else 1

    report = validate_telemetry_contract(worker_dir)
    if "--json" in args:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_validation_text(report))
    return 0 if report.get("ok") else 1


def _run_release_check(args: list[str]) -> int:
    """Print a read-only release readiness report (engram release-check).

    Aggregates required-file presence, English-first release notes, publish
    allowlist, a public-doc private-term scan, and release-evidence completeness.
    Performs NO build/tag/publish — it only reads the working tree. Exits
    non-zero when not ready so scripts/CI can gate on it.
    """
    from piia_engram.release_readiness import (
        build_release_readiness,
        render_release_readiness_text,
    )

    if args and args[0] in {"-h", "--help"}:
        print("Usage:\n  engram release-check [--json]\n")
        return 0

    # Maintainer command: run from the repo root (the working tree to ship).
    root = Path.cwd()
    report = build_release_readiness(root)
    if "--json" in args:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_release_readiness_text(report))
    return 0 if report.get("ready") else 1


def _run_dashboard(args: list[str]) -> int:
    """Print the non-technical owner control dashboard (engram dashboard).

    Read-only and metadata-only: aggregates recall trust, lifecycle proposals,
    integrity status, and export/telemetry readiness into one bilingual view.
    Surfaces proposals + the commands to act on them; performs no destructive
    action. ``--html`` writes a fully-escaped local HTML page.
    """
    import os as _os
    from piia_engram.core import Engram
    from piia_engram.integrity import scan_integrity
    from piia_engram.owner_dashboard import (
        build_owner_dashboard,
        render_dashboard_html,
        render_dashboard_text,
    )

    if args and args[0] in {"-h", "--help"}:
        print("Usage:\n  engram dashboard [--json] [--html [PATH]]\n")
        return 0

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)
    lessons = eng.get_lessons(limit=None, _update_access=False) or []
    decisions = eng.get_decisions(limit=None, _update_access=False) or []
    integrity_report = scan_integrity(root)
    telemetry_status = {}
    try:
        from piia_engram import telemetry as _tel
        telemetry_status = _tel.get_status()
    except Exception:
        telemetry_status = {}

    # Readiness reports — all read-only, metadata-only, computed here so the
    # dashboard surface stays pure. Each is best-effort and degrades to None.
    merge_report = None
    try:
        merge_report = eng.suggest_merges()
    except Exception:
        merge_report = None
    reconcile_report = None
    try:
        from piia_engram.reconcile_proposal import build_reconcile_proposal
        candidates = eng.collect_memory_candidates()
        reconcile_report = build_reconcile_proposal(
            candidates, list(lessons) + list(decisions), source="memory_files",
        )
    except Exception:
        reconcile_report = None
    version_report = None
    try:
        from piia_engram.governance_store import RelationStore
        from piia_engram.version_chain import build_version_report
        version_report = build_version_report(RelationStore(root).all_edges())
    except Exception:
        version_report = None

    dashboard = build_owner_dashboard(
        lessons=list(lessons), decisions=list(decisions),
        integrity_report=integrity_report, telemetry_status=telemetry_status,
        merge_report=merge_report, reconcile_report=reconcile_report,
        version_report=version_report,
    )

    if "--json" in args:
        print(json.dumps(dashboard, ensure_ascii=False, indent=2))
        return 0
    if "--html" in args:
        idx = args.index("--html")
        out = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("-") else ""
        dest = Path(out).expanduser().resolve() if out else (root / "reports" / "dashboard.html")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(render_dashboard_html(dashboard), encoding="utf-8")
        print(f"Wrote dashboard: {dest}")
        return 0
    print(render_dashboard_text(dashboard))
    return 0


def _run_integrity(args: list[str]) -> int:
    """Print a metadata-only integrity scan + self-heal proposals (engram integrity).

    Read-only and proposal-only: checks JSON validity, duplicate ids, store/index
    drift, governance-ledger chain, and relation/version-chain health, then
    suggests owner commands to fix any problems. It NEVER repairs, rebuilds, or
    overwrites anything — acting on a proposal is an explicit owner command.
    """
    import os as _os
    from piia_engram.integrity import (
        build_self_heal_proposals,
        render_integrity_text,
        scan_integrity,
    )

    if args and args[0] in {"-h", "--help"}:
        print("Usage:\n  engram integrity [--json]\n")
        return 0

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    report = scan_integrity(root)
    proposals = build_self_heal_proposals(report)

    if "--json" in args:
        print(json.dumps({"report": report, "proposals": proposals},
                         ensure_ascii=False, indent=2))
        return 0
    print(render_integrity_text(report, proposals))
    # Exit non-zero when problems are found so scripts/CI can detect drift.
    return 0 if report.get("healthy") else 1


def _run_lifecycle(args: list[str]) -> int:
    """Print a metadata-only memory lifecycle / decay proposal (engram lifecycle).

    Read-only and proposal-only: it scores active lessons + decisions by
    freshness/access/tier/quality metadata and reports archive/prune *candidates*
    with reasons. It NEVER archives, prunes, or deletes — acting on a proposal is
    a separate, explicit, owner-confirmed step. See
    docs/runbooks/memory-lifecycle.md.
    """
    import os as _os
    from piia_engram.core import Engram
    from piia_engram.lifecycle import build_lifecycle_proposal, render_lifecycle_text

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram lifecycle [--json]                      Metadata-only decay/archive proposal\n"
            "  engram lifecycle apply [--id ID ...] [--commit] [--yes] [--json]\n"
            "                                                 Owner-confirmed soft archive of candidates\n"
            "                                                 (default = dry-run preview; --commit --yes to apply)\n"
            "  engram lifecycle restore <id> [--yes] [--json] Undo a lifecycle soft archive\n"
        )
        return 0

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")

    if args and args[0] == "apply":
        return _run_lifecycle_apply(Engram(root=root), args[1:])
    if args and args[0] == "restore":
        return _run_lifecycle_restore(Engram(root=root), args[1:])

    eng = Engram(root=root)
    lessons = eng.get_lessons(limit=None, _update_access=False) or []
    decisions = eng.get_decisions(limit=None, _update_access=False) or []
    report = build_lifecycle_proposal(list(lessons) + list(decisions))

    if "--json" in args:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(render_lifecycle_text(report))
    return 0


def _run_lifecycle_apply(eng, args: list[str]) -> int:
    """Owner-confirmed lifecycle archive apply (dry-run by default).

    ``--commit`` opts out of the safe dry-run preview; an actual mutation also
    requires ``--yes``. Without ``--yes`` a ``--commit`` invocation fails closed
    (reports ``requires_confirmation`` and changes nothing). ``--id`` (repeatable)
    narrows the action to a specific candidate subset.
    """
    from piia_engram.lifecycle_apply import (
        apply_lifecycle_archive,
        render_lifecycle_apply_text,
    )

    json_output = "--json" in args
    confirm = "--yes" in args
    commit = "--commit" in args
    ids: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"--json", "--yes", "--commit"}:
            i += 1
            continue
        if arg == "--id":
            if i + 1 >= len(args):
                print("Missing value for --id")
                return 2
            ids.append(args[i + 1])
            i += 2
            continue
        print(f"Unknown lifecycle apply option: {arg}")
        return 2

    payload = apply_lifecycle_archive(
        eng,
        ids=ids or None,
        confirm=confirm,
        dry_run=not commit,
    )
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_lifecycle_apply_text(payload))
    # Fail-closed (confirmation required) is a non-zero exit so scripts notice.
    return 1 if payload.get("requires_confirmation") else 0


def _run_lifecycle_restore(eng, args: list[str]) -> int:
    """Undo a lifecycle soft archive (owner-confirmed)."""
    json_output = "--json" in args
    confirm = "--yes" in args
    item_id = ""
    for arg in args:
        if arg in {"--json", "--yes"}:
            continue
        if arg.startswith("--"):
            print(f"Unknown lifecycle restore option: {arg}")
            return 2
        if not item_id:
            item_id = arg
    if not item_id:
        print("Usage: engram lifecycle restore <id> [--yes] [--json]")
        return 2

    if not confirm:
        payload = {
            "schema": 1,
            "action": "lifecycle_restore",
            "id": item_id,
            "requires_confirmation": True,
            "changed": False,
            "status": "confirmation_required",
        }
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"Lifecycle restore for {item_id} requires confirmation - "
                "re-run with --yes to apply."
            )
        return 1

    result = eng.restore_lifecycle_archive(item_id)
    payload = {
        "schema": 1,
        "action": "lifecycle_restore",
        "id": item_id,
        "requires_confirmation": False,
        "changed": bool(result.get("changed")),
        "status": "restored" if result.get("changed") else (
            "not_found" if result.get("error") else "noop"
        ),
        "from_tier": result.get("from_tier", ""),
        "to_tier": result.get("to_tier", ""),
        "error": result.get("error"),
    }
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"Lifecycle restore {item_id}: {payload['status']} "
            f"({payload['from_tier'] or 'none'} -> {payload['to_tier'] or 'none'})"
        )
    return 0 if result.get("error") is None else 1


def _run_merge(args: list[str]) -> int:
    """Near-duplicate merge proposal + owner-confirmed apply (engram merge).

    ``engram merge`` (no subcommand) prints the metadata-only merge preview
    (read-only). ``engram merge apply`` previews/applies the same plan via the
    reversible soft-archive ``merge_knowledge`` primitive: dry-run by default,
    ``--commit --yes`` to actually fold each secondary into its primary. Never
    hard-deletes and exposes no agent-facing apply tool.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram merge [--threshold T] [--limit N] [--json]\n"
            "                                       Metadata-only near-duplicate suggestions\n"
            "  engram merge apply [--pair PRIMARY:SECONDARY ...] [--threshold T]\n"
            "                     [--limit N] [--commit] [--yes] [--json]\n"
            "                                       Owner-confirmed soft-archive merge\n"
            "                                       (default = dry-run preview; --commit --yes to apply)\n"
        )
        return 0

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)

    if args and args[0] == "apply":
        return _run_merge_apply(eng, args[1:])

    from piia_engram.merge_apply import apply_merge, render_merge_apply_text

    threshold, limit, _ = _parse_merge_opts(args)
    payload = apply_merge(eng, threshold=threshold, limit=limit, dry_run=True)
    if "--json" in args:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(render_merge_apply_text(payload))
    print("  run 'engram merge apply --commit --yes' to fold them")
    return 0


def _parse_merge_opts(args: list[str]) -> tuple[float, int, list[tuple[str, str]]]:
    """Parse shared merge options: --threshold, --limit, --pair PRIMARY:SECONDARY."""
    threshold = 0.45
    limit = 10
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--threshold" and i + 1 < len(args):
            try:
                threshold = float(args[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        if arg == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        if arg == "--pair" and i + 1 < len(args):
            raw = args[i + 1]
            if ":" in raw:
                p, s = raw.split(":", 1)
                if p and s:
                    pairs.append((p, s))
            i += 2
            continue
        i += 1
    return threshold, limit, pairs


def _run_merge_apply(eng, args: list[str]) -> int:
    """Owner-confirmed near-duplicate merge apply (dry-run by default)."""
    from piia_engram.merge_apply import apply_merge, render_merge_apply_text

    json_output = "--json" in args
    confirm = "--yes" in args
    commit = "--commit" in args
    threshold, limit, pairs = _parse_merge_opts(args)

    payload = apply_merge(
        eng,
        pairs=pairs or None,
        threshold=threshold,
        limit=limit,
        confirm=confirm,
        dry_run=not commit,
    )
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_merge_apply_text(payload))
    return 1 if payload.get("requires_confirmation") else 0


def _print_conflicts_usage() -> None:
    print(
        "Usage / 用法:\n"
        "  engram conflicts list [--json]\n"
        "      List active decision conflicts. / 列出当前 active 决策冲突。\n"
        "  engram conflicts resolve <id1> <id2> --action supersede|archive|dismiss [--keep ID]\n"
        "      [--commit] [--yes] [--note TEXT] [--json]\n"
        "      Dry-run by default; --commit --yes applies. / 默认演练；--commit --yes 才写入。\n"
    )


def _conflict_payload(eng) -> dict:
    from piia_engram.conflict_governance import sample_conflicts, split_conflicts

    all_conflicts = eng.detect_active_decision_conflicts(include_suppressed=True)
    conflicts, suppressed = split_conflicts(all_conflicts)
    return {
        "schema": 1,
        "count_unsuppressed": len(conflicts),
        "count_suppressed": len(suppressed),
        "conflicts": sample_conflicts(conflicts, limit=len(conflicts)),
        "suppressed": sample_conflicts(suppressed, limit=len(suppressed)),
    }


def _render_conflict_list_text(payload: dict) -> str:
    lines = [
        "Decision conflicts / 决策冲突",
        f"unsuppressed / 未抑制: {payload.get('count_unsuppressed', 0)}",
        f"suppressed / 已抑制: {payload.get('count_suppressed', 0)}",
    ]
    for item in payload.get("conflicts", []):
        lines.append(
            f"- {item.get('id1')} <-> {item.get('id2')} "
            f"q={item.get('q_sim')} c={item.get('c_sim')} "
            f"{item.get('q1')} / {item.get('q2')}"
        )
    if payload.get("suppressed"):
        lines.append("Suppressed / 已抑制:")
        for item in payload.get("suppressed", []):
            changed = "content changed / 内容已变化" if item.get("content_changed") else "unchanged / 未变化"
            lines.append(f"- {item.get('id1')} <-> {item.get('id2')} ({changed})")
    lines.append(
        "Resolve with `engram conflicts resolve <id1> <id2> --action ... --commit --yes`. "
        "/ 使用该命令并加 --commit --yes 关闭冲突。"
    )
    return "\n".join(lines) + "\n"


def _parse_conflict_resolve_args(args: list[str]) -> tuple[dict, str | None]:
    if len(args) < 2:
        return {}, (
            "Usage: engram conflicts resolve <id1> <id2> --action supersede|archive|dismiss "
            "/ 用法：engram conflicts resolve <id1> <id2> --action supersede|archive|dismiss"
        )
    opts = {
        "id1": args[0],
        "id2": args[1],
        "action": "",
        "keep": "",
        "note": "",
        "json": False,
        "commit": False,
        "yes": False,
    }
    i = 2
    while i < len(args):
        arg = args[i]
        if arg == "--json":
            opts["json"] = True
            i += 1
            continue
        if arg == "--commit":
            opts["commit"] = True
            i += 1
            continue
        if arg == "--yes":
            opts["yes"] = True
            i += 1
            continue
        if arg in {"--action", "--keep", "--note"}:
            if i + 1 >= len(args):
                return {}, f"Missing value for {arg} / 缺少 {arg} 的值"
            opts[arg[2:]] = args[i + 1]
            i += 2
            continue
        return {}, f"Unknown conflicts resolve option: {arg} / 未知 conflicts resolve 选项：{arg}"
    return opts, None


def _find_decision_by_id(eng, item_id: str) -> dict | None:
    for decision in eng.get_decisions(limit=None, _update_access=False):
        if str(decision.get("id")) == str(item_id):
            return decision
    return None


def _run_conflicts_resolve(eng, args: list[str]) -> tuple[int, dict]:
    from piia_engram.governance_store import ResolutionStore

    opts, error = _parse_conflict_resolve_args(args)
    if error:
        return 2, {"error": error}

    id1 = str(opts["id1"])
    id2 = str(opts["id2"])
    action = str(opts["action"])
    keep = str(opts["keep"] or "")
    if action not in {"supersede", "archive", "dismiss"}:
        return 2, {"error": "Invalid --action / 无效 --action"}
    if keep and keep not in {id1, id2}:
        return 2, {"error": "--keep must be one of id1/id2 / --keep 必须是 id1/id2 之一"}
    if action in {"supersede", "archive"} and not keep:
        return 2, {"error": "--keep is required for supersede/archive / supersede/archive 必须提供 --keep"}

    first = _find_decision_by_id(eng, id1)
    second = _find_decision_by_id(eng, id2)
    if not first or not second:
        return 1, {"error": "Decision not found or inactive / 决策不存在或非 active"}

    dry_run = not bool(opts["commit"])
    if opts["commit"] and not opts["yes"]:
        return 1, {
            "schema": 1,
            "action": action,
            "id1": id1,
            "id2": id2,
            "dry_run": False,
            "changed": False,
            "requires_confirmation": True,
            "status": "confirmation_required",
        }

    other = id2 if keep == id1 else id1
    payload = {
        "schema": 1,
        "action": action,
        "id1": id1,
        "id2": id2,
        "keep": keep,
        "other": other if action in {"supersede", "archive"} else "",
        "dry_run": dry_run,
        "changed": False,
        "requires_confirmation": False,
        "status": "preview" if dry_run else "applied",
    }
    if dry_run:
        return 0, payload

    store = ResolutionStore(eng.root)
    keep_decision = first if keep == id1 else second
    other_decision = second if keep == id1 else first
    if action == "supersede":
        relation = eng.add_relation(keep, "supersedes", other)
        archive = eng.update_decision(other, {"status": "outdated"})
        store.record(first, second, action=action, keep=keep, note=str(opts["note"] or ""))
        payload["changed"] = bool(relation.get("added") or archive.get("status") == "outdated")
    elif action == "archive":
        archive = eng.update_decision(other, {"status": "outdated"})
        store.record(first, second, action=action, keep=keep, note=str(opts["note"] or ""))
        payload["changed"] = bool(archive.get("status") == "outdated")
    else:
        record = store.dismiss(first, second, note=str(opts["note"] or ""))
        payload["changed"] = bool(record)

    eng._audit.log(
        "write",
        "knowledge/conflict_resolutions",
        detail=f"{action} {id1}::{id2} keep={keep or ''}",
    )
    payload["kept_question"] = keep_decision.get("question", "") if keep else ""
    payload["other_question"] = other_decision.get("question", "") if keep else ""
    return 0, payload


def _render_conflict_resolve_text(payload: dict) -> str:
    if payload.get("error"):
        return f"{payload['error']}\n"
    status = payload.get("status")
    changed = payload.get("changed")
    dry = payload.get("dry_run")
    return (
        f"Conflict resolution / 冲突处置: {payload.get('action')} "
        f"{payload.get('id1')} <-> {payload.get('id2')} "
        f"status={status} dry_run={str(dry).lower()} changed={str(changed).lower()}\n"
    )


def run_conflicts(argv: list[str] | None = None) -> int:
    """Read/list and owner-confirm decision-conflict resolutions."""
    from piia_engram.core import Engram

    args = list(argv or [])
    if not args or args[0] in {"-h", "--help"}:
        _print_conflicts_usage()
        return 0

    root = Path(os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)
    if args[0] == "list":
        json_output = "--json" in args
        unknown = [arg for arg in args[1:] if arg not in {"--json"}]
        if unknown:
            print(f"Unknown conflicts list option: {unknown[0]} / 未知 conflicts list 选项：{unknown[0]}")
            return 2
        payload = _conflict_payload(eng)
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_render_conflict_list_text(payload), end="")
        return 0
    if args[0] == "resolve":
        rc, payload = _run_conflicts_resolve(eng, args[1:])
        if payload.get("schema") and "--json" in args:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif "--json" in args:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_render_conflict_resolve_text(payload), end="")
        return rc

    print(f"Unknown conflicts command: {args[0]} / 未知 conflicts 命令：{args[0]}")
    _print_conflicts_usage()
    return 2


def _run_reconcile(args: list[str]) -> int:
    """Reconcile proposal + owner-confirmed import-only apply (engram reconcile).

    ``engram reconcile`` (no subcommand) scans external AI memory files and
    prints a metadata-only classification (import / duplicate / conflict / skip),
    importing nothing. ``engram reconcile apply`` imports ONLY the novel
    (``import``) candidates via the existing write API: dry-run by default,
    ``--commit --yes`` to actually import. Duplicates and conflicts are never
    applied (conflict resolution is deferred); no agent-facing tool is exposed.
    """
    import os as _os
    from piia_engram.core import Engram
    from piia_engram.reconcile_apply import (
        apply_reconcile,
        preview_reconcile_conflicts,
        render_reconcile_conflicts_text,
        render_reconcile_apply_text,
    )

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram reconcile [--json]                 Metadata-only import proposal\n"
            "  engram reconcile conflicts [--json]       Metadata-only conflict preview\n"
            "  engram reconcile apply [--commit] [--yes] [--json]\n"
            "                                            Owner-confirmed import-only apply\n"
            "                                            (default = dry-run preview; --commit --yes to import)\n"
        )
        return 0

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)
    candidates = eng.collect_memory_candidates()

    json_output = "--json" in args
    apply = bool(args) and args[0] == "apply"
    conflicts = bool(args) and args[0] == "conflicts"
    confirm = "--yes" in args
    commit = apply and "--commit" in args

    if conflicts:
        payload = preview_reconcile_conflicts(
            eng, candidates,
            source="memory_files",
        )
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_reconcile_conflicts_text(payload))
        return 0

    payload = apply_reconcile(
        eng, candidates,
        source="memory_files",
        confirm=confirm,
        dry_run=not commit,
    )
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_reconcile_apply_text(payload))
    return 1 if payload.get("requires_confirmation") else 0


def _governance_root():
    from piia_engram.core import Engram
    return Engram().root


def run_grants(root) -> int:
    """List agent trust grants + revocations (engram grants)."""
    from piia_engram.governance_store import GrantStore
    data = GrantStore(root).list_grants()
    print("Agent grants (explicit):")
    if data["grants"]:
        for a, lvl in sorted(data["grants"].items()):
            print(f"  {a}: {lvl}")
    else:
        print("  (none — agents are auto-classified by default)")
    print("Revoked:")
    if data["revoked"]:
        for a in sorted(data["revoked"]):
            print(f"  {a}")
    else:
        print("  (none)")
    return 0


def run_trust(root, agent: str, level: str) -> int:
    """Grant an agent a trust level (engram trust <agent> <level>)."""
    from piia_engram.governance_store import GrantStore
    try:
        GrantStore(root).set_grant(agent, level)
    except ValueError as exc:
        print(f"[error] {exc}")
        return 2
    print(f"[ok] {agent} → {level}")
    return 0


def run_revoke(root, agent: str) -> int:
    """Revoke an agent (engram revoke <agent>)."""
    from piia_engram.governance_store import GrantStore
    GrantStore(root).revoke(agent)
    print(f"[ok] revoked {agent}.")
    print("     Note: stops FUTURE disclosure only — cannot recall context "
          "already sent to an AI tool.")
    return 0


def run_audit(root, limit: int = 20) -> int:
    """Show recent disclosure receipts + ledger integrity (engram audit)."""
    from piia_engram.governance import GovernanceLedger, default_ledger_path
    led = GovernanceLedger(default_ledger_path(root))
    # Codex round-5 P2: verify() FIRST. records() does an unguarded json.loads
    # per line, so on a corrupt ledger it would raise and traceback. verify()
    # reports the break gracefully, so we bail before ever touching records().
    ok, msg = led.verify()
    if not ok:
        print(f"ledger integrity: BROKEN — {msg}")
        return 1
    recs = led.records()
    if not recs:
        print("(no disclosures recorded yet)")
        return 0
    for r in recs[-limit:]:
        ev = r.get("event", {})
        print(f"  #{r.get('seq')} {r.get('ts')}  {ev.get('agent_id', '?')} "
              f"[{ev.get('trust_level', '?')}] returned={ev.get('returned_count', '?')} "
              f"excluded_sensitivity={ev.get('excluded_by_sensitivity', '?')}")
    print("ledger integrity: OK")
    return 0


def run_verify_ledger(root) -> int:
    """Verify the governance ledger hash chain (engram verify-ledger)."""
    from piia_engram.governance import GovernanceLedger, default_ledger_path
    ok, msg = GovernanceLedger(default_ledger_path(root)).verify()
    print(f"[{'ok' if ok else 'FAIL'}] governance ledger: {msg}")
    return 0 if ok else 1


def _print_status_usage() -> None:
    print(
        "Usage:\n"
        "  engram status [--no-probe]\n"
        "  engram status --html [--output PATH] [--no-probe]\n"
    )


def run_status(argv: list[str] | None = None) -> int:
    """Print a redacted first-run health summary."""
    from piia_engram.status_report import build_status, render_status_text, write_status_html

    args = list(argv or [])
    html_output = False
    no_probe = False
    output: Path | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            _print_status_usage()
            return 0
        if arg == "--html":
            html_output = True
        elif arg == "--no-probe":
            no_probe = True
        elif arg == "--output":
            if i + 1 >= len(args):
                print("Missing value for --output")
                _print_status_usage()
                return 2
            output = Path(args[i + 1]).expanduser()
            i += 1
        else:
            print(f"Unknown status option: {arg}")
            _print_status_usage()
            return 2
        i += 1

    if output is not None and not html_output:
        print("--output only applies with --html")
        _print_status_usage()
        return 2
    status = build_status(probe=not no_probe)
    if html_output:
        path = write_status_html(status, output)
        print(f"Engram status HTML written to: {path}")
    else:
        print(render_status_text(status), end="")
    return 0


def _print_preview_usage() -> None:
    print(
        "Usage:\n"
        "  engram preview [--level quick|standard|full] [--as ROLE]\n"
        "                 [--project NAME] [--query TEXT] [--read-only] [--json]\n"
        "  engram preview --html [--output PATH] [...same options]\n"
        "  --read-only: skip session/audit/structure writes (zero-write to store)\n"
        "\n"
        "Roles: owner | assistant | reviewer | automation\n"
        "Shows exactly what a simulated AI caller would receive (exposed vs\n"
        "withheld, redaction + budget effects). Read-only; nothing is sent.\n"
    )


def run_preview(argv: list[str] | None = None) -> int:
    """Show what a simulated AI caller would receive (engram preview).

    Local + owner-run (CLI = ``private-self``): composes the same governed
    paths the real injection uses (``resolve_effective_profile`` for the
    caller ceiling, ``gather_recall`` for the payload, ``build_safe_context``
    for redaction + budget) and renders an owner-facing exposed/withheld
    report. Read-only by construction — it adds no new agent-facing surface
    and never widens what governance already allows.
    """
    from piia_engram.context_preview import (
        DEFAULT_LEVEL,
        DEFAULT_ROLE,
        build_context_preview,
        render_context_preview_text,
        write_context_preview_html,
    )
    from piia_engram.core import Engram

    args = list(argv or [])
    level = DEFAULT_LEVEL
    role = DEFAULT_ROLE
    project = ""
    query = ""
    json_output = False
    html_output = False
    read_only = False
    output: Path | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            _print_preview_usage()
            return 0
        if arg == "--json":
            json_output = True
        elif arg == "--html":
            html_output = True
        elif arg == "--read-only":
            read_only = True
        elif arg in {"--level", "--as", "--project", "--query", "--output"}:
            if i + 1 >= len(args):
                print(f"Missing value for {arg}")
                _print_preview_usage()
                return 2
            value = args[i + 1]
            if arg == "--level":
                level = value
            elif arg == "--as":
                role = value
            elif arg == "--project":
                project = value
            elif arg == "--query":
                query = value
            else:
                output = Path(value).expanduser()
            i += 1
        else:
            print(f"Unknown preview option: {arg}")
            _print_preview_usage()
            return 2
        i += 1

    if output is not None and not html_output:
        print("--output only applies with --html")
        _print_preview_usage()
        return 2
    if json_output and html_output:
        print("--json and --html are mutually exclusive")
        _print_preview_usage()
        return 2

    root = Path(os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root, read_only=read_only)
    try:
        preview = build_context_preview(
            eng,
            level=level,
            role=role,
            project_folder=project,
            query=query,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        _print_preview_usage()
        return 2

    if json_output:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
    elif html_output:
        path = write_context_preview_html(preview, root, output)
        print(f"Engram context preview HTML written to: {path}")
    else:
        print(render_context_preview_text(preview), end="")
    return 0


def _print_continuity_usage() -> None:
    print(
        "Usage:\n"
        "  engram continuity [--project PATH] [--limit N]\n"
        "  engram continuity --json [--project PATH] [--limit N]\n"
    )


def run_continuity(argv: list[str] | None = None) -> int:
    """Print a metadata-only cross-tool continuity proof."""
    from piia_engram.continuity_report import (
        build_continuity_report,
        render_continuity_text,
    )
    from piia_engram.core import Engram

    args = list(argv or [])
    project_folder = os.getcwd()
    limit = 500
    json_output = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            _print_continuity_usage()
            return 0
        if arg == "--json":
            json_output = True
        elif arg == "--project":
            if i + 1 >= len(args):
                print("Missing value for --project")
                _print_continuity_usage()
                return 2
            project_folder = args[i + 1]
            i += 1
        elif arg == "--limit":
            if i + 1 >= len(args):
                print("Missing value for --limit")
                _print_continuity_usage()
                return 2
            try:
                limit = int(args[i + 1])
            except ValueError:
                print("--limit must be an integer")
                return 2
            i += 1
        else:
            print(f"Unknown continuity option: {arg}")
            _print_continuity_usage()
            return 2
        i += 1

    report = build_continuity_report(
        Engram(),
        project_folder=project_folder,
        session_limit=limit,
    )
    if json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_continuity_text(report), end="")
    return 0


def _print_management_usage() -> None:
    print(
        "Usage:\n"
        "  engram management [--project PATH] [--review-limit N] [--playbook-limit N]\n"
        "                    [--review-kind all|lesson|decision] [--quality all|low|ok|missing]\n"
        "                    [--playbook-state all|active|archived|deleted|staging] [--scope all|global|project|shared]\n"
        "  engram management --json [same options]\n"
        "  engram management action review approve|archive <id> [--yes] [--json]\n"
        "  engram management action playbook archive|delete|restore <id> [--yes] [--json]\n"
        "  engram management action playbook_scope accept_global|accept_project|accept_shared|skip <id> [--project PATH] [--yes] [--json]\n"
    )


def _run_management_action_cli(args: list[str]) -> int:
    from piia_engram.core import Engram
    from piia_engram.management_actions import (
        render_management_action_text,
        run_management_action,
    )

    if len(args) < 4:
        _print_management_usage()
        return 2
    target, action, item_id = args[1], args[2], args[3]
    tail = args[4:]
    json_output = "--json" in tail
    confirm = "--yes" in tail
    project_folder = ""
    project_folders: list[str] = []
    reason = ""
    i = 0
    while i < len(tail):
        arg = tail[i]
        if arg in {"--json", "--yes"}:
            i += 1
            continue
        if arg == "--reason":
            if i + 1 >= len(tail):
                print("Missing value for --reason")
                _print_management_usage()
                return 2
            reason = tail[i + 1]
            i += 2
            continue
        if arg == "--project":
            if i + 1 >= len(tail):
                print("Missing value for --project")
                _print_management_usage()
                return 2
            project_folder = tail[i + 1]
            project_folders.append(project_folder)
            i += 2
            continue
        print(f"Unknown management action option: {arg}")
        _print_management_usage()
        return 2

    result = run_management_action(
        Engram(),
        target=target,
        action=action,
        item_id=item_id,
        confirm=confirm,
        project_folder=project_folder,
        project_folders=project_folders,
        reason=reason,
    )
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_management_action_text(result), end="")
    return 0 if result.get("error") is None else 1


def run_management(argv: list[str] | None = None) -> int:
    """Print a metadata-only management projection for GUI consumers."""
    from piia_engram.core import Engram
    from piia_engram.management_view import (
        build_management_view,
        render_management_text,
    )

    args = list(argv or [])
    if args and args[0] == "action":
        return _run_management_action_cli(args)
    project_folder = os.getcwd()
    review_limit = 50
    playbook_limit = 50
    json_output = False
    review_kind = "all"
    quality_status = "all"
    playbook_state = "all"
    scope_type = "all"
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            _print_management_usage()
            return 0
        if arg == "--json":
            json_output = True
        elif arg == "--project":
            if i + 1 >= len(args):
                print("Missing value for --project")
                _print_management_usage()
                return 2
            project_folder = args[i + 1]
            i += 1
        elif arg == "--review-limit":
            if i + 1 >= len(args):
                print("Missing value for --review-limit")
                _print_management_usage()
                return 2
            try:
                review_limit = int(args[i + 1])
            except ValueError:
                print("--review-limit must be an integer")
                return 2
            i += 1
        elif arg == "--playbook-limit":
            if i + 1 >= len(args):
                print("Missing value for --playbook-limit")
                _print_management_usage()
                return 2
            try:
                playbook_limit = int(args[i + 1])
            except ValueError:
                print("--playbook-limit must be an integer")
                return 2
            i += 1
        elif arg == "--review-kind":
            if i + 1 >= len(args):
                print("Missing value for --review-kind")
                _print_management_usage()
                return 2
            review_kind = args[i + 1]
            i += 1
        elif arg == "--quality":
            if i + 1 >= len(args):
                print("Missing value for --quality")
                _print_management_usage()
                return 2
            quality_status = args[i + 1]
            i += 1
        elif arg == "--playbook-state":
            if i + 1 >= len(args):
                print("Missing value for --playbook-state")
                _print_management_usage()
                return 2
            playbook_state = args[i + 1]
            i += 1
        elif arg == "--scope":
            if i + 1 >= len(args):
                print("Missing value for --scope")
                _print_management_usage()
                return 2
            scope_type = args[i + 1]
            i += 1
        else:
            print(f"Unknown management option: {arg}")
            _print_management_usage()
            return 2
        i += 1

    view = build_management_view(
        Engram(),
        project_folder=project_folder,
        review_limit=review_limit,
        playbook_limit=playbook_limit,
        review_kind=review_kind,
        quality_status=quality_status,
        playbook_state=playbook_state,
        scope_type=scope_type,
    )
    if json_output:
        print(json.dumps(view, ensure_ascii=False, indent=2))
    else:
        print(render_management_text(view), end="")
    return 0
