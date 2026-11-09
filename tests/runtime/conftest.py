from pathlib import Path
import pytest
from schwartz_mcp.engine import Engine

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def engine(tmp_path):
    return Engine(ROOT, tmp_path / "runtime.sqlite3")


def project(engine, key="fixture"):
    return engine.create_project(
        {
            "product": "Fixture notebook",
            "audience": "Test audience",
            "objective": "Fixture validation, not a real campaign",
            "channels": ["sales-page"],
        },
        {},
        key,
    )


def plan_start(
    engine,
    workflow="headline-laboratory",
    mode="host_driven",
    profile=None,
    budget=None,
    inputs=None,
):
    p = project(engine)
    plan = engine.plan_run(
        p["id"],
        workflow,
        inputs,
        {
            "generators": 1,
            "debate_rounds": 1,
            "judges": 1,
            "reverse_pairs": 0,
            **(profile or {}),
        },
        {"max_model_calls": 300, **(budget or {})},
    )
    r = engine.start_run(p["id"], plan["id"], plan["hash"], mode, workflow)
    return p, plan, r


# These outputs exercise orchestration and provenance. They are explicitly synthetic
# contract fixtures and are never presented as real advertising/empirical evaluations.
def value(schema):
    if "enum" in schema:
        return schema["enum"][0]
    typ = schema.get("type")
    if typ == "object":
        return {k: value(v) for k, v in schema["properties"].items()}
    if typ == "array":
        return []
    if typ in ("number", "integer"):
        return 0
    if typ == "boolean":
        return False
    return "Synthetic fixture; no empirical claim"


def candidate(ident="c1", content="Notebook idea A"):
    return {
        "id": ident,
        "content": content,
        "rationale": "Synthetic fixture",
        "hypothesis_id": ident,
        "claims": [],
        "source_atom_ids": ["demo-evidence-boundary"],
        "evidence_ids": [],
        "limitations": ["Synthetic test data"],
    }


def output(task):
    if task["task_type"] == "comparison":
        return {
            "outcome": "tie",
            "rationale": "Synthetic equal preference",
            "criterion_findings": ["No empirical claim"],
            "evidence_ids": [],
        }
    outputs = value(task["output_schema"])
    for role, payload in outputs.items():
        if role == "campaign_brief":
            payload["channels"] = task["inputs"]["request"]["payload"].get(
                "channels", []
            )
        if role == "experiment_analysis":
            payload["observation_artifact_id"] = task["inputs"]["observed_results"][
                "id"
            ]
        if "candidates" in payload:
            source = task["inputs"].get("candidate_pool", {}).get("payload")
            level = {
                "theme": "strategic_angle",
                "headlines": "headline_and_lead",
                "structure": "structure",
                "draft": "complete_asset",
                "proof": "complete_asset",
                "polish": "complete_asset",
                "adapt": "complete_asset",
            }.get(task["stage_id"], source["level"] if source else "strategic_angle")
            payload.update(
                context_id=task["comparison_context_id"],
                level=level,
                candidates=[candidate(), candidate("c2", "Notebook idea B")],
            )
            if task["stage_id"] == "debate":
                payload["candidates"][0]["content"] += " revised"
        if role.startswith("independent_reviews"):
            payload["reviews"] = [
                {
                    "role": task["executor_requirements"]["review_role"],
                    "candidate_id": i,
                    "candidate_hash": h,
                    "verdict": "pass",
                    "findings": [],
                    "uncertainty": "Synthetic fixture",
                }
                for i, h in task["candidate_hashes"].items()
            ]
        if "source_atom_ids" in payload:
            payload["source_atom_ids"] = ["demo-evidence-boundary"]
    return outputs


def drive(engine, pid, rid, maximum=400):
    for _ in range(maximum):
        work = engine.claim_work(pid, rid, "fixture-host")
        if "work_id" not in work:
            return work
        engine.submit_work(
            pid, work["work_id"], work["lease_token"], work["input_hash"], output(work)
        )
    raise AssertionError("Run failed to converge within fixture budget")
