"""M2A: safe legacy session digest backfill."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from piia_engram.core import Engram


_FAKE_KEY = "sk-" + "ABCDEF1234567890abcdef"


def _eng(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


def _snapshot(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for path in root.rglob("*"):
        if path.is_file():
            out[str(path.relative_to(root)).replace("\\", "/")] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return out


def _write_legacy_session(
    eng: Engram,
    *,
    tool: str = "codex",
    session_id: str = "legacy-1",
    body: str,
    project_folder: str = "",
) -> Path:
    tool_dir = eng.root / "contexts" / tool
    tool_dir.mkdir(parents=True, exist_ok=True)
    header = f"# Session: {tool} @ 2026-06-24 10:00\n"
    if project_folder:
        header += f"## Project: {project_folder}\n"
    path = tool_dir / f"{session_id}.md"
    path.write_text(header + "\n### 10:00\n" + body + "\n", encoding="utf-8")
    return path


def test_preview_does_not_create_digest_sidecar(tmp_path: Path):
    eng = _eng(tmp_path)
    _write_legacy_session(
        eng,
        body="Goal: finish handoff.\nCompleted: added digest.\nNext: test backfill.\n",
    )

    preview = eng.preview_session_digest_backfill(tool="codex")

    assert preview["schema"] == "session_digest_backfill.v1"
    assert preview["mode"] == "preview"
    assert preview["candidates"] == 1
    assert preview["written"] == 0
    assert preview["items"][0]["would_write"] is True
    assert not (eng.root / "contexts" / "codex" / "legacy-1.digest.json").exists()


def test_apply_refuses_without_owner_confirmation(tmp_path: Path):
    eng = _eng(tmp_path)
    _write_legacy_session(
        eng,
        body="Goal: finish handoff.\nCompleted: added digest.\nNext: test backfill.\n",
    )

    result = eng.apply_session_digest_backfill(tool="codex", yes=False)

    assert result["mode"] == "apply"
    assert result["candidates"] == 1
    assert result["written"] == 0
    assert any(item["reason"] == "requires_yes" for item in result["skipped"])
    assert not (eng.root / "contexts" / "codex" / "legacy-1.digest.json").exists()


def test_apply_writes_digest_for_meaningful_legacy_session(tmp_path: Path):
    eng = _eng(tmp_path)
    _write_legacy_session(
        eng,
        body=(
            "Goal: finish handoff.\n"
            "Completed: added digest backfill.\n"
            "Tests: pytest passed.\n"
            "Next: add resume quality metadata.\n"
        ),
    )

    result = eng.apply_session_digest_backfill(tool="codex", yes=True)

    digest_path = eng.root / "contexts" / "codex" / "legacy-1.digest.json"
    assert result["written"] == 1
    assert result["items"][0]["session_id"] == "legacy-1"
    assert digest_path.exists()
    digest = json.loads(digest_path.read_text(encoding="utf-8"))
    assert digest["schema"] == "session_digest.v1"
    assert "digest backfill" in json.dumps(digest, ensure_ascii=False)


def test_apply_skips_trivial_legacy_session(tmp_path: Path):
    eng = _eng(tmp_path)
    _write_legacy_session(eng, body="hello")

    result = eng.apply_session_digest_backfill(tool="codex", yes=True)

    assert result["candidates"] == 0
    assert result["written"] == 0
    assert any(item["reason"] == "no_session_signal" for item in result["skipped"])
    assert not (eng.root / "contexts" / "codex" / "legacy-1.digest.json").exists()


def test_apply_is_idempotent(tmp_path: Path):
    eng = _eng(tmp_path)
    _write_legacy_session(
        eng,
        body="Goal: finish handoff.\nCompleted: added digest.\nNext: test backfill.\n",
    )

    first = eng.apply_session_digest_backfill(tool="codex", yes=True)
    second = eng.apply_session_digest_backfill(tool="codex", yes=True)

    assert first["written"] == 1
    assert second["written"] == 0
    assert any(item["reason"] == "already_has_digest" for item in second["skipped"])


def test_backfill_output_redacts_paths_and_fake_keys(tmp_path: Path):
    eng = _eng(tmp_path)
    _write_legacy_session(
        eng,
        session_id="legacy-secret",
        body=(
            f"Goal: rotate {_FAKE_KEY}.\n"
            "Completed: inspected E:\\Private\\store.db.\n"
            "Next: continue safely.\n"
        ),
    )

    result = eng.apply_session_digest_backfill(tool="codex", yes=True)
    blob = json.dumps(result, ensure_ascii=False)
    digest_blob = (eng.root / "contexts" / "codex" / "legacy-secret.digest.json").read_text(
        encoding="utf-8"
    )

    assert _FAKE_KEY not in blob
    assert _FAKE_KEY not in digest_blob
    assert "E:\\Private\\store.db" not in blob
    assert "E:\\Private\\store.db" not in digest_blob
    assert "content" not in blob
    assert str(tmp_path) not in blob


def test_corrupted_session_file_is_skipped_with_metadata_only_reason(tmp_path: Path):
    eng = _eng(tmp_path)
    tool_dir = eng.root / "contexts" / "codex"
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "bad.md").write_bytes(b"\xff\xfe\x00\x00")

    result = eng.preview_session_digest_backfill(tool="codex")

    assert result["candidates"] == 0
    assert result["written"] == 0
    assert any(item["reason"] == "read_error" for item in result["skipped"])
    blob = json.dumps(result, ensure_ascii=False)
    assert str(tool_dir) not in blob
    assert "bad.md" not in blob


def test_project_filter_only_includes_matching_header(tmp_path: Path):
    eng = _eng(tmp_path)
    project = tmp_path / "wanted"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()
    _write_legacy_session(
        eng,
        session_id="wanted",
        body="Goal: wanted.\nCompleted: did wanted work.\n",
        project_folder=str(project),
    )
    _write_legacy_session(
        eng,
        session_id="other",
        body="Goal: other.\nCompleted: did other work.\n",
        project_folder=str(other),
    )

    result = eng.preview_session_digest_backfill(tool="codex", project_folder=str(project))

    assert result["candidates"] == 1
    assert [item["session_id"] for item in result["items"]] == ["wanted"]
    assert any(item["reason"] == "project_mismatch" for item in result["skipped"])


def test_continuity_cli_digest_backfill_preview_json(tmp_path: Path, monkeypatch, capsys):
    from piia_engram.setup_wizard import run_continuity

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = Engram()
    _write_legacy_session(
        eng,
        body="Goal: finish handoff.\nCompleted: added digest.\nNext: test backfill.\n",
        project_folder=str(tmp_path),
    )

    assert run_continuity(["--digest-backfill-preview", "--json", "--project", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["schema"] == "session_digest_backfill.v1"
    assert payload["mode"] == "preview"
    assert payload["candidates"] == 1
    assert "content" not in out


def test_continuity_cli_digest_backfill_preview_is_zero_write(
    tmp_path: Path, monkeypatch, capsys
):
    from piia_engram.setup_wizard import run_continuity

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    _write_legacy_session(
        Engram(root=tmp_path, read_only=True),
        body="Goal: finish handoff.\nCompleted: added digest.\nNext: test backfill.\n",
        project_folder=str(tmp_path),
    )
    before = _snapshot(tmp_path)

    assert run_continuity(["--digest-backfill-preview", "--json", "--project", str(tmp_path)]) == 0

    assert json.loads(capsys.readouterr().out)["candidates"] == 1
    assert _snapshot(tmp_path) == before


def test_continuity_cli_digest_backfill_apply_without_yes_fails_closed(
    tmp_path: Path, monkeypatch, capsys
):
    from piia_engram.setup_wizard import run_continuity

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    _write_legacy_session(
        Engram(root=tmp_path, read_only=True),
        body="Goal: finish handoff.\nCompleted: added digest.\nNext: test backfill.\n",
        project_folder=str(tmp_path),
    )
    before = _snapshot(tmp_path)

    assert run_continuity(["--digest-backfill-apply", "--json", "--project", str(tmp_path)]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "apply"
    assert payload["written"] == 0
    assert any(item["reason"] == "requires_yes" for item in payload["skipped"])
    assert _snapshot(tmp_path) == before
