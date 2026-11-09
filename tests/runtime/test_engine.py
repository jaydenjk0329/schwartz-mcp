import json
from pathlib import Path
import pytest
from schwartz_mcp.common import DomainError
from schwartz_mcp.engine import Engine
from schwartz_mcp.contracts import SCHEMAS
from conftest import ROOT, project, plan_start, output, drive, candidate

IDS = [w["id"] for w in json.loads((ROOT / "workflows/catalog.json").read_text())]


@pytest.mark.parametrize("workflow", IDS)
def test_each_workflow_reaches_output_or_honest_observation_wait(engine, workflow):
    inputs = {"existing_assets": {"text": "Synthetic asset"}}
    if workflow == "experiment-and-learning":
        inputs["candidate_pool"] = {
            "context_id": "fixture",
            "level": "complete_asset",
            "candidates": [candidate(), candidate("c2", "B")],
        }
    p, plan, r = plan_start(engine, workflow, inputs=inputs)
    stop = drive(engine, p["id"], r["run_id"])
    assert stop["state"] in ("completed", "waiting_external_result"), stop
    run = engine.inspect_run(p["id"], r["run_id"])["run"]
    if workflow in ("full-campaign", "experiment-and-learning"):
        assert stop["state"] == "waiting_external_result"
    if workflow == "full-campaign":
        assert "package_ready" in run["milestones"]
        exported = engine.export_campaign(p["id"], r["run_id"])
        assert Path(exported["path"]).is_file() and not exported["published"]
        bundle = json.loads(Path(exported["path"]).read_text())
        ids = {a["id"] for a in bundle["artifacts"]}
        assert all(set(a["parent_ids"]) <= ids for a in bundle["artifacts"])
        assert any(a["kind"] == "pairwise_comparison" for a in bundle["artifacts"])
    if "selected_candidates" in run["bindings"]:
        artifact = engine.get_artifact(p["id"], run["bindings"]["selected_candidates"])
        assert artifact["gap_ids"] and not artifact["full_published_book_complete"]
    assert set(SCHEMAS) >= {
        a for s in engine.registry.stages.values() for a in s["produces"]
    }


def test_restart_and_duplicate_start(engine):
    p, plan, r = plan_start(engine)
    restarted = Engine(ROOT, engine.store.path)
    same = restarted.start_run(
        p["id"], plan["id"], plan["hash"], "host_driven", "headline-laboratory"
    )
    assert same == r
    assert restarted.claim_work(p["id"], r["run_id"], "host")["stage_id"] == "brief"
    with pytest.raises(DomainError, match="idempotency_conflict"):
        restarted.start_run(
            p["id"], plan["id"], plan["hash"], "server_managed", "headline-laboratory"
        )


def test_no_double_lease_and_expired_worker_cannot_commit(engine):
    p, _, r = plan_start(engine)
    a = engine.claim_work(p["id"], r["run_id"], "one")
    assert engine.claim_work(p["id"], r["run_id"], "two")["state"] == "leased"
    with engine.store.tx() as con:
        con.execute("UPDATE steps SET expires=0 WHERE id=?", (a["work_id"],))
    b = engine.claim_work(p["id"], r["run_id"], "two")
    assert a["lease_token"] != b["lease_token"]
    with pytest.raises(DomainError, match="stale_lease"):
        engine.submit_work(
            p["id"], a["work_id"], a["lease_token"], a["input_hash"], output(a)
        )
    result = engine.submit_work(
        p["id"], b["work_id"], b["lease_token"], b["input_hash"], output(b)
    )
    assert (
        engine.submit_work(
            p["id"], b["work_id"], b["lease_token"], b["input_hash"], output(b)
        )
        == result
    )


def test_pause_blocks_inflight_result_and_resume_uses_checkpoint(engine):
    p, _, r = plan_start(engine)
    w = engine.claim_work(p["id"], r["run_id"], "host")
    state = engine.inspect_run(p["id"], r["run_id"])["run"]
    paused = engine.control_run(p["id"], r["run_id"], state["version"], "pause")
    assert engine.claim_work(p["id"], r["run_id"], "host")["state"] == "paused"
    with pytest.raises(DomainError, match="stale_lease"):
        engine.submit_work(
            p["id"], w["work_id"], w["lease_token"], w["input_hash"], output(w)
        )
    engine.control_run(p["id"], r["run_id"], paused["version"], "resume")
    assert engine.claim_work(p["id"], r["run_id"], "host")["stage_id"] == "brief"


