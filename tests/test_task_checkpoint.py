"""Opt-in task capture contracts; all transcripts, stores and profiles are synthetic."""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import threading
import time

import pytest
from task_checkpoint_harness import install_capture_success

from piia_engram.hooks import _producer, _processor, spool
from piia_engram import mcp_server

FIELDS = ("goal", "constraint", "blocker", "last_failure_reason", "next_action")
VALUES = ("Finish sample task", "Keep sample invariant", "Await sample input",
          "Sample assertion failed", "Run sample verification")
LABELS = ("Goal", "Constraint", "Risk", "Last failure reason", "Next")
ZH_LABELS = ("目标", "约束", "风险", "上次失败原因", "下一步")


def api():
    from piia_engram import task_checkpoint
    return task_checkpoint


def block(values=VALUES, labels=LABELS):
    return "\n".join(f"{label}: {value}" for label, value in zip(labels, values))


def record(text, shape="nested", **extra):
    content = [{"type": "text", "text": text}]
    result = ({"type": "assistant", "content": content} if shape == "top" else
              {"type": "assistant", "message": {"role": "assistant", "content": content}})
    return dict(result, **extra)


def transcript(tmp_path, entries=None):
    path = tmp_path / "transcript.jsonl"
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in
                           (entries if entries is not None else [record(block())])), encoding="utf-8")
    return path


def opt_in(monkeypatch, root, *, real_budget=False):
    monkeypatch.setenv("ENGRAM_HOOK_TASK_CHECKPOINT_V1", "1")
    monkeypatch.setenv("ENGRAM_HOOK_TRANSCRIPT_ROOTS", json.dumps([str(root)]))
    if not real_budget:
        install_capture_success(monkeypatch)


def produce(monkeypatch, path, project, *, event="Stop"):
    monkeypatch.setattr(_producer.sys, "stdin", io.StringIO(json.dumps({
        "cwd": str(project), "transcript_path": str(path), "hook_event_name": event})))
    assert _producer.claude_event("claude_stop") == 0
    queued = list(spool.spool_dir().glob("*.jsonl"))
    assert len(queued) == 1
    return queued[0]


@pytest.mark.parametrize("switch", [None, "0", "true", "yes", " 1", "2"])
def test_off_state_never_reads_transcript(tmp_path, monkeypatch, switch):
    if switch is None:
        monkeypatch.delenv("ENGRAM_HOOK_TASK_CHECKPOINT_V1", raising=False)
    else:
        monkeypatch.setenv("ENGRAM_HOOK_TASK_CHECKPOINT_V1", switch)
    path = transcript(tmp_path)
    def denied(*args, **kwargs):
        raise AssertionError("off-state transcript read")
    monkeypatch.setattr(Path, "open", denied)
    captured = []
    monkeypatch.setattr(_producer, "enqueue", lambda *args, **kwargs: captured.append(args[2]))
    monkeypatch.setattr(_producer, "_read_input", lambda deadline: {"transcript_path": str(path)})
    assert _producer.claude_event("claude_stop") == 0
    assert len(captured) == 1
    assert set(captured[0]) == {"transcript_path", "project_folder", "threshold"}


@pytest.mark.parametrize("shape", ["top", "nested"])
@pytest.mark.parametrize("event", ["Stop", "PreCompact"])
def test_snapshot_survives_source_deletion_below_threshold(tmp_path, monkeypatch, shape, event):
    opt_in(monkeypatch, tmp_path)
    path = transcript(tmp_path, [record(block(), shape)])
    project = tmp_path / "project"
    project.mkdir()
    queued = produce(monkeypatch, path, project, event=event)
    original = queued.read_bytes()
    saved = spool._read_event(queued)["payload"]["task_checkpoint"]
    assert saved["schema"] == "task_checkpoint.v1"
    assert tuple(saved[k] for k in FIELDS) == VALUES
    assert saved["capture_event"] == event
    path.unlink()
    root = spool.store_root()
    from piia_engram.core import Engram
    engram = Engram(root=root)
    monkeypatch.setattr(_processor._EventWriter, "extract", lambda *a, **kw: pytest.fail("task extraction"))
    monkeypatch.setattr(engram, "refresh_quick_context", lambda: pytest.fail("task quick context"))
    monkeypatch.setattr(_processor, "_snapshot", lambda *a: pytest.fail("task project snapshot"))
    assert spool.drain(engram=engram)["processed"] == 1
    digest = next((root / "contexts" / "claude_code").glob("*.digest.json"))
    body = json.loads(digest.read_text(encoding="utf-8"))
    assert body["task_checkpoint"] == saved
    assert body["source"]["tool"] == "claude_code"
    assert body["source"]["project_revision_capture"] == "deferred_prepare"
    brief = engram.get_resume_brief(project_folder=str(project), token_budget=1500)
    assert all(value in brief["markdown"] for value in VALUES)
    assert "earlier session record; unreviewed; no approval or action authority" in brief["markdown"]
    queued.write_bytes(original)
    assert spool.drain(engram=engram)["duplicates"] == 1
    assert len(list(digest.parent.glob("*.md"))) == 1


