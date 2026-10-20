"""Canonical, body-free result hashes across processes and changing receipts."""

import hashlib
import json
import os
import subprocess
import sys

import pytest

from piia_engram.isolated_store_launch import build_child_env
from test_isolated_store import _snap
from test_isolated_supersede import admit, recall
from test_replay_experience import CLOCK, _world


def expected(receipt):
    payload = {"effective_cuts": {"evidence_before": receipt["effective_evidence_before"],
                                  "admitted_before": receipt["effective_admitted_before"]},
               "excluded": receipt["excluded"],
               "items": list(zip(receipt["returned_ids"], receipt["returned_content_sha256"]))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
def test_canonical_result_hash_is_identical_in_fresh_process(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    admit(w, 1)
    first = recall(w)
    receipt = first["receipt"]
    assert first["result_sha256"] == receipt["result_sha256"] == expected(receipt)
    assert receipt == json.loads(w.pr.receipts_path.read_text(encoding="utf-8").splitlines()[-1])
    second = recall(w)
    assert first["result_sha256"] == second["result_sha256"]
    assert receipt["snapshot_id"] != second["receipt"]["snapshot_id"]
    options = {"evidence_before": "2020-01-03T00:00:00Z", "admitted_before":
               "2020-01-03T00:00:00Z" if mode == "replay_experience" else "2099-01-01T00:00:00Z"}
    if mode == "replay_experience":
        options["now"] = CLOCK
    program = "\n".join(["import json,sys,os", "os.environ.pop('CODEX_HOME',None)",
        "from piia_engram.isolated_store import Config,IsolatedStore",
        "s=IsolatedStore.open(Config.load(sys.argv[1]))",
        "print(s.recall('dp-test','round',**json.loads(sys.argv[2]))['result_sha256'])"])
    env = build_child_env(dict(os.environ), w.cfg)
    env["CODEX_HOME"] = str(w.cfg.fake_home / ".codex")
    result = subprocess.run([sys.executable, "-c", program, str(w.cfg_path), json.dumps(options)],
                            env=env, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == first["result_sha256"]


@pytest.mark.parametrize("tamper", ["result_sha256", "effective_evidence_before", "excluded", "returned_content_sha256"])
def test_verifier_recomputes_hash_even_for_matching_sealed_copy(tmp_path, monkeypatch, tamper):
    w = _world(tmp_path, monkeypatch, mode="production")
    admit(w, 1)
    receipt = recall(w)["receipt"]
    if tamper == "excluded":
        receipt[tamper] = {"family_excluded": 99}
    elif tamper == "returned_content_sha256":
        receipt[tamper] = ["0" * 64]
    else:
        receipt[tamper] = "0" * 64
    lines = w.pr.receipts_path.read_text(encoding="utf-8").splitlines()
    lines[-1] = json.dumps(receipt)
    w.pr.receipts_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = _snap(w.area)
    assert w.pr.verify_recall_receipt(receipt)["result"] == "recall_receipt_invalid"
    assert _snap(w.area) == before


def test_legacy_receipt_without_hash_remains_readable_byte_for_byte(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch, mode="production")
    receipt = recall(w)["receipt"]
    receipt.pop("result_sha256", None)
    lines = w.pr.receipts_path.read_text(encoding="utf-8").splitlines()
    lines[-1] = json.dumps(receipt)
    w.pr.receipts_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = _snap(w.area)
    assert w.pr.verify_recall_receipt(receipt)["result"] == "verified"
    assert _snap(w.area) == before


def test_hash_covers_order_counts_cuts_and_empty_results(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch, mode="production")
    admit(w, 1)
    result = recall(w)
    receipt = result["receipt"]
    for field, value in (("effective_evidence_before", "2020-01-04T00:00:00.000000Z"),
                         ("effective_admitted_before", "2020-01-04T00:00:00.000000Z"),
                         ("excluded", {"family_excluded": 1}),
                         ("returned_ids", ["other"]), ("returned_content_sha256", ["0" * 64])):
        changed = dict(receipt, **{field: value})
        assert w.pr._recall_result_hash(changed) == expected(changed) != result["result_sha256"]
    empty = w.pr.recall("dp-test", "round", evidence_before="2020-01-03T00:00:00Z",
                         admitted_before="2099-01-01T00:00:00Z", limit=0)
    assert empty["items"] == []
    assert empty["result_sha256"] == expected(empty["receipt"]) != result["result_sha256"]
    pairs = dict(receipt, returned_ids=["a", "b"], returned_content_sha256=["1" * 64, "2" * 64])
    reversed_pairs = dict(pairs, returned_ids=["b", "a"], returned_content_sha256=["2" * 64, "1" * 64])
    assert w.pr._recall_result_hash(pairs) != w.pr._recall_result_hash(reversed_pairs)