def test_project_boundary_unknown_evidence_and_bad_schema(engine):
    p, _, r = plan_start(engine)
    other = project(engine, "other")
    with pytest.raises(DomainError, match="not_found"):
        engine.inspect_run(other["id"], r["run_id"])
    w = engine.claim_work(p["id"], r["run_id"], "host")
    o = output(w)
    o["campaign_brief"]["evidence_ids"] = ["invented"]
    with pytest.raises(DomainError, match="unknown_evidence"):
        engine.submit_work(p["id"], w["work_id"], w["lease_token"], w["input_hash"], o)
    del o["campaign_brief"]["product"]
    with pytest.raises(DomainError, match="artifact_schema"):
        engine.submit_work(p["id"], w["work_id"], w["lease_token"], w["input_hash"], o)
    assert engine.inspect_run(p["id"], r["run_id"])["run"]["position"] == 0


def test_budget_reservation_is_durable_and_cannot_silently_top_up(engine):
    p, _, r = plan_start(engine, budget={"max_model_calls": 1})
    w = engine.claim_work(p["id"], r["run_id"], "host")
    engine.fail_work(
        p["id"], w["work_id"], w["lease_token"], "provider_error", "fixture failure"
    )
    assert (
        engine.claim_work(p["id"], r["run_id"], "host")["state"] == "budget_exhausted"
    )
    run = engine.inspect_run(p["id"], r["run_id"])["run"]
    assert run["usage"]["model_calls"] == 1
    engine.control_run(
        p["id"], r["run_id"], run["version"], "resume", budget={"max_model_calls": 2}
    )
    assert "work_id" in engine.claim_work(p["id"], r["run_id"], "host")


def test_revision_gets_fresh_reviews_and_peer_reviews_are_hidden(engine):
    p, _, r = plan_start(engine)
    before = []
    after = []
    for _ in range(100):
        w = engine.claim_work(p["id"], r["run_id"], "host")
        if "work_id" not in w:
            break
        if w["stage_id"] == "independent_review":
            assert "independent_reviews" not in w["inputs"]
            (
                after if w["executor_requirements"].get("after_revision") else before
            ).append(w["candidate_hashes"])
            if w["executor_requirements"].get("after_revision"):
                bad = output(w)
                bad[next(iter(bad))]["reviews"][0]["candidate_hash"] = "old"
                with pytest.raises(DomainError, match="stale_review"):
                    engine.submit_work(
                        p["id"], w["work_id"], w["lease_token"], w["input_hash"], bad
                    )
        engine.submit_work(
            p["id"], w["work_id"], w["lease_token"], w["input_hash"], output(w)
        )
    assert len(before) == 5 and len(after) == 5 and before[0] != after[0]


def test_registered_checks_and_source_flags(engine):
    p = project(engine)
    args = {
        "unit_price": "12.50",
        "quantity": 3,
        "discount": 5,
        "shipping": 2,
        "tax": 0,
    }
    a = engine.execute_check(p["id"], "offer_arithmetic", args, "calc")
    assert a["payload"]["result"]["total"] == "34.50"
    assert (
        engine.execute_check(p["id"], "offer_arithmetic", args, "calc")["id"] == a["id"]
    )
    with pytest.raises(DomainError, match="unknown_adapter"):
        engine.execute_check(p["id"], "shell", {"cmd": "echo nope"}, "bad")
    assert engine.knowledge.audit()["missing_details_remain_flagged"]


def test_evidence_is_frozen_at_plan_and_later_imports_require_new_plan(engine):
    p = project(engine)
    a = engine.add_evidence(p["id"], {"fact": "Fixture A"}, "fixture", "test", "a")
    plan = engine.plan_run(p["id"], "source-study")
    b = engine.add_evidence(p["id"], {"fact": "Fixture B"}, "fixture", "test", "b")
    r = engine.start_run(p["id"], plan["id"], plan["hash"], "host_driven", "frozen")
    work = engine.claim_work(p["id"], r["run_id"], "host")
    assert [e["id"] for e in work["evidence"]] == [a["id"]]
    out = output(work)
    out["source_analysis"]["evidence_ids"] = [b["id"]]
    with pytest.raises(DomainError, match="unknown_evidence"):
        engine.submit_work(
            p["id"], work["work_id"], work["lease_token"], work["input_hash"], out
        )


def test_learning_requires_exact_experiment_and_actual_observations(engine):
    p, _, r = plan_start(engine, "full-campaign")
    assert drive(engine, p["id"], r["run_id"])["state"] == "waiting_external_result"
    run = engine.inspect_run(p["id"], r["run_id"])["run"]
    with pytest.raises(DomainError, match="observations"):
        engine.control_run(
            p["id"],
            r["run_id"],
            run["version"],
            "resume",
            {"observed_results": {"synthetic": True}},
        )
    # Mechanical fixture simulates an external measured-data import; not a real experiment.
    data = {
        "origin": "test fixture explicitly simulating observed input",
        "synthetic": False,
        "experiment_artifact_id": run["bindings"]["experiment_plan"],
        "observations": [],
    }
    engine.control_run(
        p["id"], r["run_id"], run["version"], "resume", {"observed_results": data}
    )
    assert drive(engine, p["id"], r["run_id"])["state"] == "completed"
