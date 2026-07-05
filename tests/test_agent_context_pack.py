from __future__ import annotations

import copy
import threading
from pathlib import Path

from piia_engram.core import Engram


def _eng(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


def _summaries(items: list[dict]) -> list[str]:
    return [str(item.get("summary") or item.get("title") or "") for item in items]


def _file_bytes_snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_agent_context_pack_schema_and_zero_write(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    eng = _eng(tmp_path)
    project = tmp_path / "project-a"
    project.mkdir()
    eng.save_project_snapshot(str(project), {"title": "Synthetic Project", "stage": "M6"})
    eng.add_decision({
        "question": "How should writes be handled?",
        "choice": "preview first",
        "project_folder": str(project),
        "tier": "verified",
    })

    before = _file_bytes_snapshot(tmp_path)
    pack = eng.build_agent_context_pack(
        project_folder=str(project),
        agent_role="reviewer",
        task_summary="Review memory write gate changes",
    )
    after = _file_bytes_snapshot(tmp_path)

    assert pack["schema"] == "agent_context_pack.v1"
    assert pack["role"] == "reviewer"
    assert pack["pack_meta"]["source_schema"] == "project_resume_pack.v1"
    assert pack["project"]["title"] == "Synthetic Project"
    assert pack["task"]["summary"] == "Review memory write gate changes"
    assert "preview first" in " ".join(_summaries(pack["context"]["trusted"]))
    assert before == after
    assert b'"action": "read"' not in after.get("audit.log", b"")


def test_agent_context_pack_bounds_long_task_summary(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    project = tmp_path / "project-a"
    project.mkdir()
    tail = "LONG_TAIL_MARKER_SHOULD_NOT_APPEAR"
    long_summary = f"{'A' * 360}{tail}{'B' * 180}"

    pack = eng.build_agent_context_pack(
        project_folder=str(project),
        agent_role="implementer",
        task_summary=long_summary,
    )

    assert len(pack["task"]["summary"]) <= 300
    assert len(pack["focus"]["current"]) <= 300
    assert tail not in pack["task"]["summary"]
    assert tail not in pack["focus"]["current"]


def test_agent_context_pack_uses_resume_meta_omitted_count(tmp_path: Path) -> None:
    eng = _eng(tmp_path)

    def fake_resume_pack(**_: object) -> dict:
        return {
            "schema": "project_resume_pack.v1",
            "project": {},
            "handoff": {},
            "trusted_context": [],
            "review_needed": [],
            "omitted": [],
            "pack_meta": {"omitted_count": 7},
            "safety_notes": [],
        }

    eng.build_project_resume_pack = fake_resume_pack  # type: ignore[method-assign]

    pack = eng.build_agent_context_pack(
        project_folder=str(tmp_path / "project-a"),
        agent_role="reviewer",
        task_summary="Review omitted accounting",
    )

    assert pack["pack_meta"]["counts"]["omitted"] == 7


def test_agent_context_pack_excludes_other_project_memory(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    eng.add_lesson({
        "summary": "Project A implementation rule",
        "project_folder": str(project_a),
        "tier": "verified",
    })
    eng.add_lesson({
        "summary": "Project B private rule",
        "project_folder": str(project_b),
        "tier": "verified",
    })
    eng.add_lesson({"summary": "Reusable global rule", "tier": "verified"})

    pack = eng.build_agent_context_pack(
        project_folder=str(project_a),
        agent_role="implementer",
        task_summary="Implement the project rule",
    )
    body = repr(pack)

    assert "Project A implementation rule" in body
    assert "Reusable global rule" in body
    assert "Project B private rule" not in body


def test_agent_context_pack_excludes_archived_knowledge(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    project = tmp_path / "project-a"
    project.mkdir()
    lesson = eng.add_lesson({
        "summary": "Archived project lesson must not be trusted",
        "project_folder": str(project),
        "tier": "verified",
    })
    decision = eng.add_decision({
        "question": "Archived project decision",
        "choice": "must not be trusted",
        "project_folder": str(project),
        "tier": "verified",
    })
    assert eng.soft_archive_knowledge_tier(lesson["id"], allow_verified=True)["changed"] is True
    assert eng.soft_archive_knowledge_tier(decision["id"], allow_verified=True)["changed"] is True

    pack = eng.build_agent_context_pack(
        project_folder=str(project),
        agent_role="reviewer",
        task_summary="Review archived memory filtering",
    )
    body = repr(pack)

    assert "Archived project lesson must not be trusted" not in body
    assert "Archived project decision" not in body


def test_agent_context_pack_role_slices_review_needed(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    project = tmp_path / "project-a"
    project.mkdir()
    eng.save_agent_context(
        tool="codex",
        session_id="s1",
        project_folder=str(project),
        content=(
            "Goal: finish write gate.\n"
            "Decided to keep preview-first write confirmation.\n"
            "Next: review governance ack.\n"
        ),
    )

    reviewer = eng.build_agent_context_pack(
        project_folder=str(project),
        agent_role="reviewer",
        task_summary="Review governance ack",
    )
    implementer = eng.build_agent_context_pack(
        project_folder=str(project),
        agent_role="implementer",
        task_summary="Implement governance ack",
    )

    assert reviewer["context"]["review_needed"]
    assert all(
        item["reason"] == "candidate_not_trusted"
        for item in reviewer["context"]["review_needed"]
    )
    trusted_summaries = set(_summaries(reviewer["context"]["trusted"]))
    candidate_summaries = set(_summaries(reviewer["context"]["review_needed"]))
    assert candidate_summaries.isdisjoint(trusted_summaries)
    assert implementer["context"]["review_needed"] == []
    assert any("Prioritize risks" in item for item in reviewer["role_guidance"])
    assert any("Prefer implementation" in item for item in implementer["role_guidance"])


def test_agent_context_pack_unknown_role_falls_back_to_orchestrator(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    project = tmp_path / "project-a"
    project.mkdir()

    pack = eng.build_agent_context_pack(
        project_folder=str(project),
        agent_role="external-specialist",
        task_summary="Coordinate delegated work",
    )

    assert pack["role"] == "orchestrator"
    assert any("sub-agents" in item for item in pack["role_guidance"])


def test_agent_context_pack_is_deterministic(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    project = tmp_path / "project-a"
    project.mkdir()
    eng.add_lesson({"summary": "Use focused tests", "project_folder": str(project), "tier": "verified"})
    eng.add_decision({"question": "Test style", "choice": "focused first", "project_folder": str(project), "tier": "verified"})

    first = eng.build_agent_context_pack(
        project_folder=str(project),
        agent_role="tester",
        task_summary="Run focused tests",
    )
    second = eng.build_agent_context_pack(
        project_folder=str(project),
        agent_role="tester",
        task_summary="Run focused tests",
    )

    assert first == second
    assert first == copy.deepcopy(second)


def test_agent_context_pack_redacts_paths_and_key_shapes(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    project = tmp_path / "secret-project"
    project.mkdir()
    fake_path = "C:/Users/alice/private/project"
    fake_key = "sk-" + "a" * 32
    eng.add_lesson({
        "summary": f"Never expose {fake_path} or {fake_key}",
        "project_folder": str(project),
        "tier": "verified",
    })

    pack = eng.build_agent_context_pack(
        project_folder=str(project),
        agent_role="researcher",
        task_summary=f"Research {fake_path}",
    )
    text = repr(pack)

    assert fake_path not in text
    assert fake_key not in text
    assert str(project) not in text


def test_agent_context_pack_task_keywords_are_sanitized(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    project = tmp_path / "secret-project"
    project.mkdir()
    fake_path = "C:/Users/alice/private/project"
    fake_key = "sk-" + "b" * 32

    pack = eng.build_agent_context_pack(
        project_folder=str(project),
        agent_role="reviewer",
        task_summary=f"Review {fake_path} with token {fake_key}",
    )
    text = repr(pack)

    assert fake_path not in text
    assert fake_key not in text
    assert "alice" not in pack["task"]["keywords"]
    assert "users" not in pack["task"]["keywords"]
    assert "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" not in pack["task"]["keywords"]


def test_agent_context_pack_bounds_selected_source(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    long_source = "source-" + ("x" * 300)

    def fake_resume_pack(**_: object) -> dict:
        return {
            "schema": "project_resume_pack.v1",
            "project": {},
            "handoff": {},
            "trusted_context": [{
                "kind": "lesson",
                "summary": "Bound the source field",
                "source": long_source,
            }],
            "review_needed": [],
            "omitted": [],
            "pack_meta": {"omitted_count": 0},
            "safety_notes": [],
        }

    eng.build_project_resume_pack = fake_resume_pack  # type: ignore[method-assign]

    pack = eng.build_agent_context_pack(
        project_folder=str(tmp_path / "project-a"),
        agent_role="implementer",
        task_summary="Bound source metadata",
    )

    assert len(pack["context"]["trusted"][0]["source"]) <= 160
    assert pack["context"]["trusted"][0]["source"] == long_source[:160]


def test_agent_context_pack_redacts_before_bounding_sensitive_fragments(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    project = tmp_path / "secret-project"
    project.mkdir()
    split_task_key = ("x" * 297) + "sk-" + ("c" * 32)
    split_summary_key = ("s" * 237) + "sk-" + ("d" * 32)
    split_source_key = ("u" * 157) + "sk-" + ("e" * 32)

    def fake_resume_pack(**_: object) -> dict:
        return {
            "schema": "project_resume_pack.v1",
            "project": {},
            "handoff": {"current_focus": split_task_key},
            "trusted_context": [{
                "kind": "lesson",
                "summary": split_summary_key,
                "source": split_source_key,
            }],
            "review_needed": [],
            "omitted": [],
            "pack_meta": {"omitted_count": 0},
            "safety_notes": [],
        }

    eng.build_project_resume_pack = fake_resume_pack  # type: ignore[method-assign]

    pack = eng.build_agent_context_pack(
        project_folder=str(project),
        agent_role="implementer",
        task_summary=split_task_key,
    )
    text = repr(pack)

    assert "sk-" not in text
    assert len(pack["task"]["summary"]) <= 300
    assert len(pack["context"]["trusted"][0]["summary"]) <= 240
    assert len(pack["context"]["trusted"][0]["source"]) <= 160

    fallback_pack = eng.build_agent_context_pack(
        project_folder=str(project),
        agent_role="implementer",
        task_summary="",
    )

    assert "sk-" not in repr(fallback_pack)
    assert len(fallback_pack["focus"]["current"]) <= 300


def test_agent_context_pack_real_resume_path_redacts_before_resume_bounding(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    project = tmp_path / "secret-project"
    project.mkdir()
    split_summary_key = ("s" * 237) + "sk-" + ("f" * 32)
    eng.add_lesson({
        "summary": split_summary_key,
        "project_folder": str(project),
        "tier": "verified",
    })

    pack = eng.build_agent_context_pack(
        project_folder=str(project),
        agent_role="implementer",
        task_summary="Review real resume path",
    )
    text = repr(pack)

    assert "sk-" not in text
    assert pack["context"]["trusted"]
    assert len(pack["context"]["trusted"][0]["summary"]) <= 240


def test_agent_context_pack_does_not_suppress_concurrent_write_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    eng = _eng(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def fake_resume_pack(**_: object) -> dict:
        entered.set()
        assert release.wait(timeout=5)
        return {
            "schema": "project_resume_pack.v1",
            "project": {},
            "handoff": {},
            "trusted_context": [],
            "review_needed": [],
            "omitted": [],
            "pack_meta": {"omitted_count": 0},
            "safety_notes": [],
        }

    eng.build_project_resume_pack = fake_resume_pack  # type: ignore[method-assign]
    worker_error: list[BaseException] = []

    def build_pack() -> None:
        try:
            eng.build_agent_context_pack(
                project_folder=str(tmp_path / "project-a"),
                agent_role="reviewer",
                task_summary="Build agent context",
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            worker_error.append(exc)

    thread = threading.Thread(target=build_pack)
    thread.start()
    try:
        assert entered.wait(timeout=5)
        eng.add_decision({
            "question": "Concurrent audit write?",
            "choice": "record it",
            "tier": "verified",
        })
    finally:
        release.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert worker_error == []
    audit_text = (tmp_path / "audit.log").read_text(encoding="utf-8")
    assert "Concurrent audit write?" in audit_text
    assert '"action": "write"' in audit_text


def test_agent_context_pack_bounds_project_and_focus_lists(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    split_next_key = ("n" * 297) + "sk-" + ("g" * 32)
    split_block_key = ("b" * 297) + "sk-" + ("h" * 32)

    def fake_resume_pack(**_: object) -> dict:
        return {
            "schema": "project_resume_pack.v1",
            "project": {
                "title": "T" * 500,
                "stage": "S" * 400,
                "updated_at": "U" * 350,
            },
            "handoff": {
                "current_focus": "",
                "next_actions": [split_next_key],
                "blocked_on": [split_block_key],
            },
            "trusted_context": [],
            "review_needed": [],
            "omitted": [],
            "pack_meta": {"omitted_count": 0},
            "safety_notes": [],
        }

    eng.build_project_resume_pack = fake_resume_pack  # type: ignore[method-assign]

    pack = eng.build_agent_context_pack(
        project_folder=str(tmp_path / "project-a"),
        agent_role="orchestrator",
        task_summary="",
    )

    assert "sk-" not in repr(pack)
    assert len(pack["project"]["title"]) <= 300
    assert len(pack["project"]["stage"]) <= 300
    assert len(pack["project"]["updated_at"]) <= 300
    assert len(pack["focus"]["next_actions"][0]) <= 300
    assert len(pack["focus"]["blocked_on"][0]) <= 300
