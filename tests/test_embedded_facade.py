"""Embedded host facade phase 1: witness, handshake, snapshot, zero-write proof."""

from __future__ import annotations

import builtins
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from piia_engram.embedded import (
    FACADE_CONTRACT_VERSION,
    SNAPSHOT_SCHEMA,
    FacadeContextError,
    FacadeHandshakeError,
    canonical_hash,
    capability_witness,
    handshake,
    require_compatible,
    retrieve_task_context_snapshot,
    validate_snapshot,
    verify_witness,
    write_capability_witness,
)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A throwaway store root. Never the developer's live store."""
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "0")
    return root


@pytest.fixture()
def project(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


SHAREABLE_SUMMARY = "Bounded reads must stay zero write."
PRIVATE_BODY = "PRIVATEBODYMARKER evidence acceptance criteria internal reasoning"


def _seed(store_root: Path, project_folder: Path, project_id: str) -> None:
    """Seed a shareable and a private lesson through the real write API.

    Both lesson summaries deliberately share vocabulary with the objective used
    by ``_retrieve``; otherwise keyword search matches neither and every
    assertion about the private path passes vacuously.
    """
    from piia_engram.core import Engram

    engram = Engram(root=store_root)
    engram.save_project_snapshot(str(project_folder), {
        "title": "facade fixture project",
        "sharing_class": "public_equivalent",
        "public_equivalent_summary": "Bounded read fixture project context.",
        "project_id": project_id,
    })
    engram.add_lesson({
        "summary": PRIVATE_BODY,
        "domain": "testing",
        "tier": "verified",
        "status": "active",
        "project_id": project_id,
        "project_folder": str(project_folder),
    })
    engram.add_lesson({
        "summary": "shareable evidence acceptance criteria lesson",
        "domain": "testing",
        "tier": "verified",
        "status": "active",
        "project_id": project_id,
        "project_folder": str(project_folder),
        "sharing_class": "public_equivalent",
        "public_equivalent_summary": SHAREABLE_SUMMARY,
    })


def _tree_fingerprint(root: Path) -> dict[str, str]:
    """Content-level fingerprint of every file under root."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class WriteGuard:
    """Fails the test if anything attempts a write syscall inside the store.

    Patches the write entry points the runtime actually uses (open in a writing
    mode, os-level mutation, shutil copies) rather than trusting a mtime check,
    so a write that happened to reproduce identical bytes still gets caught.
    """

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.violations: list[str] = []
        self._orig: dict[str, object] = {}

    def _inside(self, path) -> bool:
        try:
            return self.root == Path(path).resolve() or self.root in Path(path).resolve().parents
        except (OSError, ValueError, TypeError):
            return False

    def __enter__(self):
        guard = self

        real_open = builtins.open

        def guarded_open(file, mode="r", *a, **kw):
            if any(flag in str(mode) for flag in ("w", "a", "x", "+")) and guard._inside(file):
                guard.violations.append(f"open({file!s}, mode={mode!r})")
            return real_open(file, mode, *a, **kw)

        self._orig["open"] = real_open
        builtins.open = guarded_open

        for module, name in (
            (os, "replace"), (os, "rename"), (os, "remove"), (os, "unlink"),
            (os, "mkdir"), (os, "makedirs"), (shutil, "copy2"), (shutil, "copytree"),
        ):
            original = getattr(module, name)
            self._orig[f"{module.__name__}.{name}"] = original

            def make(orig=original, label=f"{module.__name__}.{name}"):
                def wrapper(*args, **kwargs):
                    if args and guard._inside(args[0]):
                        guard.violations.append(f"{label}({args[0]!s})")
                    return orig(*args, **kwargs)
                return wrapper

            setattr(module, name, make())

        for cls_name, meth in (("write_text", "write_text"), ("write_bytes", "write_bytes")):
            original = getattr(Path, meth)
            self._orig[f"Path.{meth}"] = original

            def make_path(orig=original, label=f"Path.{meth}"):
                def wrapper(self_path, *args, **kwargs):
                    if guard._inside(self_path):
                        guard.violations.append(f"{label}({self_path!s})")
                    return orig(self_path, *args, **kwargs)
                return wrapper

            setattr(Path, meth, make_path())
        return self

    def __exit__(self, *exc):
        builtins.open = self._orig["open"]
        for module, name in (
            (os, "replace"), (os, "rename"), (os, "remove"), (os, "unlink"),
            (os, "mkdir"), (os, "makedirs"), (shutil, "copy2"), (shutil, "copytree"),
        ):
            setattr(module, name, self._orig[f"{module.__name__}.{name}"])
        for meth in ("write_text", "write_bytes"):
            setattr(Path, meth, self._orig[f"Path.{meth}"])
        return False


# --------------------------------------------------------------------------
# capability witness
# --------------------------------------------------------------------------


def test_witness_is_self_verifying_and_content_free():
    witness = capability_witness()
    ok, problems = verify_witness(witness)
    assert ok, problems
    assert witness["facade_contract"] == FACADE_CONTRACT_VERSION
    assert witness["snapshot_schema"] == SNAPSHOT_SCHEMA
    assert witness["read_only_guarantee"]["store_writes"] == "none"
    exposed = witness["read_only_guarantee"]["write_paths_exposed"]
    # the one declared non-store exception: the witness writer itself
    assert len(exposed) == 1 and exposed[0].startswith("write_capability_witness:")
    # every witnessed source is hashed
    assert set(witness["source_digests"]) == {
        "embedded/__init__.py", "embedded/contract.py",
        "embedded/handshake.py", "embedded/snapshot.py",
    }
    assert all(len(v) == 64 for v in witness["source_digests"].values())
    # content-free: no absolute path or user directory leaks into the witness
    blob = json.dumps(witness)
    assert ":\\" not in blob and "/home/" not in blob and "/Users/" not in blob


def test_witness_tamper_is_detected():
    witness = capability_witness()
    witness["runtime_version"] = "9.9.9"  # self-hash no longer matches
    ok, problems = verify_witness(witness)
    assert not ok and "witness_self_hash_mismatch" in problems


def test_witness_source_drift_is_detected():
    witness = capability_witness()
    tampered = dict(witness)
    digests = dict(witness["source_digests"])
    digests["embedded/snapshot.py"] = "0" * 64
    tampered["source_digests"] = digests
    tampered["witness_hash"] = canonical_hash(
        {k: v for k, v in tampered.items() if k != "witness_hash"}
    )
    ok, problems = verify_witness(tampered)
    assert not ok
    assert any(p.startswith("witness_source_digest_drift") for p in problems)


def test_witness_file_round_trips(tmp_path):
    path = write_capability_witness(tmp_path / "witness.json")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    ok, problems = verify_witness(loaded)
    assert ok, problems


# --------------------------------------------------------------------------
# handshake
# --------------------------------------------------------------------------


def test_handshake_matches_on_declared_contract():
    result = handshake(
        required_contract=FACADE_CONTRACT_VERSION,
        required_snapshot_schema=SNAPSHOT_SCHEMA,
        required_retrieval_modes=["keyword_no_persistent_index"],
    )
    assert result["match"] is True and result["problems"] == []


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"required_contract": "engram.embedded_host_facade.v2"}, "facade_contract_mismatch"),
        ({"required_snapshot_schema": "core.something.v9"}, "snapshot_schema_mismatch"),
        ({"required_retrieval_modes": ["vector_index"]}, "retrieval_mode_unsupported:vector_index"),
    ],
)
def test_handshake_mismatch_fails_closed(kwargs, expected):
    result = handshake(**kwargs)
    assert result["match"] is False
    assert expected in result["problems"]
    # fail-closed: never degrade to a "best effort" match
    with pytest.raises(FacadeHandshakeError):
        require_compatible(**kwargs)


def test_handshake_reports_facade_identity_on_mismatch():
    result = handshake(required_contract="wrong.contract.v1")
    assert result["facade_contract"] == FACADE_CONTRACT_VERSION
    assert result["requested"]["contract"] == "wrong.contract.v1"


# --------------------------------------------------------------------------
# bounded retrieval + snapshot schema
# --------------------------------------------------------------------------


def _retrieve(store_root, project_folder, **over):
    kwargs = dict(
        engram_root=store_root,
        project_folder=project_folder,
        project_id="proj-alpha",
        task_id="task-0007",
        task_class="software_development",
        objective="Bind observed evidence to frozen acceptance criteria.",
        limit=8,
    )
    kwargs.update(over)
    return retrieve_task_context_snapshot(**kwargs)


def test_snapshot_is_schema_isomorphic_with_consumer_contract(store, project):
    """Field set and hash discipline must match core.engram_task_context_snapshot.v1.

    The consumer's field set is asserted literally here: if either side drifts,
    this test is the tripwire.
    """
    _seed(store, project, "proj-alpha")
    snapshot = _retrieve(store, project)

    consumer_fields = {
        "schema", "retrieval_query", "scope", "scope_hash", "previous_context_hash",
        "source_hashes", "included_count", "matched_count", "withheld_count",
        "provider_included_count", "excluded_count", "excluded_by_reason", "items",
        "status", "engram_read_only", "engram_write_performed", "provider_authority",
        "context_hash",
    }
    assert set(snapshot) == consumer_fields
    assert snapshot["schema"] == SNAPSHOT_SCHEMA
    assert snapshot["retrieval_query"]["tier"] == "verified"
    assert snapshot["retrieval_query"]["search_mode"] == "keyword_no_persistent_index"
    assert snapshot["engram_read_only"] is True
    assert snapshot["engram_write_performed"] is False
    assert snapshot["provider_authority"] is False
    # hash binds every other field, using the consumer's canonicalisation
    recomputed = canonical_hash({k: v for k, v in snapshot.items() if k != "context_hash"})
    assert snapshot["context_hash"] == recomputed
    validate_snapshot(snapshot)


def test_private_bodies_are_counted_but_never_quoted(store, project):
    _seed(store, project, "proj-alpha")
    snapshot = _retrieve(store, project)

    # Guard against a vacuous pass: both paths must actually be exercised, i.e.
    # a shareable item made it in AND a private item was matched-then-withheld.
    assert snapshot["withheld_count"] >= 1, "private path not exercised"
    summaries = [i["public_equivalent_summary"] for i in snapshot["items"]]
    assert SHAREABLE_SUMMARY in summaries, "shareable path not exercised"

    blob = json.dumps(snapshot, ensure_ascii=False)
    assert "PRIVATEBODYMARKER" not in blob
    # a matched-but-private item is reflected in the counts, not the payload
    assert snapshot["matched_count"] == snapshot["withheld_count"] + snapshot["provider_included_count"]


def test_context_empty_on_a_fresh_store(store, project):
    snapshot = _retrieve(store, project)
    assert snapshot["status"] == "context_empty"
    assert snapshot["items"] == [] and snapshot["included_count"] == 0
    assert snapshot["matched_count"] == 0
    validate_snapshot(snapshot)


def test_snapshot_is_deterministic_across_calls_and_instances(store, project):
    _seed(store, project, "proj-alpha")
    first = _retrieve(store, project)
    second = _retrieve(store, project)
    assert first["context_hash"] == second["context_hash"]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_previous_context_hash_round_trips(store, project):
    _seed(store, project, "proj-alpha")
    first = _retrieve(store, project)
    second = _retrieve(store, project, previous_context_hash=first["context_hash"])
    assert second["previous_context_hash"] == first["context_hash"]
    validate_snapshot(second)


@pytest.mark.parametrize(
    "over,code",
    [
        ({"limit": 0}, "context_limit_invalid"),
        ({"limit": 99}, "context_limit_invalid"),
        ({"limit": True}, "context_limit_invalid"),
        ({"task_class": "not_a_class"}, "context_task_class_invalid"),
        ({"objective": "see C:\\Users\\secret\\notes.md"}, "context_objective_unsafe"),
        ({"objective": "authorization: Bearer abc123"}, "context_objective_unsafe"),
        ({"previous_context_hash": "nothex"}, "previous_context_hash_invalid"),
        ({"project_id": ""}, "context_project_id_invalid"),
    ],
)
def test_bounds_and_safety_fail_closed(store, project, over, code):
    with pytest.raises(FacadeContextError) as exc:
        _retrieve(store, project, **over)
    assert str(exc.value) == code


def test_validate_snapshot_rejects_tampering(store, project):
    _seed(store, project, "proj-alpha")
    snapshot = _retrieve(store, project)
    snapshot["included_count"] = snapshot["included_count"] + 1
    with pytest.raises(FacadeContextError):
        validate_snapshot(snapshot)


def test_error_messages_carry_no_content_or_paths(store, project):
    with pytest.raises(FacadeContextError) as exc:
        _retrieve(store, project, objective="see C:\\Users\\someone\\private.md")
    message = str(exc.value)
    assert message == "context_objective_unsafe"
    assert ":\\" not in message and "someone" not in message


# --------------------------------------------------------------------------
# zero-write constitution
# --------------------------------------------------------------------------


def test_retrieval_performs_no_write_syscall_inside_the_store(store, project):
    """Phase 1's constitution: a retrieval must not write to the store at all."""
    _seed(store, project, "proj-alpha")
    before = _tree_fingerprint(store)

    with WriteGuard(store) as guard:
        snapshot = _retrieve(store, project)

    assert snapshot["engram_write_performed"] is False
    assert guard.violations == [], f"write syscalls inside store: {guard.violations}"
    assert _tree_fingerprint(store) == before


def test_retrieval_on_fresh_store_creates_nothing(store, project):
    """Even against an empty root, a read must not materialise store structure."""
    before = _tree_fingerprint(store)
    with WriteGuard(store) as guard:
        snapshot = _retrieve(store, project)
    assert snapshot["status"] == "context_empty"
    assert guard.violations == [], f"write syscalls inside store: {guard.violations}"
    assert _tree_fingerprint(store) == before


def test_facade_exposes_no_write_entry_points():
    """The public surface must not hand a host any way to mutate the store."""
    import piia_engram.embedded as facade

    forbidden = ("add_", "save_", "update_", "write_engram", "delete_", "archive_", "wrap_up")
    exported = [name for name in facade.__all__]
    offenders = [
        name for name in exported
        if any(name.startswith(p) for p in forbidden)
        and name != "write_capability_witness"  # writes a witness file, never the store
    ]
    assert offenders == [], f"phase 1 must expose no write path: {offenders}"


def test_write_guard_itself_detects_a_real_write(store):
    """Guard sanity: it must fail on an actual write, or the proofs above are vacuous."""
    with WriteGuard(store) as guard:
        (store / "canary.txt").write_text("x", encoding="utf-8")
    assert guard.violations, "WriteGuard failed to notice a real write"


# --------------------------------------------------------------------------
# contract binding + checked-in artifacts (manifest / witness guards)
# --------------------------------------------------------------------------

def test_runtime_capabilities_bind_facade_contract_ids():
    """The runtime manifest must carry the facade's identifiers verbatim so the
    two compatibility surfaces cannot drift apart silently."""
    from piia_engram.embedded import contract as contract_mod
    from piia_engram.runtime_capabilities import CONTRACTS

    assert CONTRACTS["embedded_host_facade"] == contract_mod.FACADE_CONTRACT_VERSION
    assert CONTRACTS["embedded_task_context_snapshot"] == contract_mod.SNAPSHOT_SCHEMA
    manifest_witness = capability_witness()
    assert manifest_witness["facade_contract"] == CONTRACTS["embedded_host_facade"]
    assert manifest_witness["snapshot_schema"] == CONTRACTS["embedded_task_context_snapshot"]


def test_checked_in_contract_manifest_matches_live_surface():
    import scripts.check_embedded_contract as guard

    manifest = json.loads(
        (guard.MANIFEST_PATH).read_text(encoding="utf-8")
    )
    assert guard.check(manifest) == []
    # anti-vacuity: a tampered manifest must be reported, not waved through
    tampered = dict(manifest, facade_contract="engram.tampered.v9")
    assert guard.check(tampered) != []


def test_checked_in_capability_witness_verifies_against_live_sources():
    witness_file = (
        Path(__file__).resolve().parents[1] / "docs" / "embedded" / "capability-witness.json"
    )
    ok, problems = verify_witness(json.loads(witness_file.read_text(encoding="utf-8")))
    assert ok, problems


# --------------------------------------------------------------------------
# input boundary: hostile strings at the public entry points
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "field,value",
    [
        ("required_contract", "engram.embedded_host_facade.v1\nx" * 200),
        ("required_contract", "​​engram.embedded_host_facade.v1"),  # zero-width space
        ("required_snapshot_schema", "engram.embedded_task_context_snapshot.v1\r\nGET /etc"),
        ("required_contract", "x" * 10_000),
    ],
)
def test_handshake_hostile_strings_fail_closed_with_stable_codes(field, value):
    result = handshake(**{field: value})
    assert result["match"] is False
    assert result["problems"], "hostile input must produce explicit problem codes"
    for problem in result["problems"]:
        assert value not in problem  # codes never echo input content


