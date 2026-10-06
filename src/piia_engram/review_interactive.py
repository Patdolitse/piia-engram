"""``engram review interactive``: decide pending proposals one at a time in a terminal.

The Owner sees one pending proposal per screen (kind, id, time, scope, text,
risk, where it came from, a possible duplicate with its diff, and the
supersede chain) and types one letter plus Enter:

    a  approve        r  reject (asks for an optional reason, kept in the receipt only)
    s  supersede      (asks for the id of the approved entry it replaces)
    k  skip           v  show the full text        q  stop and go to the summary

Nothing is written while reviewing: the store is opened read-only. At the end
the decisions are listed and applied only after ``y``. ``n`` (or Enter), end of
input and Ctrl+C discard every decision and write nothing.

Each decision becomes a mark (``approve``, ``reject``, ``supersede:<id>``, with
the version the Owner saw) and runs through ``review_cli.apply_marks``, the
function behind ``engram review apply --yes``: same checks, same tombstones,
same receipt and audit event. An item that changed after it was shown is
skipped (``version_conflict``) and listed in the summary.

Only a person at a terminal gets the prompt: when stdin or stdout is not a
terminal the command refuses and points at ``engram review export`` /
``engram review apply``. Nothing here is reachable over MCP.

Stored text came from an AI. Every line printed here passes through
``_screen_line``, which turns control and format characters (escape sequences,
carriage returns, bidi overrides, zero-width characters) into spaces, so stored
text cannot clear the screen, move the cursor or forge a line of this interface.
"""

from __future__ import annotations

import getpass
import json
import sys
import unicodedata
from typing import Any, TextIO

from . import dedup_review as _dedup_review
from . import review_cli as _review_cli
from . import tombstones as _tombstones
from . import write_provenance as _write_provenance
from .i18n import t

FOLD = 300  # characters of a long field shown before "v" is needed

USAGE = (
    "Usage: engram review interactive [--operator <name>]   (alias: engram review -i)\n"
    "  Review pending proposals one at a time in a terminal:\n"
    "  a approve, r reject (optional reason), s supersede an approved entry,\n"
    "  k skip, v full text, q stop. Nothing is written until you confirm the\n"
    "  summary with y; n, end of input or Ctrl+C write nothing.\n"
    "  Applies through the same path as `engram review apply` (same receipt).\n"
    "  Needs a terminal; otherwise use `engram review export` and `engram review apply`."
)

_PROBLEMS_ZH = {
    "self": "条目不能取代自己",
    "not_found": "该提案已不在存储中",
    "target_not_found": "没有这个 id 的条目",
    "type_mismatch": "该条目的种类或类型不同",
    "target_not_trusted": "该条目不是已批准（可信）的条目",
    "scope_mismatch": "该条目属于另一个项目作用域",
    "cycle": "该条目已取代本条，会形成循环",
    "invalid_id": "不是有效的 id",
    "already_chosen": "本次审核中已有另一条选择取代它",
}
_PROBLEMS_EN = {
    **_review_cli.SUPERSEDE_PROBLEMS,
    "invalid_id": "not a valid id",
    "already_chosen": "another item in this review already supersedes it",
}


# ---------------------------------------------------------------------------
# terminal output: one sanitizer for every line
# ---------------------------------------------------------------------------


def _screen_line(text: Any) -> str:
    """One display line: every control or format character (ESC, CR, BEL, bidi
    overrides, zero-width characters, ...) becomes a space."""
    return "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in str(text)).rstrip()


def _one_line(value: Any, limit: int = FOLD) -> tuple[str, bool]:
    """(value on one cleaned line, folded?)"""
    text = _write_provenance.clean_client_text(value, limit=10**9)
    if len(text) <= limit:
        return text, False
    return text[: limit - 3].rstrip() + "...", True