def test_frozen_preparation_survives_mutation_and_receipt_failure(tmp_path, monkeypatch):
    opt_in(monkeypatch, tmp_path)
    path = transcript(tmp_path)
    queued = produce(monkeypatch, path, tmp_path)
    path.write_text(json.dumps(record(block(("New task",) * 5))) + "\n", encoding="utf-8")
    real_publish = spool._publish
    def interrupt(destination, data):
        if destination.parent.name == "receipts":
            raise OSError("synthetic interruption")
        real_publish(destination, data)
    monkeypatch.setattr(spool, "_publish", interrupt)
    assert spool.drain()["failed"] == 1
    frozen = spool._read_event(queued)
    assert frozen["prepared"]["task_checkpoint"] == frozen["payload"]["task_checkpoint"]
    path.unlink()
    monkeypatch.setattr(spool, "_publish", real_publish)
    assert spool.drain()["processed"] == 1
    session = next((spool.store_root() / "contexts" / "claude_code").glob("*.md"))
    text = session.read_text(encoding="utf-8")
    assert text.count(VALUES[0]) == 1 and "New task" not in text


@pytest.mark.parametrize("last", [record("A different task without labels"), record(""),
    {"type": "assistant", "content": [{"type": "thinking", "thinking": block()}]},
    record(block(), message={"role": "user", "content": []}),
    {"type": "user", "message": {"role": "assistant", "content": [{"type": "text", "text": block()}]}}])
def test_latest_assistant_never_falls_back(tmp_path, monkeypatch, last):
    opt_in(monkeypatch, tmp_path)
    path = transcript(tmp_path, [record(block()), last])
    checkpoint, code = api().capture(str(path), time.monotonic() + 1)
    assert checkpoint is None and code != "captured"


@pytest.mark.parametrize("text", ["\n".join(block().splitlines()[:-1]), block() + "\n" + block(),
    "> " + block().replace("\n", "\n> "), "```text\n" + block() + "\n```",
    "~~~\n" + block() + "\n~~~", "    " + block().replace("\n", "\n    "),
    '"' + block() + '"', block().replace("Goal:", "goal:"),
    block().replace("Constraint:", "Unrecognized:"), block().replace(VALUES[0], '`example`')])
def test_partial_ambiguous_quoted_fenced_labels_are_rejected(text):
    assert api().parse_text(text)[0] is None


@pytest.mark.parametrize("opening,inner,closing", [
    ("````markdown", "```", "````"),
    ("```markdown", "``` not-a-close", "```"),
    ("```markdown", "```not-a-close", "```"),
    ("~~~~markdown", "~~~", "~~~~"),
    ("~~~markdown", "~~~ not-a-close", "~~~"),
    ("````", "~~~\n```", ""),
    ("~~~~", "```\n~~~", ""),
])
def test_fenced_examples_never_reach_persistence_or_receiving_brief(
        tmp_path, monkeypatch, opening, inner, closing):
    opt_in(monkeypatch, tmp_path)
    text = opening + "\n" + inner + "\n\n" + block() + "\n\n" + closing
    path = transcript(tmp_path, [record(text)] * 12)
    queued = produce(monkeypatch, path, tmp_path)
    event = spool._read_event(queued)
    queue_bytes = queued.read_bytes()
    assert spool.drain()["processed"] == 1
    root = spool.store_root()
    assert list((root / "contexts" / "claude_code").glob("*.md"))
    from piia_engram.core import Engram
    output = Engram(root=root).get_resume_brief(project_folder=str(tmp_path))["markdown"]
    assert "task_checkpoint" not in event["payload"]
    assert all(value not in output for value in VALUES)
    for value in VALUES:
        assert value.encode("utf-8") not in queue_bytes
        for candidate in root.rglob("*"):
            if candidate.is_file() and not candidate.name.endswith(".lock"):
                assert value.encode("utf-8") not in candidate.read_bytes(), candidate.name


WINDOWS_PATHS = (
    "D:/SyntheticPrivate/customer-notes/report.txt",
    "D:\\SyntheticPrivate\\customer-notes\\report.txt",
    "D:/SyntheticPrivate\\customer-notes/report.txt",
    "D:\\SyntheticPrivate/customer-notes\\report.txt",
    "\\\\server\\share\\SyntheticPrivate\\report.txt",
    "//server/share/SyntheticPrivate/report.txt",
    "\\server\\share\\SyntheticPrivate\\report.txt",
    "\\\\?\\D:\\SyntheticPrivate\\report.txt",
    "\\\\?\\D:/SyntheticPrivate/report.txt",
    "\\\\?\\UNC\\server\\share\\SyntheticPrivate\\report.txt",
    "\\?\\D:\\SyntheticPrivate\\report.txt",
)


@pytest.mark.parametrize("private_path,field", [(path, FIELDS[i % len(FIELDS)])
    for i, path in enumerate(WINDOWS_PATHS)])
