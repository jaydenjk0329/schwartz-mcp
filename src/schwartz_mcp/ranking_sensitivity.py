"""Descriptive judge/order sensitivity, not statistical confidence or market prediction."""

from collections import defaultdict


def scores(candidate_ids, comparisons):
    groups = defaultdict(list)
    for m in comparisons:
        if m["result"]["outcome"] == "abstain":
            continue
        pair = tuple(sorted((m["a_id"], m["b_id"])))
        judge = str(m.get("judge", 0))
        outcome = m["result"]["outcome"]
        first_score = (
            0.5
            if outcome == "tie"
            else float((outcome == "A") == (m["a_id"] == pair[0]))
        )
        groups[(pair, judge)].append(first_score)
    points, counts, graph, pair_scores = (
        defaultdict(float),
        defaultdict(int),
        defaultdict(set),
        defaultdict(list),
    )
    for (pair, _), values in groups.items():
        a, b = pair
        mean = sum(values) / len(values)
        points[a] += mean
        points[b] += 1 - mean
        counts[a] += 1
        counts[b] += 1
        graph[a].add(b)
        graph[b].add(a)
        pair_scores[pair].append(mean)
    rows = [
        {"candidate_id": i, "points": points[i], "comparisons": counts[i]}
        for i in candidate_ids
    ]
    rows.sort(key=lambda r: (-rate(r), r["candidate_id"]))
    remaining, components = set(candidate_ids), []
    while remaining:
        todo, seen = [min(remaining)], set()
        while todo:
            i = todo.pop()
            if i in seen:
                continue
            seen.add(i)
            todo.extend(graph[i] - seen)
        remaining -= seen
        components.append(sorted(seen))
    wins = defaultdict(set)
    for (a, b), values in pair_scores.items():
        mean = sum(values) / len(values)
        if mean > 0.5:
            wins[a].add(b)
        if mean < 0.5:
            wins[b].add(a)
    return rows, components, wins


def rate(row):
    return row["points"] / row["comparisons"] if row["comparisons"] else 0


def positions(rows):
    return {
        r["candidate_id"]: (
            1 + sum(rate(o) > rate(r) + 1e-12 for o in rows),
            sum(rate(o) >= rate(r) - 1e-12 for o in rows),
        )
        for r in rows
    }


def sensitivity(candidate_ids, comparisons):
    baseline, components, _ = scores(candidate_ids, comparisons)
    judges = sorted({str(m.get("judge", 0)) for m in comparisons})
    scenarios = [
        {"label": "all_judges", "comparable": len(components) <= 1, "ranking": baseline}
    ]
    if len(judges) > 1:
        for judge in judges:
            rows, parts, _ = scores(
                candidate_ids,
                [m for m in comparisons if str(m.get("judge", 0)) != judge],
            )
            scenarios.append(
                {
                    "label": "without_judge_" + judge,
                    "comparable": len(parts) <= 1,
                    "ranking": rows,
                }
            )
    ranges = [positions(s["ranking"]) for s in scenarios if s["comparable"]]
    bands = []
    if len(components) <= 1:
        for i in candidate_ids:
            bands.append(
                {
                    "candidate_id": i,
                    "best_rank": min(r[i][0] for r in ranges),
                    "worst_rank": max(r[i][1] for r in ranges),
                }
            )
    grouped = defaultdict(list)
    for m in comparisons:
        grouped[(tuple(sorted((m["a_id"], m["b_id"]))), str(m.get("judge", 0)))].append(
            m
        )
    checks = []
    for (pair, judge), matches in grouped.items():
        if len({m["a_id"] for m in matches}) < 2:
            continue
        choices = []
        for m in matches:
            o = m["result"]["outcome"]
            choices.append(m["a_id"] if o == "A" else m["b_id"] if o == "B" else o)
        checks.append(
            {
                "candidate_ids": list(pair),
                "judge": judge,
                "comparison_ids": [m["id"] for m in matches],
                "preference_changed": len(set(choices)) > 1,
                "same_displayed_side_selected": len(
                    {m["result"]["outcome"] for m in matches}
                )
                == 1
                and matches[0]["result"]["outcome"] in ("A", "B"),
            }
        )
    return {
        "score_method": "One unit per unordered pair and judge; repeated/reversed orientations averaged.",
        "components": components,
        "judge_count": len(judges),
        "order_checks": checks,
        "rank_bands": bands,
        "scenarios": scenarios,
        "interpretation": "Descriptive tie and leave-one-judge-out rank ranges, not calibrated confidence intervals. Judge roles may share models and biases.",
        "empirical_confidence": False,
    }