class _Terminal:
    def __init__(self, stdin: TextIO, stdout: TextIO):
        self.inp = stdin
        self.out = stdout

    def say(self, text: Any = "") -> None:
        for line in str(text).split("\n"):
            self.out.write(_screen_line(line) + "\n")
        self.out.flush()

    def ask(self, prompt: str) -> str:
        self.out.write(_screen_line(prompt) + " ")
        self.out.flush()
        line = self.inp.readline()
        if line == "":
            raise EOFError
        return line.rstrip("\r\n")


def _is_terminal(stream: Any) -> bool:
    try:
        return bool(stream is not None and stream.isatty())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# what one item looks like
# ---------------------------------------------------------------------------


def _claim_fields(kind: str, row: dict) -> list[tuple[str, Any]]:
    if kind == "decision":
        return [("question", row.get("question") or row.get("title")), ("choice", row.get("choice")),
                ("reasoning", row.get("reasoning"))]
    if kind == "playbook":
        fields: list[tuple[str, Any]] = [("title", row.get("title")), ("description", row.get("description"))]
        for i, step in enumerate(row.get("steps") or [], 1):
            fields.append((f"step {i}", step.get("action", "") if isinstance(step, dict) else step))
        return fields
    return [("summary", row.get("summary")), ("detail", row.get("detail"))]


def _chain_lines(row: dict, edges: list[dict]) -> list[str]:
    item_id = str(row.get("id") or "")
    lines = []
    pending = _dedup_review.safe_id(row.get("pending_supersedes"))
    if pending:
        lines.append(t(f"  提议取代：{pending}（批准后生效）", f"  proposes to supersede: {pending} (on approval)"))
    olds = sorted({_dedup_review.safe_id(e.get("dst")) for e in edges
                   if e.get("rel") == "supersedes" and e.get("src") == item_id} - {""})
    news = sorted({_dedup_review.safe_id(e.get("src")) for e in edges
                   if e.get("rel") == "supersedes" and e.get("dst") == item_id} - {""})
    if olds:
        lines.append(t("  已取代：", "  supersedes: ") + ", ".join(olds))
    if news:
        lines.append(t("  被取代于：", "  superseded by: ") + ", ".join(news))
    if lines:
        lines.insert(0, t("取代链：", "supersede chain:"))
    return lines


def card(n: int, total: int, kind: str, row: dict, *, eng, lookup: dict[str, dict],
         edges: list[dict], full: bool = False) -> tuple[list[str], bool]:
    """The screen for one pending item; returns (lines, something was folded)."""
    item_id = _dedup_review.safe_id(row.get("id")) or "?"
    mem_type = _review_cli._type_label(row)
    lines = [
        "",
        f"=== [{n}/{total}] {kind} {item_id} ===",
        t("类型：", "type:    ") + (_one_line(mem_type, 40)[0] or t("缺失", "MISSING")),
        t("作用域：", "scope:   ") + _one_line(_review_cli.scope_label(eng, kind, row), 120)[0],
        t("创建：", "created: ") + _one_line(row.get("created_at") or row.get("timestamp") or "?", 40)[0],
    ]
    risk = _one_line(row.get("risk_level") or "unknown", 20)[0]
    flags = row.get("risk_flags") if isinstance(row.get("risk_flags"), list) else []
    flag_text = ", ".join(_one_line(f, 40)[0] for f in flags[:8])
    lines.append(t("风险：", "risk:    ") + risk + (t(f"（原因：{flag_text}）", f" (reasons: {flag_text})")
                                                  if flag_text else ""))
    lines.append(t("来源：", "source:  ") + _write_provenance.client_card_line(row)[2:])
    source_tool = _one_line(row.get("source_tool") or "unknown", 60)[0]
    queued = _one_line(row.get("queued_at") or row.get("timestamp") or "?", 40)[0]
    lines.append(t(f"  写入工具：{source_tool}，入队 {queued}", f"  via {source_tool}, queued {queued}"))
    folded = False
    for name, value in _claim_fields(kind, row):
        if value in (None, ""):
            continue
        if full:
            body = [_screen_line(part) for part in str(value).replace("\r\n", "\n").split("\n")]
            lines.append(f"{name}:")
            lines.extend("  " + part for part in body)
        else:
            text, cut = _one_line(value)
            folded = folded or cut
            lines.append(f"{name}: {text}")
    notes = []
    if row.get("reproposal_of_rejected"):
        notes.append(t("曾被拒绝条目的重提：", "re-proposal of rejected ")
                     + (_dedup_review.safe_id(row.get("reproposal_of_rejected")) or "?"))
    near = _tombstones.near(eng.root, kind, row)
    if near is not None:
        notes.append(t("与已拒绝条目相近：", "near a rejected entry: ") + (_dedup_review.safe_id(near.get("id")) or "?"))
    if not mem_type:
        notes.append(t("缺少类型标签", "missing type label"))
    if notes:
        lines.append(t("提示：", "flags: ") + "; ".join(notes))
    lines.extend(_dedup_review.card_lines(kind, row, lookup.get(kind) or {}))
    lines.extend(_chain_lines(row, edges))
    if folded:
        lines.append(t("（长内容已折叠，输入 v 查看全文）", "(long text folded; v shows the full text)"))
    return lines, folded