@pytest.mark.parametrize("interruption", ["archive", "digest"])
def test_windows_path_omission_across_queue_prepared_partials_and_receiving(
        tmp_path, monkeypatch, private_path, field, interruption):
    opt_in(monkeypatch, tmp_path)
    values = tuple("Inspect " + private_path if key == field else value
                   for key, value in zip(FIELDS, VALUES))
    path = transcript(tmp_path, [record(block(values))])
    queued = produce(monkeypatch, path, tmp_path)
    task = spool._read_event(queued)["payload"]["task_checkpoint"]
    assert task[field] is None and task["omissions"] == {field: "privacy"}
    assert all(task[key] == value for key, value in zip(FIELDS, VALUES) if key != field)
    from piia_engram import storage
    real_publish, real_update = spool._publish, storage._update_json
    if interruption == "archive":
        def partial(destination, data):
            if destination.suffix == ".md":
                destination.with_suffix(".partial").write_bytes(data)
                raise OSError("synthetic interruption")
            return real_publish(destination, data)
        monkeypatch.setattr(spool, "_publish", partial)
    else:
        def partial_json(destination, updater, *args, **kwargs):
            if destination.name.endswith(".digest.json"):
                destination.with_suffix(".partial").write_text(
                    json.dumps(updater({}), ensure_ascii=False), encoding="utf-8")
                raise OSError("synthetic interruption")
            return real_update(destination, updater, *args, **kwargs)
        monkeypatch.setattr(storage, "_update_json", partial_json)
    assert spool.drain()["failed"] == 1
    event = spool._read_event(queued)
    assert event["prepared"]["task_checkpoint"] == task
    root = spool.store_root()
    # Recheck forged body at preparation, frozen replay, render and sidecar input.
    forged = dict(task, **{field: values[FIELDS.index(field)]}, omissions={})
    with pytest.raises(ValueError):
        api().validate(forged)
    with pytest.raises(ValueError):
        api().render(forged)
    forged_event = copy.deepcopy(event)
    forged_event["payload"]["task_checkpoint"] = forged
    with pytest.raises(ValueError):
        _processor.prepare(forged_event, root)
    forged_event = copy.deepcopy(event)
    forged_event["prepared"]["task_checkpoint"] = forged
    with pytest.raises(ValueError):
        _processor.validate_payload(forged_event)
    path.unlink()
    monkeypatch.setattr(spool, "_publish", real_publish)
    monkeypatch.setattr(storage, "_update_json", real_update)
    assert spool.drain()["processed"] == 1
    assert list(root.rglob("*.partial"))
    assert list(root.rglob("*.digest.json"))
    from piia_engram.core import Engram
    engram = Engram(root=root)
    output = engram.get_resume_brief(project_folder=str(tmp_path))["markdown"]
    assert "SyntheticPrivate" not in output
    assert all(value in output for key, value in zip(FIELDS, VALUES) if key != field)
    # Include diagnostics plus the JSON-escaped and rendered canaries.
    for candidate in root.parent.rglob("*"):
        if candidate.is_file() and not candidate.name.endswith(".lock"):
            assert b"SyntheticPrivate" not in candidate.read_bytes(), candidate.name
    sidecar = next(root.rglob("*.digest.json"))
    digest = json.loads(sidecar.read_text(encoding="utf-8"))
    digest["task_checkpoint"] = forged
    sidecar.write_text(json.dumps(digest, ensure_ascii=False), encoding="utf-8")
    refused = engram.get_resume_brief(project_folder=str(tmp_path))["markdown"]
    assert "SyntheticPrivate" not in refused


def test_capture_success_contract_includes_slow_extraction(tmp_path, monkeypatch):
    opt_in(monkeypatch, tmp_path)
    original = api()._read_latest
    def slow(*args):
        time.sleep(0.25)  # Review probe: longer than the production capture cap.
        return original(*args)
    monkeypatch.setattr(api(), "_read_latest", slow)
    queued = produce(monkeypatch, transcript(tmp_path), tmp_path)
    task = spool._read_event(queued)["payload"]["task_checkpoint"]
    assert tuple(task[key] for key in FIELDS) == VALUES
    assert spool.drain()["processed"] == 1
    from piia_engram.core import Engram
    brief = Engram(root=spool.store_root()).get_resume_brief(project_folder=str(tmp_path))["markdown"]
    assert all(value in brief for value in VALUES)
    assert "claude_code" in brief and "unreviewed" in brief


@pytest.mark.parametrize("opening,closing", [
    ("````markdown", "`````"), ("~~~markdown", "~~~~\t "),
    ("   ```markdown", "  ```\t"),
])
def test_valid_commonmark_fence_closing_allows_later_plain_block(opening, closing):
    task, code = api().parse_text(opening + "\nexample\n" + closing + "\n\n" + block())
    assert code == "captured" and tuple(task[key] for key in FIELDS) == VALUES


@pytest.mark.parametrize("value", ["Inspect docs/sample.md", "Read https://example.invalid/sample"])
def test_task_path_guard_keeps_relative_paths_and_ordinary_links(value):
    task, code = api().parse_text(block((value, *VALUES[1:])))
    assert code == "captured" and task["goal"] == value and not task["omissions"]