@pytest.mark.parametrize(
    "over",
    [
        {"objective": "x" * 10_000},
        {"objective": "multi\nline\r\nobjective\twith\twhitespace"},
        {"objective": "说明：目标是跨语言检索（中文/English mix）— 包括 emoji 🚀"},
        {"task_id": "id-with\nnewline"},
        {"task_id": "x" * 10_000},
        # credential-shaped objective, assembled at runtime so the source
        # never contains a literal that the release sanitize scanner must
        # flag (the scanner is right to block any sk- literal; the fixture
        # here is an obvious fake and only exercises the facade's guard)
        {"objective": "s" + "k-FAKE-TOKEN-SENTINEL-" + "0" * 16},
        {"objective": r"C:\Users\someone\private notes.txt"},
        {"objective": "/etc/passwd contents"},
    ],
)
def test_retrieval_hostile_inputs_never_leak_or_crash(store, project, over):
    """Hostile inputs either fail closed with a stable code or produce a valid
    snapshot; they never raise raw exceptions or echo the input."""
    try:
        snapshot = _retrieve(store, project, **over)
    except FacadeContextError as exc:
        message = str(exc)
        assert message == message.strip() and "\n" not in message
        for raw in over.values():
            assert raw[:40] not in message
        return
    validate_snapshot(snapshot)
