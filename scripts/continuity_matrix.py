"""Offline, synthetic dual-client continuity acceptance through real entry points.

The case contracts and score_observation are transport independent so a later
host experiment can reuse them. No model or client executable is invoked here.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "fixtures" / "continuity_matrix_cases.json"
FIELDS = ("goal", "constraint", "blocker", "last_failure_reason", "next_action", "source")
CLIENTS = {"Client X": "claude_code", "Client Y": "cursor"}
FORBIDDEN = ("CM_FOREIGN_BODY", "CM_PENDING_BODY", "CM_OBSOLETE_BODY")


class StdioPeer:
    """MCP JSON-RPC over anonymous stdio pipes, with bounded reads.

    Anonymous pipes also work on Windows hosts that disallow the named pipes
    used by asyncio's subprocess transport. The server and wire protocol are
    unchanged; no direct Python memory API substitutes for an MCP tool call.
    """
    def __init__(self, env: dict, errlog):
        self.process = subprocess.Popen([sys.executable, "-m", "piia_engram.mcp_server"],
            env=env, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=errlog, text=True, encoding="utf-8")
        self.messages: queue.Queue = queue.Queue()
        self.sequence = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
        finally:
            self.messages.put(None)

    def notify(self, method: str, params: dict | None = None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        self.sequence += 1
        message = {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params or {}}
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 40
        while True:
            reply = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            if reply is None:
                raise RuntimeError("stdio_server_closed")
            if reply.get("id") != self.sequence:
                continue
            if "error" in reply:
                raise RuntimeError("jsonrpc_error:" + str(reply["error"].get("code")))
            return reply["result"]

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()  # Only this trial's server process.
            self.process.wait(timeout=10)
        self.reader.join(timeout=3)
        self.process.stdout.close()
        if self.reader.is_alive():
            raise RuntimeError("stdio_reader_did_not_stop")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_cases() -> list[dict]:
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


def isolated_env(base: Path, store: Path, approval: str) -> dict[str, str]:
    # Do not inherit any live-store, client, crypto, provider or behaviour hint.
    env = {k: v for k, v in os.environ.items() if not k.startswith(
        ("ENGRAM_", "CLAUDE_", "CURSOR_", "CODEX_", "ANTHROPIC_", "OPENAI_", "GIT_"))}
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CLAUDE_CONFIG_DIR",
                "CODEX_HOME", "TEMP", "TMP", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        directory = base / key.lower()
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory.resolve())
    env.update(ENGRAM_DIR=str(store.resolve()), ENGRAM_CACHE_DIR=str(base / "cache"),
               ENGRAM_APPROVAL=approval, ENGRAM_EPHEMERAL="1", ENGRAM_TOOLS="all",
               ENGRAM_NO_UPDATE_CHECK="1", DO_NOT_TRACK="1",
               PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PYTHONPATH=str(ROOT / "src"), GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=str(base / "home" / "gitconfig"), GIT_TERMINAL_PROMPT="0")
    return env


def visible_text(observation: dict) -> str:
    # Only score content delivered to the receiver, never requests, fixtures,
    # expected answers, source files, write acknowledgements or audit records.
    recall = observation.get("recall", {})
    delivered = {key: recall[key] for key in ("identity", "recent_activity", "knowledge") if key in recall}
    return str(observation.get("resume", {}).get("markdown", "")) + "\n" + json.dumps(delivered, ensure_ascii=False)


def score_observation(case: dict, observation: dict, source_tool: str,
                      *, proposal_is_approved: bool = False) -> dict:
    text = visible_text(observation)
    required = dict(case["required"])
    required["source"] = source_tool
    fields = {name: value in text for name, value in required.items() if value is not None}
    # A self-written Source: line cannot establish attribution. Require Engram's
    # native earlier-record warning and a non-unknown source in its handoff.
    markdown = str(observation.get("resume", {}).get("markdown", ""))
    source_lines = [line for line in markdown.splitlines() if "**source**:" in line]
    fields["source"] = bool(fields.get("source") and source_lines and
        "unknown;" not in source_lines[0] and "earlier session record" in markdown and
        "unreviewed" in markdown)
    forbidden = list(FORBIDDEN)
    if not proposal_is_approved:
        forbidden.append("CM_NEW_PROPOSAL")
    wrong = [token for token in forbidden if token in text]
    return {"required_fields": required, "fields": fields,
            "surface_fields": {surface: {key: value in body for key, value in required.items() if value}
                for surface, body in (("resume", markdown), ("recall", json.dumps(observation.get("recall", {}), ensure_ascii=False)))},
            "missing_fields": [key for key, present in fields.items() if not present],
            "continuation_complete": all(fields.values()),
            "wrong_adoption": len(wrong), "wrong_adoption_markers": wrong}


def print_table(rows: list[dict], *, header: bool = True) -> None:
    if header:
        print("direction | integration | approval | scenario | direct | after drain | accepted | wrong | review | ms | cost")
    for row in rows:
        final = row.get("after_drain", {})
        direct = row.get("direct", {})
        print(" | ".join((row["direction"], row["integration"], row["approval"], row["scenario"],
            str(direct.get("continuation_complete", False)),
            str(final.get("continuation_complete", False)), str(row.get("accepted", False)), str(row.get("wrong_adoption", "?")),
            str(row.get("owner_review_items", "?")), str(row.get("elapsed_ms", "?")), "0")), flush=True)


class Trial:
    def __init__(self, base: Path, case: dict, direction: tuple[str, str], integration: str, approval: str):
        self.base, self.case = base, case
        self.sender, self.receiver = direction
        self.tool = CLIENTS[self.sender]
        self.integration, self.approval = integration, approval
        self.store = base / "store"
        self.env = isolated_env(base / "profile", self.store, approval)
        self.steps: list[dict] = []
        self.observations: dict = {}
        self.checks: dict[str, bool] = {}

    def command(self, name: str, args: list[str], *, payload: dict | None = None,
                expected_codes: tuple[int, ...] = (0,)) -> str:
        started = time.perf_counter()
        result = subprocess.run(args, input=json.dumps(payload, ensure_ascii=False) if payload else None,
            env=self.env, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=45)
        self.steps.append({"step": name, "latency_ms": round((time.perf_counter()-started)*1000, 3),
                           "returncode": result.returncode})
        # Child diagnostics stay in the isolated trial; reports use closed codes.
        with (self.base / "child-stderr.log").open("a", encoding="utf-8") as log:
            log.write(result.stderr)
        if result.returncode not in expected_codes:
            raise RuntimeError("child_command_failed:" + name)
        return result.stdout

    def git(self, cwd: Path, *args: str) -> None:
        self.command("fixture_git_" + args[0], ["git", "-C", str(cwd),
            "-c", "core.hooksPath=", "-c", "commit.gpgsign=false", *args])

    def setup(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        self.project = self.base / "source" / "sample"
        self.project.mkdir(parents=True)
        self.target = self.project
        if self.case["id"] == "git_worktrees":
            self.git(self.project, "init")
            (self.project / "sample.txt").write_text("synthetic\n", encoding="utf-8")
            self.git(self.project, "add", "sample.txt")
            self.git(self.project, "-c", "user.name=Synthetic", "-c", "user.email=fixture@example.invalid",
                     "commit", "-m", "Initialize synthetic fixture")
            self.target = self.base / "receiving-worktree"
            self.git(self.project, "worktree", "add", "-b", "receiving", str(self.target))
        # Seed only governance controls, never the six required task fields.
        # These static synthetic records are not Owner actions on a live store.
        foreign = self.base / "other" / "sample"
        foreign.mkdir(parents=True)
        self.foreign = foreign
        now = datetime.now(timezone.utc).isoformat()
        def row(key, body, tier="verified", project=None):
            return {"id": key, "summary": "continuity " + body, "domain": "testing",
                "status": "active", "tier": tier, "timestamp": now, "last_reviewed": now,
                "project_folder": str(project or self.project), "source_tool": "fixture"}
        write_json(self.store / "knowledge" / "lessons.json", [
            row("cm-current", "CM_CURRENT_BODY"), row("cm-old", "CM_OBSOLETE_BODY"),
            row("cm-pending", "CM_PENDING_BODY", "staging"),
            row("cm-foreign", "CM_FOREIGN_BODY", project=foreign)])
        write_json(self.store / "knowledge" / "relations.json", [{"src": "cm-current", "rel": "supersedes", "dst": "cm-old"}])
        self.content = "\n".join(f"{label}: {self.case['required'][field]}" for label, field in (
            ("Goal", "goal"), ("Constraint", "constraint"), ("Risk", "blocker"),
            ("Last failure reason", "last_failure_reason"), ("Next", "next_action")))

    @asynccontextmanager
    async def client(self, name: str):
        started = time.perf_counter()
        with (self.base / "server-stderr.log").open("a", encoding="utf-8") as errlog:
            session = StdioPeer(self.env, errlog)
            try:
                info = await asyncio.to_thread(session.request, "initialize", {
                    "protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": name, "version": "synthetic-v1"}})
                session.notify("notifications/initialized")
                tools = await asyncio.to_thread(session.request, "tools/list")
                assert info["serverInfo"]["name"] == "engram"
                assert {"get_resume_brief", "get_recall", "save_agent_context", "add_lesson"} <= {
                    tool["name"] for tool in tools["tools"]}
                self.steps.append({"step": name + ":initialize_and_list",
                    "latency_ms": round((time.perf_counter()-started)*1000, 3)})
                yield session
            finally:
                session.close()

    async def call(self, session, name: str, args: dict):
        started = time.perf_counter()
        result = await asyncio.to_thread(session.request, "tools/call", {"name": name, "arguments": args})
        self.steps.append({"step": "mcp:" + name, "latency_ms": round((time.perf_counter()-started)*1000, 3),
                           "is_error": bool(result.get("isError", False))})
        text = "\n".join(block["text"] for block in result.get("content", []) if "text" in block)
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {"text": text}
        return payload, bool(result.get("isError", False))

    async def observe(self, label: str, project: Path | None = None) -> dict:
        async with self.client(self.receiver) as session:
            brief, error = await self.call(session, "get_resume_brief", {
                "project_folder": str(project or self.target), "token_budget": 1500})
            recall, recall_error = await self.call(session, "get_recall", {
                "project_folder": str(project or self.target), "limit": 20, "token_budget": 1500})
        if error or recall_error or "error" in brief or "error" in recall:
            raise RuntimeError("receiving_surface_failed")
        observation = {"resume": brief, "recall": recall}
        self.observations[label] = observation
        return observation

    def hook(self, module: str, payload: dict, *args: str) -> dict:
        raw = self.command("hook:" + module, [sys.executable, "-m", "piia_engram.hooks." + module, *args], payload=payload)
        return json.loads(raw) if raw.strip() else {}

    def transcript(self, content: str) -> Path:
        path = self.base / "synthetic-transcript.jsonl"
        entries = [{"type": "assistant", "timestamp": f"2026-01-01T10:00:{i:02d}Z",
                    "content": [{"type": "text", "text": content}],
                    "message": {"role": "assistant", "content": [{"type": "text", "text": content}]}}
                   for i in range(12)]
        path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8")
        return path

    def capture(self, *, compact: bool = False) -> None:
        if self.tool == "claude_code":
            path = self.transcript(self.content)
            args = ("--env", "ENGRAM_MIN_TURNS_TO_FLUSH=5") if compact else ()
            self.hook("auto_save_on_stop", {"cwd": str(self.project), "transcript_path": str(path),
                "session_id": "synthetic-session", "hook_event_name": "PreCompact" if compact else "Stop"}, *args)
            if compact:
                self.hook("auto_absorb_compact", {"cwd": str(self.project), "transcript_path": str(path)})
        else:
            self.hook("cursor_save_on_stop", {"workspace_roots": [str(self.project)],
                "conversation_id": "synthetic-session", "summary": self.content},
                "--event", "sessionEnd" if compact else "stop")

    def drain(self, label: str = "drain") -> dict:
        result = json.loads(self.command(label, [sys.executable, "-m", "piia_engram.setup_wizard", "hooks", "drain", "--json"],
            expected_codes=(0, 1)))
        return {key: result.get(key) for key in ("pending", "processed", "duplicates", "failed", "busy", "quarantined")}

    def queue(self) -> list[Path]:
        return sorted((self.store / "hooks" / "spool").glob("*.jsonl"))

    async def manual_save(self) -> tuple[dict, bool]:
        async with self.client(self.sender) as session:
            return await self.call(session, "save_agent_context", {"tool": self.tool, "content": self.content,
                "project_folder": str(self.project), "session_id": "synthetic-session"})

    async def run(self) -> dict:
        import portalocker
        started = time.perf_counter()
        self.setup()
        cold = await self.observe("cold")
        cold_score = score_observation(self.case, cold, self.tool, proposal_is_approved=self.approval == "default")
        self.checks["cold_has_no_task_fields"] = not any(v in visible_text(cold)
            for k, v in self.case["required"].items() if k != "source" and v)
        # A confirmed tool invocation is a synthetic transport input. Strict
        # must still stage it; it is not an Owner approval operation.
        async with self.client(self.sender) as session:
            ack, err = await self.call(session, "add_lesson", {"summary": "CM_NEW_PROPOSAL Use stable sample labels.",
                "source_tool": self.tool, "project_folder": str(self.project), "user_confirmed": True})
            if err or "error" in ack:
                raise RuntimeError("proposal_write_failed")
        scenario = self.case["id"]
        barrier = self.store / "contexts" / self.tool
        lock = None
        if scenario == "store_unwritable":
            barrier.parent.mkdir(parents=True, exist_ok=True)
            barrier.write_text("synthetic non-directory write barrier\n", encoding="utf-8")
        if scenario == "store_locked":
            barrier.mkdir(parents=True, exist_ok=True)
            lock = portalocker.Lock(barrier / ".engram-write.lock", "a", timeout=0, encoding="utf-8")
            lock.acquire()
        failed_write = None
        recovery = None
        try:
            if self.integration == "manual_mcp":
                if scenario == "restart_compaction":
                    saved = self.content
                    self.content = "Goal: " + self.case["required"]["goal"]
                    await self.manual_save()  # Source process exits before the compact checkpoint.
                    self.content = saved
                payload, err = await self.manual_save()
                if scenario.startswith("store_"):
                    failed_write = payload
                    self.checks["unavailable_write_rejected"] = err or "error" in payload
                elif err or "error" in payload:
                    raise RuntimeError("checkpoint_write_failed")
                if scenario == "duplicate_events":
                    await self.manual_save()  # Native append semantics, one logical session.
            else:
                self.capture(compact=scenario == "restart_compaction")
                self.checks["hook_queued"] = bool(self.queue())
                if scenario.startswith("store_"):
                    recovery = self.drain("drain_while_unavailable")
                    self.checks["failed_drain_retains_event"] = bool(recovery["failed"] and recovery["pending"])
        finally:
            if lock:
                lock.release()
            if scenario == "store_unwritable":
                barrier.unlink()
        # This read is deliberately before any successful explicit processor run.
        direct_obs = await self.observe("direct")
        direct = score_observation(self.case, direct_obs, self.tool, proposal_is_approved=self.approval == "default")
        if self.integration == "manual_mcp" and scenario.startswith("store_"):
            payload, err = await self.manual_save()
            self.checks["retry_saved"] = not err and "error" not in payload
        originals = [(path.name, path.read_bytes()) for path in self.queue()]
        drained = self.drain()
        self.checks["drain_finished"] = drained["pending"] == 0 and drained["failed"] == 0 and not drained["busy"]
        if scenario == "duplicate_events" and self.integration == "automatic_hooks":
            for name, data in originals:
                (self.store / "hooks" / "spool" / name).write_bytes(data)
            replay = self.drain("duplicate_replay")
            self.checks["receipt_deduplicated"] = replay["duplicates"] == len(originals) and replay["processed"] == 0
        else:
            replay = None
        if scenario == "restart_compaction" and self.integration == "automatic_hooks":
            transcript = self.base / "synthetic-transcript.jsonl"
            if transcript.exists():
                transcript.write_text("", encoding="utf-8")  # Receiver cannot recover by rereading input.
        final_obs = await self.observe("after_drain")
        final = score_observation(self.case, final_obs, self.tool, proposal_is_approved=self.approval == "default")
        if self.integration == "automatic_hooks":
            module = "auto_inject_resume_brief" if self.receiver == "Client X" else "cursor_inject_resume_brief"
            injected = self.hook(module, {"cwd": str(self.target)})
            markdown = injected.get("hookSpecificOutput", {}).get("additionalContext", "")
            self.observations["injection"] = injected
            injection = score_observation(self.case, {"resume": {"markdown": markdown}}, self.tool,
                proposal_is_approved=self.approval == "default")
        else:
            injection = None
        if scenario == "same_name_isolation":
            other_obs = await self.observe("same_name_negative", self.foreign)
            other_text = visible_text(other_obs)
            leaked = [key for key, value in self.case["required"].items() if key != "source" and value in other_text]
            self.checks["same_name_no_task_leak"] = not leaked
            self.checks["foreign_control_is_reachable_in_own_project"] = "CM_FOREIGN_BODY" in other_text
        else:
            leaked = []
        lessons = json.loads((self.store / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
        proposal = [item for item in lessons if "CM_NEW_PROPOSAL" in item.get("summary", "")]
        self.checks["approval_mode_honored"] = len(proposal) == 1 and proposal[0]["tier"] == (
            "staging" if self.approval == "strict" else "verified")
        self.checks["current_control_recalled"] = "CM_CURRENT_BODY" in visible_text(final_obs)
        if scenario == "duplicate_events":
            self.checks["one_logical_session"] = len(list((self.store / "contexts" / self.tool).glob("*.md"))) == 1
        # Raw local evidence is isolated and never printed into public summaries.
        write_json(self.base / "observations.json", self.observations)
        review_count = sum(item.get("tier") == "staging" for item in lessons)
        recoverable = final["continuation_complete"] and final["wrong_adoption"] == 0 and all(self.checks.values())
        wrong_total = cold_score["wrong_adoption"] + direct["wrong_adoption"] + final["wrong_adoption"] + (
            injection or {}).get("wrong_adoption", 0) + len(leaked)
        return {"scenario": scenario, "direction": self.sender + "->" + self.receiver,
            "integration": self.integration, "approval": self.approval,
            "direct": direct, "after_drain": final, "hook_injection_after_drain": injection,
            "checks": self.checks, "recoverable_after_drain": recoverable,
            "wrong_adoption": wrong_total,
            "negative_isolation_leaked_fields": leaked,
            "accepted": recoverable and wrong_total == 0 and (self.integration == "manual_mcp" or
                (direct["continuation_complete"] and injection["continuation_complete"])),
            "automatic_end_to_end": self.integration == "automatic_hooks" and direct["continuation_complete"],
            "drain": drained, "unavailable_drain": recovery, "duplicate_drain": replay,
            "unavailable_write_observed": failed_write is not None,
            "owner_review_items": review_count, "owner_review_new_items": review_count - 1,
            "model_calls": 0, "model_cost": 0,
            "steps": self.steps, "elapsed_ms": round((time.perf_counter()-started)*1000, 3)}


async def run_matrix(work_dir: Path, *, cases: list[dict] | None = None) -> dict:
    # Require a fresh explicit root: never reuse somebody else's store or fixtures.
    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    print_table([])
    for case in cases if cases is not None else load_cases():
        for direction in (("Client X", "Client Y"), ("Client Y", "Client X")):
            for integration in ("manual_mcp", "automatic_hooks"):
                for approval in ("default", "strict"):
                    key = f"{case['id']}-{CLIENTS[direction[0]]}-{integration}-{approval}"
                    trial = Trial(work_dir / key, case, direction, integration, approval)
                    try:
                        row = await asyncio.wait_for(trial.run(), timeout=180)
                    except Exception as exc:
                        row = {"scenario": case["id"], "direction": "->".join(direction),
                            "integration": integration, "approval": approval, "accepted": False,
                            "error": type(exc).__name__, "steps": trial.steps, "model_calls": 0, "model_cost": 0}
                        # Full exception is private local evidence only.
                        import traceback
                        (trial.base / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
                    rows.append(row)
                    write_json(work_dir / "partial-results.json", rows)
                    print_table([row], header=False)
    return {"schema": "synthetic_continuity_matrix.v1", "synthetic": True,
        "real_host_verified": False, "model_calls": 0, "model_cost": 0, "cases": len(cases or load_cases()),
        "rows": rows, "accepted_count": sum(row["accepted"] for row in rows),
        "wrong_adoption": sum(row.get("wrong_adoption", 0) for row in rows),
        "error_count": sum("error" in row for row in rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True, help="New isolated fixture/evidence directory")
    parser.add_argument("--output", type=Path, required=True, help="JSON result path")
    parser.add_argument("--scenario", choices=[case["id"] for case in load_cases()])
    args = parser.parse_args()
    cases = [case for case in load_cases() if case["id"] == args.scenario] if args.scenario else None
    result = asyncio.run(run_matrix(args.work_dir, cases=cases))
    write_json(args.output, result)
    print(f"Accepted {result['accepted_count']}/{len(result['rows'])}; errors {result['error_count']}; "
          f"wrong adoption {result['wrong_adoption']}; model cost 0")
    return 0 if result["accepted_count"] == len(result["rows"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