# ---------------------------------------------------------------------------
# the review loop
# ---------------------------------------------------------------------------


def _engram(read_only: bool):
    from .core import Engram

    return Engram(read_only=read_only)


def _pending(eng) -> tuple[list[tuple[str, dict]], dict[str, dict[str, dict]]]:
    lookup: dict[str, dict[str, dict]] = {}
    rows = sorted(_review_cli._pending(eng, lookup), key=_review_cli._sort_key)
    return rows, lookup


def pending_order(eng) -> list[str]:
    """Ids of the pending items in the order the review shows them (same as export)."""
    return [str(row.get("id")) for _kind, row in _pending(eng)[0]]


def _edges(root) -> list[dict]:
    try:
        from .governance_store import RelationStore

        return RelationStore(root).all_edges()
    except Exception:  # a damaged relation file only hides the chain lines
        return []


def _problem_text(code: str) -> str:
    return t(_PROBLEMS_ZH.get(code, code), _PROBLEMS_EN.get(code, code))


def _ask_target(term: _Terminal, eng, item_id: str, taken: set[str]) -> str:
    """The id of the approved entry this item supersedes, or '' when cancelled."""
    while True:
        answer = term.ask(t("要取代的已批准条目 id（回车取消）：", "Id of the approved entry it replaces (Enter cancels):"))
        target = answer.strip()
        if not target:
            return ""
        if not _dedup_review.safe_id(target):
            code = "invalid_id"
        elif target in taken:
            code = "already_chosen"
        else:
            code = _review_cli.supersede_problem(eng, item_id, target)
        if not code:
            return target
        term.say(t("已拒绝：", "Refused: ") + _problem_text(code) + t("。请重新输入。", ". Try again."))


def _ask_reason(term: _Terminal) -> str:
    limit = _review_cli.REASON_MAX
    answer = term.ask(t(f"拒绝理由（可选，最多 {limit} 字，只记在本次回执里，回车跳过）：",
                        f"Reason (optional, up to {limit} characters, kept in this run's receipt only; "
                        "Enter to skip):"))
    reason = _review_cli.clean_reason(answer)
    if len(_write_provenance.clean_client_text(answer, limit=10**9)) > len(reason):
        term.say(t(f"（理由已截断为 {limit} 字）", f"(reason trimmed to {limit} characters)"))
    return reason


_PROMPT_ZH = "[a]批准 [r]拒绝 [s]取代 [k]跳过 [v]全文 [q]结束 >"
_PROMPT_EN = "[a]pprove [r]eject [s]upersede [k] skip [v]iew full [q]uit >"