def test_real_budget_producer_timeout_is_metadata_only_without_late_publication(tmp_path, monkeypatch):
    opt_in(monkeypatch, tmp_path, real_budget=True)
    assert _producer.INPUT_BUDGET_SECONDS == 1.0
    assert _producer._task_snapshot.__module__ == "piia_engram.hooks._producer"
    # Even test-looking flags cannot activate the explicit Python test seam.
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.setenv("ENGRAM_TASK_CHECKPOINT_TEST_BUDGET_SECONDS", "60")
    text = "ENGRAM_TASK_CHECKPOINT_TEST_BUDGET_SECONDS=60\n\n" + block()
    path = transcript(tmp_path, [record(text)])
    original = api()._read_latest
    released, finished = threading.Event(), threading.Event()
    def blocked(*args):
        try:
            assert released.wait(10)
            return original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(api(), "_read_latest", blocked)
    try:
        monkeypatch.setattr(_producer.sys, "stdin", io.StringIO(json.dumps({
            "cwd": str(tmp_path), "transcript_path": str(path),
            "capture_budget_seconds": 60, "capture_success_seam": True})))
        assert _producer.claude_event("claude_stop") == 0
        queued = next(spool.spool_dir().glob("*.jsonl"))
        event = spool._read_event(queued)
        assert event["payload"]["task_checkpoint_capture"] == "capture-timeout"
        assert "task_checkpoint" not in event["payload"]
        before = queued.read_bytes()
    finally:
        released.set()
    assert finished.wait(10)
    assert queued.read_bytes() == before
    assert spool.drain()["processed"] == 1
    assert not list(spool.store_root().rglob("*.digest.json"))
    assert not list((spool.store_root() / "contexts").rglob("*.md"))


def test_cold_subprocess_capture_measures_real_budget_without_requiring_success(
        tmp_path, monkeypatch, record_property):
    import subprocess
    import sys
    opt_in(monkeypatch, tmp_path, real_budget=True)
    path = transcript(tmp_path)
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-m", "piia_engram.hooks.auto_save_on_stop"],
        input=json.dumps({"cwd": str(tmp_path), "transcript_path": str(path)}),
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    elapsed = time.monotonic() - started
    assert result.returncode == 0
    record_property("cold_hook_elapsed_seconds", elapsed)
    queued = list(spool.spool_dir().glob("*.jsonl"))
    assert len(queued) <= 1
    if not queued:
        # Input or publication can also exhaust the real application budget.
        # This measurement must not turn best-effort enqueue into a contract.
        record_property("cold_capture_outcome", "not-published")
        assert not list(spool.store_root().rglob("*.digest.json"))
        return
    event = spool._read_event(queued[0])
    outcome = event["payload"]["task_checkpoint_capture"]
    record_property("cold_capture_outcome", outcome)
    assert outcome in {"captured", "capture-timeout"}
    if outcome == "captured":
        task = event["payload"]["task_checkpoint"]
        assert tuple(task[key] for key in FIELDS) == VALUES
    else:
        assert "task_checkpoint" not in event["payload"]


def test_chinese_aliases_unknowns_and_oversized_fields():
    values = ("完成示例目标", "保持数据隔离", "等待验证", "字段顺序错误", "运行验证")
    checkpoint, _ = api().parse_text(block(values, ZH_LABELS))
    assert tuple(checkpoint[k] for k in FIELDS) == values
    checkpoint, _ = api().parse_text(block(("unknown", "未知", "长" * 241, VALUES[3], VALUES[4])))
    assert checkpoint["goal"] is checkpoint["constraint"] is checkpoint["blocker"] is None
    assert checkpoint["omissions"] == {"goal": "unknown", "constraint": "unknown", "blocker": "oversized"}
    assert api().parse_text(block(("unknown",) * 5))[0] is None
    assert api().parse_text(block(("界" * 240,) * 5))[0] is not None


@pytest.mark.parametrize("roots", [None, "", "not-json", "[]", '["relative"]', '{}'])
def test_owner_allowlist_required_and_payload_cannot_supply_it(tmp_path, monkeypatch, roots):
    install_capture_success(monkeypatch)
    monkeypatch.setenv("ENGRAM_HOOK_TASK_CHECKPOINT_V1", "1")
    if roots is None:
        monkeypatch.delenv("ENGRAM_HOOK_TRANSCRIPT_ROOTS", raising=False)
    else:
        monkeypatch.setenv("ENGRAM_HOOK_TRANSCRIPT_ROOTS", roots)
    checkpoint, code = api().capture(str(transcript(tmp_path)), time.monotonic() + 1)
    assert checkpoint is None and code == "path-refused"


