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

from piia_engram.hooks import _producer, _processor, spool

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


def opt_in(monkeypatch, root):
    monkeypatch.setenv("ENGRAM_HOOK_TASK_CHECKPOINT_V1", "1")
    monkeypatch.setenv("ENGRAM_HOOK_TRANSCRIPT_ROOTS", json.dumps([str(root)]))


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
    opt_in(monkeypatch, tmp_path)
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
    assert time.monotonic() - start < 0.26
    assert finished.wait(1)
    assert not spool.spool_dir().exists()
    assert api().capture(str(path), time.monotonic() + 0.52) == (None, "capture-timeout")


@pytest.mark.parametrize("secret", [
    "Bearer: " + "sample" * 4, "person" + "@example.invalid", "sk-" + "sample" * 6,
    "ｔｏｋｅｎ：" + "sample" * 4, "to\u200bken: " + "sample" * 4,
    "Password: " + "sample" * 5, "Cookie: " + "sample" * 5,
    "AbCd" * 6 + "1234", "1234567890abcdef" * 3,
])
def test_privacy_before_size_and_across_every_persisted_surface(tmp_path, monkeypatch, secret):
    opt_in(monkeypatch, tmp_path)
    path = transcript(tmp_path, [record(block((secret, *VALUES[1:])))])
    queued = produce(monkeypatch, path, tmp_path)
    assert "task_checkpoint" not in spool._read_event(queued)["payload"]
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
    assert api().parse_text(block(("x" * 241 + " token: samplecredentialvalue", *VALUES[1:])))[0] is None
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
