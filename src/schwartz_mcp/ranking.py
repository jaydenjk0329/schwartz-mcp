"""Transparent pairwise preference aggregation; never a conversion predictor."""

from collections import defaultdict
from .common import DomainError
from .contracts import candidate_hash, ROLES
from .ranking_sensitivity import scores, sensitivity


def eligible(pool, reviews, evidence_ids):
    accepted = []
    excluded = []
    release = pool["level"] == "complete_asset"
    for c in pool["candidates"]:
        applicable = [
            r
            for r in reviews
            if r["candidate_id"] == c["id"] and r["candidate_hash"] == candidate_hash(c)
        ]
        problems = []
        if {r["role"] for r in applicable} != set(ROLES):
            problems.append("Missing current-version review roles")
        if any(
            r["verdict"] != "pass"
            or any(f["severity"] == "blocking" for f in r["findings"])
            for r in applicable
        ):
            problems.append("Review requests revision or abstains")
        if release:
            for claim in c["claims"]:
                if claim["kind"] == "factual" and (
                    claim["support"] != "supported"
                    or not claim["evidence_ids"]
                    or not set(claim["evidence_ids"]) <= evidence_ids
                ):
                    problems.append("Unsupported factual release claim")
        if problems:
            excluded.append(
                {"candidate_id": c["id"], "reason": "; ".join(sorted(set(problems)))}
            )
        else:
            accepted.append(c)
    return accepted, excluded


def aggregate(candidates, comparisons, context_id, level, excluded, shortlist=3):
    byid = {c["id"]: c for c in candidates}
    for m in comparisons:
        a, b = m["a_id"], m["b_id"]
        if a not in byid or b not in byid or a == b:
            raise DomainError("invalid_comparison", "Unknown or identical candidates")
        if m["a_hash"] != candidate_hash(byid[a]) or m["b_hash"] != candidate_hash(
            byid[b]
        ):
            raise DomainError("stale_comparison", "Candidate changed after comparison")
        if m["result"]["outcome"] not in ("A", "B", "tie", "abstain"):
            raise DomainError("invalid_comparison", "Unknown comparison outcome")
    rows, components, wins = scores(list(byid), comparisons)
    for row in rows:
        row["hypothesis_id"] = byid[row["candidate_id"]]["hypothesis_id"]
    diagnostics = sensitivity(list(byid), comparisons)
    limitations = [
        "Conditional judge preferences; not conversion probabilities.",
        "Independent-looking judgments may share model or host biases.",
        "No calibrated numerical rank confidence is estimated in this version.",
    ]
    if any(c["preference_changed"] for c in diagnostics["order_checks"]):
        limitations.append(
            "Reversed-order checks changed preferences; inspect order sensitivity before selection."
        )
    if any(not s["comparable"] for s in diagnostics["scenarios"]):
        limitations.append(
            "Some judge-removal scenarios are disconnected and have no global rank range."
        )
    if len(components) > 1:
        limitations.append(
            "Disconnected comparison graph: no global shortlist; collect bridging comparisons."
        )

    def cyclic(start):
        todo = list(wins.get(start, ()))
        seen = set()
        while todo:
            current = todo.pop()
            if current == start:
                return True
            if current not in seen:
                seen.add(current)
                todo.extend(wins.get(current, ()))
        return False

    if any(cyclic(a) for a in wins):
        limitations.append(
            "Preference cycle detected; criterion tradeoffs remain unresolved."
        )
    ties = defaultdict(list)
    for r in rows:
        ties[(r["points"] / r["comparisons"] if r["comparisons"] else 0)].append(
            r["candidate_id"]
        )
    if any(len(g) > 1 for g in ties.values()):
        limitations.append(
            "Equal scores are ties; displayed ID order is only deterministic presentation."
        )
    selected = []
    seen = set()
    if len(components) <= 1:
        for r in rows:
            if r["hypothesis_id"] not in seen:
                selected.append(byid[r["candidate_id"]])
                seen.add(r["hypothesis_id"])
            if len(selected) >= shortlist:
                break
        for r in rows:
            if len(selected) >= shortlist:
                break
            if byid[r["candidate_id"]] not in selected:
                selected.append(byid[r["candidate_id"]])
    result = {
        "context_id": context_id,
        "level": level,
        "candidate_ids": list(byid),
        "comparison_ids": [m["id"] for m in comparisons],
        "ranking": rows,
        "excluded": excluded,
        "limitations": limitations,
        "label": "judge_preference_not_conversion_probability",
        "sensitivity": diagnostics,
    }
    return result, {"context_id": context_id, "level": level, "candidates": selected}
