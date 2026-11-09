"""Reproducible development checks. No synthetic result becomes campaign evidence."""

from .common import digest, now


def evaluate_retrieval(knowledge, suite):
    rows = []
    for case in suite["cases"]:
        result = knowledge.search(case["query"], "atom", 5)
        ids = [r["record"]["id"] for r in result["results"]]
        hit = (
            not ids
            if case.get("expect_empty")
            else bool(set(ids) & set(case["expected_any"]))
        )
        rows.append(
            {
                "id": case["id"],
                "query": case["query"],
                "result_ids": ids,
                "passed": hit,
                "expected_any": case["expected_any"],
                "expect_empty": case.get("expect_empty", False),
            }
        )
    fraction = sum(r["passed"] for r in rows) / len(rows)
    metadata_pass = all(r["passed"] for r in rows if r["expect_empty"])
    return {
        "suite_id": suite["suite_id"],
        "suite_hash": digest(suite),
        "source_version": knowledge.source_version,
        "retrieval_version": getattr(knowledge, "retrieval_version", "legacy-v02"),
        "created_at": now(),
        "passed": sum(r["passed"] for r in rows),
        "total": len(rows),
        "pass_fraction": fraction,
        "meets_development_gate": fraction
        >= suite["acceptance"]["minimum_pass_fraction"]
        and metadata_pass,
        "classification": suite["classification"],
        "cases": rows,
        "limitations": [
            "Hand-authored relevance targets; not independent or held-out evaluation.",
            "Passing does not certify exhaustive interpretation, copy quality, or advertising outcomes.",
            "Source gaps remain unresolved.",
        ],
    }