def test_path_escape_and_non_regular_file(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    opt_in(monkeypatch, allowed)
    outside = transcript(tmp_path)
    for path in (outside, allowed, allowed / "../transcript.jsonl", Path("relative.jsonl")):
        assert api().capture(str(path), time.monotonic() + 1)[0] is None
    for path in ("//server/share/task.jsonl", "\\\\?\\C:\\task.jsonl", "NUL.jsonl"):
        assert api().capture(path, time.monotonic() + 1)[0] is None


def test_tail_and_record_bounds(tmp_path, monkeypatch):
    opt_in(monkeypatch, tmp_path)
    path = transcript(tmp_path)
    latest = path.read_bytes()
    path.write_bytes(b"x" * (256 * 1024 + 13) + b"\n" + latest)
    assert api().capture(str(path), time.monotonic() + 1)[0]["goal"] == VALUES[0]
    path.write_bytes(latest + json.dumps(record("x" * 66000)).encode("utf-8") + b"\n")
    assert api().capture(str(path), time.monotonic() + 1)[0] is None
    path.write_bytes(latest + b'{"type":"assistant"')
    assert api().capture(str(path), time.monotonic() + 1)[0] is None


def test_extraction_deadline_and_abandoned_reader_have_no_publication(tmp_path, monkeypatch):
    opt_in(monkeypatch, tmp_path, real_budget=True)
    path = transcript(tmp_path)
    finished = threading.Event()
    original = api()._read_latest
    def slow(*args):
        time.sleep(0.27)
        try:
            return original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(api(), "_read_latest", slow)
    start = time.monotonic()
    checkpoint, code = api().capture(str(path), start + 1)
    assert checkpoint is None and code == "capture-timeout"
    assert finished.wait(10)
    assert not spool.spool_dir().exists()
    assert api().capture(str(path), time.monotonic() + 0.52) == (None, "capture-timeout")


@pytest.mark.parametrize("secret", [
    "Bearer: " + "sample" * 4, "person" + "@example.invalid", "sk-" + "sample" * 6,
    "ｔｏｋｅｎ：" + "sample" * 4, "to\u200bken: " + "sample" * 4,
    "Password: " + "sample" * 5, "Cookie: " + "sample" * 5,
    "AbCd" * 6 + "1234", "1234567890abcdef" * 3,
    "138" + "00138000", "https://example.invalid?token=" + "sample" * 4,
    "postgresql://sample:sample@example.invalid/db", "to\u2062ken: " + "sample" * 4,
])
def test_privacy_before_size_and_across_every_persisted_surface(tmp_path, monkeypatch, secret):
    opt_in(monkeypatch, tmp_path)
    path = transcript(tmp_path, [record(block((secret, *VALUES[1:])))])
    queued = produce(monkeypatch, path, tmp_path)
    task = spool._read_event(queued)["payload"].get("task_checkpoint")
    if "\u2062" in secret:
        assert task is None  # unsupported invisible characters refuse the block
    else:
        assert task["goal"] is None and task["omissions"]["goal"] == "privacy"
        assert all(task[key] == value for key, value in zip(FIELDS[1:], VALUES[1:]))
    assert spool.drain()["processed"] == 1
    root = spool.store_root()
    from piia_engram.core import Engram
    output = Engram(root=root).get_resume_brief(project_folder=str(tmp_path))["markdown"]
    assert secret not in output
    for candidate in root.parent.rglob("*"):
        if candidate != path and candidate.is_file() and not candidate.name.endswith(".lock"):
            assert secret.encode("utf-8") not in candidate.read_bytes(), candidate.name


def test_cross_field_privacy_and_preparation_recheck(tmp_path, monkeypatch):
    assert api().parse_text(block(("token:", "samplecredentialvalue", *VALUES[2:])))[0] is None
    assert api().parse_text(block(("person@", "example.invalid", *VALUES[2:])))[0] is None
    assert api().parse_text(block(("to", "ken: samplecredentialvalue", *VALUES[2:])))[0] is None
    reordered = "\n".join(("Next: token:", "Goal: samplecredentialvalue",
                           "Constraint: Keep sample invariant", "Risk: Await sample input",
                           "Last failure reason: Sample assertion failed"))
    assert api().parse_text(reordered)[0] is None
    oversized = api().parse_text(block(("x" * 241 + " token: samplecredentialvalue", *VALUES[1:])))[0]
    assert oversized["goal"] is None and oversized["omissions"]["goal"] == "privacy"
    opt_in(monkeypatch, tmp_path)
    queued = produce(monkeypatch, transcript(tmp_path), tmp_path)
    event = spool._read_event(queued)
    for change in ({"goal": "token: samplecredentialvalue"}, {"unexpected": "sample"}, {"capture_event": "forged"}):
        forged = copy.deepcopy(event)
        forged["payload"]["task_checkpoint"].update(change)
        with pytest.raises(ValueError):
            _processor.prepare(forged, spool.store_root())


def test_prepared_context_cannot_bypass_task_guard(tmp_path, monkeypatch):
    opt_in(monkeypatch, tmp_path)
    event = spool._read_event(produce(monkeypatch, transcript(tmp_path), tmp_path))
    event["prepared"] = _processor.prepare(event, spool.store_root())
    event["prepared"]["context"] += "\ntoken: samplecredentialvalue"
    with pytest.raises(ValueError):
        _processor.validate_payload(event)


def test_incomplete_final_value_and_multiple_aliases_are_ambiguous():
    for text in (block() + "\nexcept when the sample condition changes",
                 "Example:\n" + block(), block() + "\n目标: Another task",
                 block().replace("Constraint:", "Goal:"), block().replace("Goal:", "Go\u200bal:"),
                 block().replace("Goal:", "Ｇｏａｌ:")):
        assert api().parse_text(text)[0] is None


@pytest.mark.parametrize("last", [
    record(block(), message={"role": None, "content": []}),
    {"type": None, "message": {"role": "assistant", "content": [{"type": "text", "text": block()}]}},
    {"role": "assistant", "content": [{"type": "text", "text": block()}]},
])
def test_null_or_unsupported_roles_cannot_rescue_an_old_task(tmp_path, monkeypatch, last):
    opt_in(monkeypatch, tmp_path)
    path = transcript(tmp_path, [record(block()), last])
    assert api().capture(str(path), time.monotonic() + 1)[0] is None


def test_duplicate_json_keys_are_ambiguous(tmp_path, monkeypatch):
    opt_in(monkeypatch, tmp_path)
    path = transcript(tmp_path)
    raw = json.dumps(record(block(), "top"))
    path.write_text(raw[:-1] + ', "type": "assistant"}\n', encoding="utf-8")
    assert api().capture(str(path), time.monotonic() + 1)[0] is None


def test_text_blocks_only_and_ignored_roles_do_not_reach_archive(tmp_path, monkeypatch):
    opt_in(monkeypatch, tmp_path)
    canary = "token: " + "ignoredsample" * 3
    entries = [{"type": "user", "content": [{"type": "text", "text": canary}]},
               record(block())]
    entries[-1]["message"]["content"] += [
        {"type": "thinking", "thinking": canary},
        {"type": "tool_use", "input": {"text": canary}},
        {"type": "tool_result", "content": canary}]
    path = transcript(tmp_path, entries)
    produce(monkeypatch, path, tmp_path)
    assert spool.drain()["processed"] == 1
    for candidate in spool.store_root().rglob("*"):
        if candidate.is_file() and not candidate.name.endswith(".lock"):
            assert canary.encode("utf-8") not in candidate.read_bytes()
    assert list((spool.store_root() / "contexts").rglob("*.digest.json"))


def test_missing_source_legacy_event_keeps_retry_contract(tmp_path):
    assert spool.enqueue("claude_stop", "claude_code", {"transcript_path": str(tmp_path / "missing.jsonl")})
    result = spool.drain()
    assert result["pending"] == result["failed"] == 1
    assert result["processed"] == 0


def test_replacement_truncation_and_opened_path_guard(tmp_path, monkeypatch):
    opt_in(monkeypatch, tmp_path)
    path = transcript(tmp_path)
    original = api()._opened_path
    def truncate(fd, expected):
        original(fd, expected)
        path.write_bytes(b"\n")
    monkeypatch.setattr(api(), "_opened_path", truncate)
    assert api().capture(str(path), time.monotonic() + 1)[1] == "source-changed"
    transcript(tmp_path)
    def replace(fd, expected):
        original(fd, expected)
        replacement = tmp_path / "replacement.jsonl"
        replacement.write_text(json.dumps(record(block())) + "\n", encoding="utf-8")
        os.replace(replacement, path)
    monkeypatch.setattr(api(), "_opened_path", replace)
    assert api().capture(str(path), time.monotonic() + 1)[0] is None


def test_symlink_or_junction_escape_is_refused(tmp_path, monkeypatch):
    import subprocess
    allowed, outside = tmp_path / "allowed", tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    transcript(outside)
    link = allowed / "link"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0
    else:
        link.symlink_to(outside, target_is_directory=True)
    opt_in(monkeypatch, allowed)
    assert api().capture(str(link / "transcript.jsonl"), time.monotonic() + 1)[0] is None


def test_exact_tail_and_record_byte_boundaries(tmp_path, monkeypatch):
    opt_in(monkeypatch, tmp_path)
    path = transcript(tmp_path)
    latest = path.read_bytes()
    # An incomplete leading record is discarded; a complete latest record survives.
    for padding in (api().MAX_TAIL_BYTES - len(latest), api().MAX_TAIL_BYTES + 1):
        path.write_bytes(b" " * padding + b"\n" + latest)
        assert api().capture(str(path), time.monotonic() + 1)[0] is not None
    value = record(block())
    raw = json.dumps(value).encode("utf-8")
    value["padding"] = " " * (api().MAX_RECORD_BYTES - len(raw) - len(', "padding": ""'))
    raw = json.dumps(value).encode("utf-8")
    assert len(raw) == api().MAX_RECORD_BYTES
    path.write_bytes(raw + b"\n")
    assert api().capture(str(path), time.monotonic() + 1)[0] is not None
    path.write_bytes(raw[:-1] + b' }\n')
    assert api().capture(str(path), time.monotonic() + 1)[1] == "record-too-large"


def test_object_metadata_nulls_and_sizes_are_validated():
    task = api().parse_text(block())[0]
    for update in ({"unexpected": "value"}, {"goal": "长" * 241}, {"goal": None},
                   {"captured_at": "not-time"}, {"captured_at": "2026-01-01"},
                   {"captured_at": "2026-01-01T00:00:00." + "138" + "00138000+00:00"},
                   {"captured_at": "2026-01-01X00:00:00+00:00"},
                   {"capture_event": "PostCompact"}, {"omissions": {"goal": "private prose"}}):
        with pytest.raises((ValueError, TypeError)):
            api().validate(dict(task, **update))
    assert len(json.dumps(task, ensure_ascii=False).encode("utf-8")) <= api().MAX_OBJECT_BYTES


def test_content_digest_opt_in_remains_independent(tmp_path, monkeypatch):
    opt_in(monkeypatch, tmp_path)
    path = transcript(tmp_path, [record(block())] * 12)
    queued = produce(monkeypatch, path, tmp_path)
    from piia_engram.core import Engram
    engram = Engram(root=spool.store_root())
    monkeypatch.setattr(engram, "get_preferences", lambda: {"hook_content_digest_v2": True})
    event = spool._read_event(queued)
    prepared = _processor.prepare(event, spool.store_root(), engram)
    assert prepared["summary"] == "" and not prepared["digest"]
    assert prepared["independent_content_digest"]
    assert prepared["context"] == api().render(prepared["task_checkpoint"])
    assert spool.drain(engram=engram)["processed"] == 1
    # Independent input loss cannot gate processing the captured task.
    queued.write_bytes(spool._json_bytes(dict(event, event_id="f" * 32)))
    path.unlink()
    assert spool.drain(engram=engram)["processed"] == 1


def test_task_body_never_enters_heuristic_digest_or_knowledge(tmp_path, monkeypatch):
    opt_in(monkeypatch, tmp_path)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    values = ("Complete the sample task", "Always validate because it prevents sample errors",
              "Await sample approval", "A sample check failed", "Run the sample check")
    produce(monkeypatch, transcript(tmp_path, [record(block(values))]), tmp_path)
    from piia_engram.core import Engram
    engram = Engram(root=spool.store_root())
    builder = engram._build_checkpoint_digest
    def check_empty(body, **kwargs):
        assert body == ""
        return builder(body, **kwargs)
    monkeypatch.setattr(engram, "_build_checkpoint_digest", check_empty)
    assert spool.drain(engram=engram)["processed"] == 1
    digest = json.loads(next((spool.store_root() / "contexts").rglob("*.digest.json")).read_text(encoding="utf-8"))
    assert digest["decisions"] == digest["lessons"] == digest["completed"] == []
    for path in (spool.store_root() / "knowledge").glob("*.json"):
        assert all(value not in path.read_text(encoding="utf-8") for value in values)
    assert all(value not in engram.generate_context(level="full") for value in values)


def test_task_revision_is_frozen_and_newer_canonical_state_wins(tmp_path, monkeypatch):
    opt_in(monkeypatch, tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    from piia_engram.core import Engram
    engram = Engram(root=spool.store_root())
    engram.save_project_snapshot(str(project), {"current_state": {"current_focus": "Initial sample state"}})
    queued = produce(monkeypatch, transcript(tmp_path), project)
    original = spool._publish
    def interrupt(destination, data):
        if destination.suffix == ".md":
            raise OSError("synthetic interruption")
        return original(destination, data)
    monkeypatch.setattr(spool, "_publish", interrupt)
    assert spool.drain(engram=engram)["failed"] == 1
    frozen = spool._read_event(queued)["prepared"]
    assert frozen["project_revision"] == 1
    engram.save_project_snapshot(str(project), {"current_state": {"current_focus": "New canonical sample state"}})
    monkeypatch.setattr(spool, "_publish", original)
    assert spool.drain(engram=engram)["processed"] == 1
    digest = json.loads(next((spool.store_root() / "contexts").rglob("*.digest.json")).read_text(encoding="utf-8"))
    assert digest["source"]["project_revision"] == 1
    assert digest["source"]["project_revision_captured_at"] == frozen["project_revision_captured_at"]
    brief = engram.get_resume_brief(project_folder=str(project))
    assert brief["freshness"]["authoritative_source"] == "project_checkpoint"
    assert brief["handoff_meta"]["current_focus"] == "New canonical sample state"


def test_existing_mcp_owner_gate_and_direct_hook_projection(tmp_path, monkeypatch):
    import asyncio
    opt_in(monkeypatch, tmp_path)
    produce(monkeypatch, transcript(tmp_path), tmp_path)
    assert spool.drain()["processed"] == 1
    from piia_engram.core import Engram
    engram = Engram(root=spool.store_root())
    monkeypatch.setattr(mcp_server, "_get_engram", lambda: engram)
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    monkeypatch.setattr(mcp_server._gov_rt, "caller_is_owner", lambda root: False)
    result = asyncio.run(mcp_server.get_resume_brief(project_folder=str(tmp_path)))
    assert json.loads(result)["governance_withheld"] is True
    assert all(value not in result for value in VALUES)
    direct = engram.get_resume_brief(project_folder=str(tmp_path))
    assert VALUES[0] in direct["markdown"]
    assert "unreviewed; no approval or action authority" in direct["markdown"]


@pytest.mark.parametrize("interruption", ["archive", "digest"])
def test_interrupted_publications_keep_only_frozen_safe_content(tmp_path, monkeypatch, interruption):
    opt_in(monkeypatch, tmp_path)
    canary = "token: " + "ignoredsample" * 3
    entry = record(block())
    entry["message"]["content"].insert(0, {"type": "tool_result", "content": canary})
    path = transcript(tmp_path, [entry])
    queued = produce(monkeypatch, path, tmp_path)
    from piia_engram import storage
    real_publish, real_update = spool._publish, storage._update_json
    if interruption == "archive":
        def partial(destination, data):
            if destination.suffix == ".md":
                destination.with_suffix(".partial").write_bytes(data)
                raise OSError("synthetic publication interruption")
            return real_publish(destination, data)
        monkeypatch.setattr(spool, "_publish", partial)
    else:
        def partial_json(destination, updater, *args, **kwargs):
            if destination.name.endswith(".digest.json"):
                destination.with_suffix(".partial").write_text(
                    json.dumps(updater({}), ensure_ascii=False), encoding="utf-8")
                raise OSError("synthetic digest interruption")
            return real_update(destination, updater, *args, **kwargs)
        monkeypatch.setattr(storage, "_update_json", partial_json)
    assert spool.drain()["failed"] == 1
    assert spool._read_event(queued)["prepared"]["task_checkpoint"]["goal"] == VALUES[0]
    path.unlink()
    monkeypatch.setattr(spool, "_publish", real_publish)
    monkeypatch.setattr(storage, "_update_json", real_update)
    assert spool.drain()["processed"] == 1
    assert list(spool.store_root().rglob("*.partial"))
    for candidate in spool.store_root().rglob("*"):
        if candidate.is_file() and not candidate.name.endswith(".lock"):
            assert canary.encode("utf-8") not in candidate.read_bytes()


@pytest.fixture
def protected_task(tmp_path, monkeypatch):
    import hashlib
    from piia_engram import crypto
    from piia_engram.hooks import _spool_protection as protection
    opt_in(monkeypatch, tmp_path)
    root = spool.store_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / ".corpus_salt").write_bytes(b"0123456789abcdef")
    monkeypatch.setenv("ENGRAM_SECRET", "synthetic-checkpoint-key")
    monkeypatch.setattr(crypto, "_derive_key", lambda secret, salt, iterations:
                        hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 1))
    monkeypatch.setattr(protection, "CRYPTO_BUDGET_SECONDS", 30)
    monkeypatch.setattr(_producer, "INPUT_BUDGET_SECONDS", 35)
    return root, protection