def _decide(term: _Terminal, eng, n: int, total: int, kind: str, row: dict, *, lookup, edges,
            taken: set[str]) -> dict | None | str:
    """One item: a decision dict, None for skip, or "quit"."""
    lines, _folded = card(n, total, kind, row, eng=eng, lookup=lookup, edges=edges)
    term.say("\n".join(lines))
    item_id = str(row.get("id"))
    version = int(row.get("version") or 1)
    while True:
        key = term.ask(t(_PROMPT_ZH, _PROMPT_EN)).strip().lower()
        if key == "a":
            return {"id": item_id, "mark": "approve", "expected_version": version, "kind": kind}
        if key == "r":
            decision = {"id": item_id, "mark": "reject", "expected_version": version, "kind": kind}
            reason = _ask_reason(term)
            if reason:
                decision["reason"] = reason
            return decision
        if key == "s":
            target = _ask_target(term, eng, item_id, taken)
            if target:
                taken.add(target)
                return {"id": item_id, "mark": f"{_review_cli.SUPERSEDE_PREFIX}{target}",
                        "expected_version": version, "kind": kind, "target": target}
            continue
        if key == "k":
            return None
        if key == "q":
            return "quit"
        if key == "v":
            full, _ = card(n, total, kind, row, eng=eng, lookup=lookup, edges=edges, full=True)
            term.say("\n".join(full))
            continue
        term.say(t("请输入 a / r / s / k / v / q 之一。", "Type one of a / r / s / k / v / q."))


def _plan_lines(decisions: list[dict]) -> list[str]:
    lines = [t("本次决定：", "Your decisions:")]
    for d in decisions:
        if d["mark"] == "approve":
            lines.append(t(f"  批准  {d['kind']} {d['id']}", f"  approve    {d['kind']} {d['id']}"))
        elif d["mark"] == "reject":
            note = f"  ({d['reason']})" if d.get("reason") else ""
            lines.append(t(f"  拒绝  {d['kind']} {d['id']}{note}", f"  reject     {d['kind']} {d['id']}{note}"))
        else:
            lines.append(t(f"  取代  {d['kind']} {d['id']} -> 旧条目 {d['target']}",
                           f"  supersede  {d['kind']} {d['id']} -> replaces {d['target']}"))
    return lines


def _outcome_lines(payload: dict, skipped: int, eng) -> list[str]:
    done = {"approve": 0, "reject": 0, "supersede": 0}
    failed = []
    for item in payload.get("items", []):
        if item.get("status") == "applied":
            done[item.get("action", "approve")] = done.get(item.get("action", "approve"), 0) + 1
        else:
            failed.append(item)
    lines = [t(
        f"汇总：批准 {done['approve']}，拒绝 {done['reject']}，取代 {done['supersede']}，"
        f"跳过 {skipped}，失败 {len(failed)}",
        f"Summary: approved {done['approve']}, rejected {done['reject']}, superseded {done['supersede']}, "
        f"skipped {skipped}, failed {len(failed)}",
    )]
    for item in failed:
        lines.append(f"  {item.get('action')} {item.get('id')}: {item.get('status')}")
    audit = getattr(eng, "_audit", None)
    if audit is not None and getattr(audit, "enabled", False) and getattr(audit, "log_path", None):
        lines.append(t("回执（审计记录 review/apply）：", "Receipt (audit event review/apply): ") + str(audit.log_path))
    else:
        lines.append(t("审计日志已关闭（ENGRAM_AUDIT=0），未写回执。",
                       "Audit logging is off (ENGRAM_AUDIT=0); no receipt was written."))
    return lines


def _default_operator() -> str:
    try:
        name = getpass.getuser()
    except Exception:
        name = ""
    return _write_provenance.clean_client_text(name, 64) or "owner"


_NOTHING_WRITTEN = ("未写入任何内容。", "Nothing was written.")


