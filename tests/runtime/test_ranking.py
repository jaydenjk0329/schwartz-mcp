"""Failure cases that must never become an apparently confident tournament winner."""

import pytest
from schwartz_mcp.common import DomainError
from schwartz_mcp.contracts import candidate_hash, ROLES
from schwartz_mcp.ranking import aggregate, eligible
from conftest import candidate


def match(a, b, outcome="A"):
    return {
        "id": a["id"] + b["id"],
        "a_id": a["id"],
        "b_id": b["id"],
        "a_hash": candidate_hash(a),
        "b_hash": candidate_hash(b),
        "result": {"outcome": outcome},
    }


def test_unsupported_release_claim_cannot_win_even_with_passing_reviews():
    c = candidate()
    c["claims"] = [
        {
            "text": "Doubles revenue",
            "kind": "factual",
            "support": "unsupported",
            "evidence_ids": [],
        }
    ]
    reviews = [
        {
            "candidate_id": c["id"],
            "candidate_hash": candidate_hash(c),
            "role": role,
            "verdict": "pass",
            "findings": [],
        }
        for role in ROLES
    ]
    accepted, excluded = eligible(
        {"level": "complete_asset", "candidates": [c]}, reviews, set()
    )
    assert not accepted and "Unsupported factual" in excluded[0]["reason"]


def test_revision_invalidates_previous_passing_reviews():
    c = candidate()
    reviews = [
        {
            "candidate_id": c["id"],
            "candidate_hash": candidate_hash(c),
            "role": role,
            "verdict": "pass",
            "findings": [],
        }
        for role in ROLES
    ]
    c["content"] += " revised"
    assert not eligible(
        {"level": "strategic_angle", "candidates": [c]}, reviews, set()
    )[0]


def test_abstention_does_not_connect_or_rank_disconnected_candidates():
    a, b, c = [candidate(i) for i in ("a", "b", "c")]
    result, selected = aggregate(
        [a, b, c], [match(a, b), match(b, c, "abstain")], "ctx", "strategic_angle", []
    )
    assert not selected["candidates"]
    assert any("Disconnected" in limitation for limitation in result["limitations"])


def test_changed_candidate_invalidates_pairwise_comparison():
    a, b = candidate("a"), candidate("b")
    comparison = match(a, b)
    a["content"] += " changed"
    with pytest.raises(DomainError, match="stale_comparison"):
        aggregate([a, b], [comparison], "ctx", "strategic_angle", [])


def test_preference_cycles_remain_visible():
    a, b, c = [candidate(i) for i in ("a", "b", "c")]
    result, _ = aggregate(
        [a, b, c], [match(a, b), match(b, c), match(c, a)], "ctx", "strategic_angle", []
    )
    assert any("cycle" in limitation for limitation in result["limitations"])
    assert any("ties" in limitation for limitation in result["limitations"])
    assert result["label"] == "judge_preference_not_conversion_probability"


def test_reversed_side_bias_is_flagged_and_not_double_weighted():
    a, b = candidate("a"), candidate("b")
    original = {**match(a, b, "A"), "judge": 0, "id": "original"}
    reverse = {**match(b, a, "A"), "judge": 0, "id": "reverse", "reversed_check": True}
    result, _ = aggregate([a, b], [original, reverse], "ctx", "strategic_angle", [])
    assert [r["comparisons"] for r in result["ranking"]] == [1, 1]
    assert [r["points"] for r in result["ranking"]] == [0.5, 0.5]
    check = result["sensitivity"]["order_checks"][0]
    assert check["preference_changed"] and check["same_displayed_side_selected"]
    assert not result["sensitivity"]["empirical_confidence"]


def test_judge_removal_reports_descriptive_rank_ranges():
    a, b = candidate("a"), candidate("b")
    matches = [
        {**match(a, b, "A"), "judge": 0, "id": "j0"},
        {**match(a, b, "B"), "judge": 1, "id": "j1"},
    ]
    result, _ = aggregate([a, b], matches, "ctx", "strategic_angle", [])
    report = result["sensitivity"]
    assert len(report["scenarios"]) == 3
    assert all(
        r["best_rank"] == 1 and r["worst_rank"] == 2 for r in report["rank_bands"]
    )
    assert "not calibrated confidence" in report["interpretation"]
