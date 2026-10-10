"""Local owner-only review window, independent of MCP and browser sessions.

Run the installed GUI entry point (double-click on Windows). Only a button
callback applies a displayed snapshot through the existing review engine.
Like the local CLI, this is not isolation from programs running as the same OS
user. It does not authenticate an agent's claim to be a human.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import review_boundary, review_cli, review_interactive
from .core import Engram
from .storage import hold_directory_lock


@dataclass(frozen=True)
class ReviewCard:
    kind: str
    item_id: str
    version: int
    fingerprint: str
    title: str
    text: str


def _safe_text(value: Any) -> str:
    # Newlines are layout, every other control/format character is inert text.
    return "\n".join(review_interactive._screen_line(line)
                     for line in str(value).replace("\r\n", "\n").split("\n"))


def _card(eng: Engram, kind: str, row: dict) -> ReviewCard:
    target_id = str(row.get("pending_supersedes") or "")
    target_kind, target = eng._find_item_by_id(target_id) if target_id else ("", None)
    document = {"kind": kind, "row": row, "target_kind": target_kind, "target": target}
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False,
                                      allow_nan=False).encode("utf-8")).hexdigest()
    lines, _ = review_interactive.card(1, 1, kind, row, eng=eng, lookup={}, edges=[], full=True)
    lines.insert(1, f"version: {review_cli._row_version(row)}")
    if target_id:
        lines += ["", "取代影响 / Replacement: the following entry becomes inactive."]
        if target is not None:
            old_lines, _ = review_interactive.card(1, 1, target_kind, target, eng=eng,
                                                   lookup={}, edges=[], full=True)
            lines += old_lines
            lines += ["完整旧条目 / Complete previous entry", json.dumps(target, ensure_ascii=False, indent=2)]
        else:
            lines.append("取代目标不存在 / Replacement target missing")
    # Preserve the complete proposal, not only a terminal card's selected fields:
    # this includes evidence/review dates, project scope and structured steps.
    lines += ["", "完整提案 / Complete proposal", json.dumps(row, ensure_ascii=False, indent=2)]
    title = row.get("summary") or row.get("question") or row.get("title") or row.get("field") or kind
    return ReviewCard(kind, str(row["id"]), review_cli._row_version(row), digest,
                      _safe_text(title).replace("\n", " "), _safe_text("\n".join(lines)))


class ReviewController:
    """Read a selected store; decide only the exact content the local UI showed.

    No methods are registered as MCP tools. Caller attribution is fixed, not
    supplied by the proposal, the environment, or an agent confirmation flag.
    """
    def __init__(self, root: Path):
        self.root = Path(root)

    def pending(self) -> list[ReviewCard]:
        eng = Engram(root=self.root, read_only=True)
        rows = sorted(review_cli._pending(eng), key=review_cli._sort_key)
        return [_card(eng, kind, row) for kind, row in rows]

    def decide(self, displayed: ReviewCard, action: str) -> dict:
        def result(status: str, *, ok: bool = False, changed: bool | None = False) -> dict:
            return {"ok": ok, "status": status, "changed": changed,
                    "id": displayed.item_id, "action": action}

        if review_boundary.mcp_origin():
            return result("local_review_only")
        if action in {"later", "cancel"}:
            return result("pending", ok=True)
        if action not in {"approve", "reject"}:
            return result("invalid_action")
        try:
            ro = Engram(root=self.root, read_only=True)
            # A decided or edited item is rejected before opening a writable
            # handle. Recheck under the same locks as the review engine below.
            if not self._matches(ro, displayed):
                return result("version_conflict")
            with ExitStack() as locks:
                locks.enter_context(ro._review_locks())
                locks.enter_context(hold_directory_lock(ro._identity_dir, timeout=30))
                if not self._matches(ro, displayed):
                    return result("version_conflict")
                marks = [{"id": displayed.item_id, "mark": action,
                          "expected_version": displayed.version}]
                preview = review_cli.preview_marks(ro, marks)
                planned = (preview.get("items") or [{}])[0]
                if planned.get("status") != "planned" or planned.get("unlinked_reason"):
                    return result(str(planned.get("unlinked_reason") or planned.get("status") or "refused"))
                eng = Engram(root=self.root)
                receipt = review_cli.apply_marks(eng, marks, {
                    "operator": "owner", "route": "owner_ui", "isatty": False,
                })
                item = (receipt.get("items") or [{}])[0]
                status = str(item.get("status") or receipt.get("status") or "refused")
                return result(status, ok=status in {"applied", "already_applied"},
                              changed=status in {"applied", "applied_unlinked"})
        except Exception:
            # Never echo exception text (which may contain paths/private bodies).
            # Partial durable review intents are recovered by the existing engine
            # after the owner reloads and reviews the current snapshot again.
            return result("review_failed", changed=None)

    @staticmethod
    def _matches(eng: Engram, displayed: ReviewCard) -> bool:
        matches = [(kind, row) for kind, row in review_cli._pending(eng)
                   if str(row.get("id")) == displayed.item_id]
        # IDs shared by legacy subsystems must not select an unseen operand.
        if len(matches) != 1 or matches[0][0] != displayed.kind:
            return False
        kind, row = matches[0]
        found_kind, _ = eng._find_item_by_id(displayed.item_id)
        return found_kind == kind and _card(eng, kind, row).fingerprint == displayed.fingerprint


class PendingNotices:
    """In-memory coalescing only; pending proposals themselves are the queue."""
    def __init__(self):
        self.seen: set[tuple[str, str, str]] = set()

    def observe(self, cards: list[ReviewCard], *, enabled: bool = True) -> int:
        present = {(c.kind, c.item_id, c.fingerprint) for c in cards}
        new = len(present - self.seen)
        self.seen = present
        return new if enabled else 0


class ReviewWindow:
    """Native, persistent local window; opening/refreshing/closing never applies.

    Keep it running/minimized to receive coalesced, body-free prompts. No service,
    startup task or client configuration is installed silently.
    """
    POLL_MS = 5000

    def __init__(self, root, controller: ReviewController, *, notify: bool = True):
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText

        self.root, self.controller = root, controller
        self.cards: list[ReviewCard] = []
        self.displayed: ReviewCard | None = None
        self.notices = PendingNotices()
        self.prompt = None
        self.closed = False
        root.title("Engram — 待审记忆 / Review proposals")
        root.geometry("960x700")
        root.minsize(700, 450)
        self.status = tk.StringVar(value="仅你点击批准后生效；AI 不能代替你批准。")
        self.notify = tk.BooleanVar(value=notify)
        ttk.Label(root, text="Engram 待审记忆 / Owner review", font=("TkDefaultFont", 15)).pack(anchor="w", padx=16, pady=12)
        toolbar = ttk.Frame(root)
        toolbar.pack(fill="x", padx=16)
        ttk.Button(toolbar, text="刷新 / Refresh", command=self.refresh).pack(side="left")
        ttk.Checkbutton(toolbar, text="新提案弹窗提醒 / Notify", variable=self.notify).pack(side="left", padx=12)
        ttk.Label(root, text="保持窗口运行或最小化以接收提醒。关闭窗口不会批准、拒绝或删除提案。").pack(anchor="w", padx=16, pady=6)
        pane = ttk.Panedwindow(root, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=16, pady=6)
        self.queue = tk.Listbox(pane, exportselection=False, width=30)
        self.queue.bind("<<ListboxSelect>>", self._selected)
        pane.add(self.queue, weight=1)
        self.details = ScrolledText(pane, wrap="word", state="disabled", width=65)
        pane.add(self.details, weight=3)
        buttons = ttk.Frame(root)
        buttons.pack(fill="x", padx=16, pady=8)
        self.approve_button = ttk.Button(buttons, text="批准并生效 / Approve", command=lambda: self._decide("approve"))
        self.reject_button = ttk.Button(buttons, text="拒绝 / Reject", command=lambda: self._decide("reject"))
        self.later_button = ttk.Button(buttons, text="稍后 / Later", command=self._later)
        for button in (self.approve_button, self.reject_button, self.later_button):
            button.pack(side="left", padx=4)
        ttk.Label(root, textvariable=self.status, wraplength=900).pack(anchor="w", padx=16, pady=(0, 12))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self.timer = root.after(self.POLL_MS, self._poll)

    def _buttons(self, enabled: bool):
        for button in (self.approve_button, self.reject_button, self.later_button):
            button.configure(state="normal" if enabled else "disabled")

    def refresh(self):
        try:
            cards = self.controller.pending()
        except Exception:
            self._buttons(False)
            self.status.set("读取失败；提案未获批准。请稍后刷新 / Read failed; nothing approved.")
            return
        # Never replace the displayed snapshot during background refresh.
        # An edited row must be selected again before any approval can apply it.
        self.cards = cards
        self.queue.delete(0, "end")
        for card in cards:
            self.queue.insert("end", f"[{card.kind}] {card.title}")
        active = self.displayed and any(c.fingerprint == self.displayed.fingerprint for c in cards)
        self._buttons(bool(active))
        self.root.title(f"Engram — {len(cards)} 条待审 / pending")
        if self.displayed is not None and not active:
            self.status.set("条目已变化或已处理，请重新选择并查看 / Changed; select and review again.")
        if self.notices.observe(cards, enabled=self.notify.get()):
            try:
                self._notice()
            except Exception:
                self.status.set("提醒未能显示，提案仍待审；请查看列表 / Prompt unavailable; review the queue.")

    def select(self, index: int):
        self.displayed = self.cards[index]
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", self.displayed.text)
        self.details.configure(state="disabled")
        self._buttons(True)
        self.status.set("只批准当前展示的这一条及其版本 / Only this displayed item and version.")

    def _selected(self, _event):
        selection = self.queue.curselection()
        if selection:
            self.select(selection[0])

    def _decide(self, action: str):
        if self.displayed is None:
            return
        self._buttons(False)
        result = self.controller.decide(self.displayed, action)
        self.displayed = None
        self.refresh()
        if result.get("ok"):
            self.status.set("已批准并生效 / Approved" if action == "approve" else "已拒绝 / Rejected")
        else:
            self.status.set("未完成，可能已保存部分结果；请重新查看 / May be partial; review again: " + result["status"])

    def _later(self):
        self.displayed = None
        self._buttons(False)
        self.status.set("仍为待审，稍后可从列表重新打开 / Still pending; review later.")

    def _notice(self):
        if self.prompt is not None and self.prompt.winfo_exists():
            return  # aggregate new items into the same body-free notice
        import tkinter as tk
        from tkinter import ttk
        prompt = self.prompt = tk.Toplevel(self.root)
        prompt.title("Engram — 新待审提案 / New proposals")
        ttk.Label(prompt, text="有新记忆等待你审核；尚未生效。\nNew proposals are pending, not approved.").pack(padx=24, pady=16)
        def view():
            prompt.destroy()
            self.root.deiconify()
            self.root.lift()
        ttk.Button(prompt, text="查看并审核 / Review", command=view).pack(side="left", padx=20, pady=16)
        ttk.Button(prompt, text="稍后 / Later", command=prompt.destroy).pack(side="right", padx=20, pady=16)
        prompt.protocol("WM_DELETE_WINDOW", prompt.destroy)

    def _poll(self):
        if self.closed:
            return
        self.refresh()
        self.timer = self.root.after(self.POLL_MS, self._poll)

    def close(self):
        self.closed = True
        self.root.after_cancel(self.timer)
        self.root.destroy()


def main() -> int:
    """Installed GUI entry point; no args permit scripted approve/reject."""
    import sys
    if len(sys.argv) != 1 or review_boundary.mcp_origin():
        return 2
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception:
        # The GUI wrapper has no terminal on Windows. Show a body-free diagnostic
        # there if Tk is missing; do not fall back to any scripted approval.
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "Engram review needs Python with Tk support.", "Engram", 0)
        return 1
    try:
        ReviewWindow(root, ReviewController(Engram(read_only=True).root))
        root.mainloop()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
