"""Conservative reuse: only an unchanged, fully committed workflow prefix."""

from copy import deepcopy
import json
from .common import DomainError, digest


def reuse_plan(engine, con, project_id, plan, source_run_id, restart_from_stage):
    source = engine._run(con, source_run_id, project_id)
    previous = engine.store.get(con, source["plan_id"], project_id, "plan")
    blockers = []
    for field in (
        "request",
        "inputs",
        "profile",
        "source_version",
        "workflow_version",
        "evidence_snapshot_hash",
    ):
        if plan[field] != previous[field]:
            blockers.append(field + "_changed")
    if previous.get("revision_instructions") and previous.get(
        "revision_instructions"
    ) != plan.get("revision_instructions"):
        blockers.append("previous_revision_instructions_changed")
    stop = len(plan["nodes"])
    if restart_from_stage:
        matches = [
            i
            for i, n in enumerate(plan["nodes"])
            if n["stage_id"] == restart_from_stage
        ]
        if not matches:
            raise DomainError("restart_stage", "Stage is not in the target workflow")
        stop = matches[0]
    elif plan.get("revision_instructions"):
        stop = 0
    rows = con.execute(
        "SELECT * FROM steps WHERE run_id=? ORDER BY position", (source_run_id,)
    ).fetchall()
    reused = []
    bindings = {}
    reason = (
        "requested_restart_boundary"
        if stop < len(plan["nodes"])
        else "no_more_completed_work"
    )
    if not blockers:
        for index, target in enumerate(plan["nodes"][:stop]):
            if index >= len(rows) or rows[index]["status"] != "completed":
                break
            old_definition = previous["nodes"][index]
            if target != old_definition:
                reason = "workflow_prefix_diverged"
                break
            if target["stage_id"] == "learn":
                reason = "observations_require_explicit_attachment"
                break
            body = json.loads(rows[index]["body"])
            if "completed_bindings" not in body:
                reason = "legacy_step_has_no_reuse_metadata"
                break
            for aid in body["completed_bindings"].values():
                artifact = engine.store.get(con, aid, project_id, "artifact")
                if (
                    artifact["hash"] != digest(artifact["payload"])
                    or artifact["validity"] == "unaccepted_not_used"
                ):
                    raise DomainError(
                        "reuse_integrity", "Referenced artifact is invalid or changed"
                    )
            bindings = deepcopy(body["completed_bindings"])
            reused.append(
                {
                    "source_step_id": rows[index]["id"],
                    "stage_id": target["stage_id"],
                    "completed_bindings": bindings,
                    "completed_outputs": body["completed_outputs"],
                }
            )
    return {
        "source_run_id": source_run_id,
        "reused_step_count": len(reused),
        "steps": reused,
        "bindings": bindings,
        "blockers": blockers,
        "stop_reason": "context_changed" if blockers else reason,
        "regenerated_stages": [n["stage_id"] for n in plan["nodes"][len(reused) :]],
        "policy": "Only an exact committed prefix is reused; the remaining suffix is regenerated.",
        "comparison_context_id": source.get("comparison_context_id", source["plan_id"])
        if not blockers
        else plan["id"],
    }


def inspect_lineage(engine, project_id, artifact_id, limit=200):
    if not 1 <= limit <= 500:
        raise DomainError("limit", "Use a lineage limit between 1 and 500")
    with engine.store.tx() as con:
        engine.store.get(con, artifact_id, project_id, "artifact")
        rows = con.execute(
            "SELECT body FROM objects WHERE project_id=? AND kind='artifact'",
            (project_id,),
        ).fetchall()
        artifacts = {a["id"]: a for a in (json.loads(r["body"]) for r in rows)}
        children = {}
        for aid, artifact in artifacts.items():
            for parent in artifact["parent_ids"]:
                children.setdefault(parent, []).append(aid)

        def walk(start, neighbors):
            visited = set()
            pending = list(neighbors(start))
            while pending and len(visited) < limit:
                aid = pending.pop(0)
                if aid in visited:
                    continue
                visited.add(aid)
                pending.extend(neighbors(aid))
            return sorted(visited), bool(pending)

        ancestors, a_more = walk(
            artifact_id, lambda i: artifacts.get(i, {}).get("parent_ids", [])
        )
        descendants, d_more = walk(artifact_id, lambda i: children.get(i, []))
        return {
            "artifact_id": artifact_id,
            "ancestors": ancestors,
            "dependent_artifacts": descendants,
            "truncated": a_more or d_more,
            "meaning": "Dependency impact only; stored artifacts remain immutable. Fork/replan to regenerate.",
        }
