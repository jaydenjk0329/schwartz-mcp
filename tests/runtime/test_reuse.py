import pytest
from schwartz_mcp.common import DomainError
from schwartz_mcp.lineage import inspect_lineage
from conftest import plan_start, drive, output, project


def test_reuses_committed_prefix_and_regenerates_revision_suffix(engine):
    p, old_plan, original = plan_start(engine, "full-campaign")
    drive(engine, p["id"], original["run_id"])
    old = engine.inspect_run(p["id"], original["run_id"])["run"]
    plan = engine.plan_run(
        p["id"],
        "full-campaign",
        reuse_from_run_id=original["run_id"],
        restart_from_stage="offer",
        revision_instructions="Retain real terms; clarify the guarantee.",
    )
    assert plan["reuse"]["reused_step_count"] > 0
    assert not plan["reuse"]["blockers"]
    fork = engine.start_run(p["id"], plan["id"], plan["hash"], "host_driven", "fork")
    before = engine.inspect_run(p["id"], fork["run_id"])["run"]
    assert before["usage"]["model_calls"] == 0
    assert before["bindings"]["product_map"] == old["bindings"]["product_map"]
    work = engine.claim_work(p["id"], fork["run_id"], "fixture")
    assert work["stage_id"] == "offer"
    assert "guarantee" in work["revision_instructions"]
    engine.submit_work(
        p["id"], work["work_id"], work["lease_token"], work["input_hash"], output(work)
    )
    assert drive(engine, p["id"], fork["run_id"])["state"] == "waiting_external_result"
    new = engine.inspect_run(p["id"], fork["run_id"])["run"]
    assert new["bindings"]["offer_plan"] != old["bindings"]["offer_plan"]
    assert (
        new["bindings"]["selected_candidates"] != old["bindings"]["selected_candidates"]
    )
    assert engine.inspect_run(p["id"], original["run_id"])["run"] == old
    impact = inspect_lineage(engine, p["id"], old["bindings"]["offer_plan"])
    assert old["bindings"]["selected_candidates"] in impact["dependent_artifacts"]
    assert new["bindings"]["selected_candidates"] not in impact["dependent_artifacts"]


@pytest.mark.parametrize("change", ["brief", "evidence", "profile"])
def test_changed_context_invalidates_reuse(engine, change):
    p, _, original = plan_start(engine, "source-study")
    drive(engine, p["id"], original["run_id"])
    kwargs = {"reuse_from_run_id": original["run_id"]}
    if change == "brief":
        kwargs["brief_override"] = {"product": "Changed product"}
    if change == "profile":
        kwargs["profile"] = {"generators": 2}
    if change == "evidence":
        engine.add_evidence(p["id"], {"fact": "New"}, "fixture", "test", "new")
    plan = engine.plan_run(p["id"], "source-study", **kwargs)
    assert plan["reuse"]["reused_step_count"] == 0
    assert plan["reuse"]["blockers"]


def test_full_reuse_costs_no_model_calls_and_can_be_reused_again(engine):
    p, _, original = plan_start(engine, "source-study")
    drive(engine, p["id"], original["run_id"])
    for i in range(2):
        plan = engine.plan_run(
            p["id"], "source-study", reuse_from_run_id=original["run_id"]
        )
        assert plan["reuse"]["reused_step_count"] == 1
        original = engine.start_run(
            p["id"], plan["id"], plan["hash"], "host_driven", "reuse" + str(i)
        )
        assert drive(engine, p["id"], original["run_id"])["state"] == "completed"
        assert (
            engine.inspect_run(p["id"], original["run_id"])["run"]["usage"][
                "model_calls"
            ]
            == 0
        )


def test_cross_project_reuse_rejected_and_inflight_not_reused(engine):
    p, _, original = plan_start(engine, "source-study")
    engine.claim_work(p["id"], original["run_id"], "fixture")
    plan = engine.plan_run(
        p["id"], "source-study", reuse_from_run_id=original["run_id"]
    )
    assert plan["reuse"]["reused_step_count"] == 0
    other = project(engine, "another")
    with pytest.raises(DomainError, match="not_found"):
        engine.plan_run(
            other["id"], "source-study", reuse_from_run_id=original["run_id"]
        )
