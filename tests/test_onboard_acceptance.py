"""M4: hard-defined <=10-min onboard-repo acceptance proof.

From {0 owner facts + a clean golden fixture repo + a cold-start onboard} to
{an owner-confirmed recall showing anchor / validated-at / expires}, timed end to
end, emitting a transcript (timestamps + steps + the proving fact) as evidence.
This is the first-value acceptance for 4.5.0.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram import recall_service


GOLDEN = Path(__file__).resolve().parent / "fixtures" / "onboard_repo_golden"
REPO_ID = "github.com/acme/onboard-golden-fixture"


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "engram"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    return Engram(root=root)


def test_onboard_repo_zero_to_verified_recall_under_10min(eng):
    transcript: dict = {"fixture": str(GOLDEN), "repo_id": REPO_ID, "steps": []}
    t0 = time.monotonic()

    def step(name: str, **data) -> None:
        transcript["steps"].append(
            {"t_seconds": round(time.monotonic() - t0, 3), "step": name, **data}
        )

    # (0) cold start: zero owner facts
    start = [e for e in eng.get_lessons(limit=None, _update_access=False)
             if e.get("domain") == "repo-fact"]
    assert start == [], "fixture store was not empty"
    step("cold_start", owner_facts=0)

    # (1) onboard the repo (the cold-start command)
    summary = eng.onboard_repo(str(GOLDEN), repo_id=REPO_ID)
    step("onboard", anchors_scanned=summary["anchors_scanned"], created=summary["created"])
    assert summary["created"] >= 5

    # (2) owner accepts every staging candidate (anchors verified against the repo)
    accepted = 0
    for entry in eng.get_lessons(limit=None, _update_access=False):
        if entry.get("domain") == "repo-fact" and entry.get("tier") == "staging":
            res = eng.accept_onboard_candidate(entry["id"], project_root=str(GOLDEN))
            if "error" not in res:
                accepted += 1
    step("owner_accept", accepted=accepted)
    assert accepted >= 5

    # (3) owner recall surfaces the trust block
    payload = recall_service.gather_recall(eng, query="depends", include_trust=True)
    trust_items = [i for i in payload.get("knowledge", []) if isinstance(i.get("trust"), dict)]
    step("owner_recall", trust_items=len(trust_items))
    assert trust_items, "owner recall surfaced no trust block"

    # (4) at least one fact shows anchor + validated-at + expires (derived)
    proven = next(
        (
            i for i in trust_items
            if i["trust"].get("anchor")
            and i["trust"].get("validated_at")
            and ("decay_policy" in i["trust"] or "freshness_status" in i["trust"])
        ),
        None,
    )
    assert proven is not None, f"no fact with anchor+validated_at+expires: {trust_items!r}"
    step(
        "proven_fact",
        anchor=proven["trust"]["anchor"],
        anchor_status=proven["trust"].get("anchor_status"),
        validated_at=proven["trust"]["validated_at"],
        why_trustworthy=proven["trust"].get("confirmation_source"),
        decay_policy=proven["trust"].get("decay_policy"),
    )

    elapsed = time.monotonic() - t0
    transcript["elapsed_seconds"] = round(elapsed, 3)
    transcript["result"] = "PASS"
    assert elapsed <= 600, f"acceptance took {elapsed:.1f}s > 600s budget"

    # the transcript is the proof artifact (printed for the CI log / `-s` runs)
    print("\n[onboard-acceptance-transcript] " + json.dumps(transcript, ensure_ascii=False))
    assert transcript["result"] == "PASS"
    assert any(s["step"] == "proven_fact" for s in transcript["steps"])