@pytest.mark.parametrize("key", ["", "wrong-key"])
def test_w2b_missing_or_wrong_key_preserves_ciphertext(protected_task, tmp_path, monkeypatch, key):
    root, protection = protected_task
    queued = produce(monkeypatch, transcript(tmp_path), tmp_path)
    original = queued.read_bytes()
    assert json.loads(original)["schema"] == 2
    assert VALUES[0].encode("utf-8") not in original
    monkeypatch.setenv("ENGRAM_SECRET", key)
    result = spool.drain()
    assert result["failed"] == result["pending"] == 1
    assert queued.read_bytes() == original
    assert not (root / "contexts").exists()
    monkeypatch.setenv("ENGRAM_SECRET", "synthetic-checkpoint-key")
    assert spool.drain()["processed"] == 1


@pytest.mark.parametrize("fault", ["missing-key", "slow-kdf", "sealing-failure"])
def test_w2b_failed_capture_has_no_plaintext_or_late_publication(protected_task, tmp_path, monkeypatch, fault):
    from piia_engram import crypto
    root, protection = protected_task
    original = crypto.EncryptionEngine.derive_corpus_key
    done = threading.Event()
    if fault == "missing-key":
        monkeypatch.setenv("ENGRAM_SECRET", "")
    elif fault == "sealing-failure":
        monkeypatch.setattr(crypto.EncryptionEngine, "corpus_encrypt", lambda *a: (_ for _ in ()).throw(ValueError("fault")))
    else:
        def slow(*args):
            time.sleep(0.25)
            try:
                return original(*args)
            finally:
                done.set()
        monkeypatch.setattr(crypto.EncryptionEngine, "derive_corpus_key", slow)
        monkeypatch.setattr(protection, "CRYPTO_BUDGET_SECONDS", 0.08)
    path = transcript(tmp_path)
    monkeypatch.setattr(_producer, "_read_input", lambda deadline: {"transcript_path": str(path)})
    assert _producer.claude_event("claude_stop") == 0
    if fault == "slow-kdf":
        assert done.wait(2)
    assert not list(spool.spool_dir().glob("*.jsonl"))
    for candidate in root.rglob("*"):
        if candidate.is_file() and not candidate.name.endswith(".lock"):
            assert VALUES[0].encode("utf-8") not in candidate.read_bytes()