def run(args: list[str], *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    args = list(args or [])
    if args and args[0] in ("-h", "--help"):
        stdout.write(USAGE + "\n")
        return 0
    operator = _review_cli._option(args, "--operator").strip()
    extra = [a for a in args if a not in ("--operator", operator)]
    if extra or ("--operator" in args and not operator):
        stdout.write(USAGE + "\n")
        return 2
    if not (_is_terminal(stdin) and _is_terminal(stdout)):
        stdout.write(
            "engram review interactive needs a terminal (stdin and stdout). Nothing was written.\n"
            "Without one, review with files instead:\n"
            "  engram review export --out <dir>     (cards to read, ids to mark)\n"
            "  engram review apply <marks.json>     (dry run; add --operator <name> --yes to apply)\n"
            "交互审核需要在终端中运行；未写入任何内容。请改用 engram review export 和 engram review apply。\n"
        )
        stdout.flush()
        return 2
    operator = _write_provenance.clean_client_text(operator, 64) or _default_operator()

    term = _Terminal(stdin, stdout)
    reader = _engram(read_only=True)
    rows, lookup = _pending(reader)
    if not rows:
        term.say(t("没有待审提案。", "No proposals are waiting for review."))
        return 0
    edges = _edges(reader.root)
    term.say(t(f"待审提案 {len(rows)} 条。审核期间不写入；结束时确认后才生效。",
               f"{len(rows)} proposal(s) waiting. Nothing is written until you confirm at the end."))
    decisions: list[dict] = []
    taken: set[str] = set()
    try:
        for n, (kind, row) in enumerate(rows, 1):
            outcome = _decide(term, reader, n, len(rows), kind, row, lookup=lookup, edges=edges, taken=taken)
            if outcome == "quit":
                break
            if isinstance(outcome, dict):
                decisions.append(outcome)
        skipped = len(rows) - len(decisions)
        if not decisions:
            term.say(t("没有要应用的决定。", "No decisions to apply.") + " " + t(*_NOTHING_WRITTEN))
            return 0
        term.say("\n".join(["", *_plan_lines(decisions)]))
        answer = term.ask(t(f"应用这 {len(decisions)} 个决定？[y/N]", f"Apply these {len(decisions)} decision(s)? [y/N]"))
        if answer.strip().lower() not in ("y", "yes"):
            term.say(t("已放弃本次决定。", "Decisions discarded.") + " " + t(*_NOTHING_WRITTEN))
            return 0
    except EOFError:
        term.say("")
        term.say(t("输入结束，已放弃本次审核。", "Input ended; review abandoned.") + " " + t(*_NOTHING_WRITTEN))
        return 1
    except KeyboardInterrupt:
        term.say("")
        term.say(t("已中断，已放弃本次审核。", "Interrupted; review abandoned.") + " " + t(*_NOTHING_WRITTEN))
        return 130

    raw = [{key: d[key] for key in ("id", "mark", "expected_version", "reason") if key in d} for d in decisions]
    marks, error = _review_cli.validate_marks(raw)
    if error:  # cannot happen for marks built above; never apply a half-valid list
        term.say(error + " " + t(*_NOTHING_WRITTEN))
        return 2
    writer = _engram(read_only=False)
    attribution = _review_cli.attribution_record(operator, mode="interactive", isatty=True)
    try:
        payload = _review_cli.apply_marks(writer, marks, attribution)
    except KeyboardInterrupt:
        # apply_marks already wrote a receipt counting what was applied before the stop.
        term.say("")
        term.say(t("应用途中被中断；回执记录了已应用的部分（审计记录 review/apply）。",
                   "Interrupted while applying; the receipt (audit event review/apply) counts what was applied."))
        return 130
    term.say(json.dumps(payload, ensure_ascii=False, indent=2))
    term.say("\n".join(_outcome_lines(payload, skipped, writer)))
    return 0
