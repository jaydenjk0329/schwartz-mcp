import json
from pathlib import Path
from html.parser import HTMLParser
import pytest
from schwartz_mcp.common import DomainError
from schwartz_mcp.exports import assemble_campaign, export_bundle, render_report
from conftest import plan_start, drive


def test_multichannel_bundle_is_ordered_auditable_and_locally_rendered(engine):
    # Separate project idempotency and run start keys: both variants share one project.
    from conftest import project

    p = project(engine)
    runs = []
    for channel in ("email", "sales-page"):
        plan = engine.plan_run(
            p["id"],
            "channel-adaptation",
            inputs={"channel": channel},
            profile={
                "generators": 1,
                "debate_rounds": 0,
                "judges": 1,
                "reverse_pairs": 0,
            },
        )
        run = engine.start_run(
            p["id"], plan["id"], plan["hash"], "host_driven", channel
        )
        assert drive(engine, p["id"], run["run_id"])["state"] == "completed"
        runs.append(run["run_id"])
    bundle = assemble_campaign(engine, p["id"], runs, "bundle")
    assert assemble_campaign(engine, p["id"], runs, "bundle") == bundle
    assert [v["channel"] for v in bundle["payload"]["variants"]] == [
        "email",
        "sales-page",
    ]
    assert not bundle["payload"]["cross_channel_ranking"] and bundle["gap_ids"]
    result = export_bundle(engine, p["id"], bundle["id"])
    document = json.loads(Path(result["path"]).read_text())
    assert len(document["variants"]) == 2
    ids = {a["id"] for a in document["artifacts"]}
    assert all(set(a["parent_ids"]) <= ids for a in document["artifacts"])
    html = Path(result["html_path"]).read_text()
    assert "Offer terms to preserve" in html and "Source limitations" in html
    assert "email" in html and "sales-page" in html
    assert not result["published"]


def test_changed_objective_cannot_enter_same_campaign_bundle(engine):
    from conftest import project

    p = project(engine)
    runs = []
    for channel, objective in [
        ("email", "Acquire customers"),
        ("sales-page", "Recruit staff"),
    ]:
        plan = engine.plan_run(
            p["id"],
            "channel-adaptation",
            inputs={"channel": channel},
            brief_override={**p["brief"], "objective": objective},
            profile={
                "generators": 1,
                "debate_rounds": 0,
                "judges": 1,
                "reverse_pairs": 0,
            },
        )
        run = engine.start_run(
            p["id"], plan["id"], plan["hash"], "host_driven", channel
        )
        drive(engine, p["id"], run["run_id"])
        runs.append(run["run_id"])
    with pytest.raises(DomainError, match="context_mismatch"):
        assemble_campaign(engine, p["id"], runs, "bad")


def test_html_treats_candidate_markup_as_text(engine):
    p, _, run = plan_start(engine, "full-campaign")
    drive(engine, p["id"], run["run_id"])
    exported = engine.export_campaign(p["id"], run["run_id"])
    doc = json.loads(Path(exported["path"]).read_text())
    target = next(
        a
        for a in doc["artifacts"]
        if a["id"] == doc["variants"][0]["selected_artifact_id"]
    )
    target["payload"]["candidates"][0]["content"] = (
        '<script>alert(1)</script><img src="https://example.invalid/track">'
    )
    html = render_report(doc["package"], doc["artifacts"], doc["variants"])

    class Tags(HTMLParser):
        tags = []

        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)

    parser = Tags()
    parser.feed(html)
    assert "script" not in parser.tags and "img" not in parser.tags
    assert "&lt;script&gt;" in html and "default-src 'none'" in html


def test_conflicting_offer_terms_block_assembly(engine):
    from conftest import project, output

    p = project(engine)
    runs = []
    for channel, price in [("email", "$10"), ("sales-page", "$20")]:
        plan = engine.plan_run(
            p["id"],
            "channel-adaptation",
            inputs={"channel": channel},
            profile={
                "generators": 1,
                "debate_rounds": 0,
                "judges": 1,
                "reverse_pairs": 0,
            },
        )
        run = engine.start_run(
            p["id"], plan["id"], plan["hash"], "host_driven", channel
        )
        for _ in range(100):
            work = engine.claim_work(p["id"], run["run_id"], "fixture")
            if "work_id" not in work:
                break
            outputs = output(work)
            if "offer_plan" in outputs:
                outputs["offer_plan"]["price"] = price
            engine.submit_work(
                p["id"],
                work["work_id"],
                work["lease_token"],
                work["input_hash"],
                outputs,
            )
        runs.append(run["run_id"])
    with pytest.raises(DomainError, match="offer_mismatch"):
        assemble_campaign(engine, p["id"], runs, "conflicting")


@pytest.mark.parametrize(
    "channel",
    [
        c["id"]
        for c in json.loads(
            (
                Path(__file__).resolve().parents[2] / "workflows/channel-adapters.json"
            ).read_text()
        )
    ],
)
def test_each_channel_receives_its_contract_and_exports(engine, channel):
    from conftest import output

    p, _, run = plan_start(engine, "channel-adaptation", inputs={"channel": channel})
    adapted = False
    for _ in range(100):
        work = engine.claim_work(p["id"], run["run_id"], "fixture")
        if "work_id" not in work:
            break
        if work["stage_id"] == "adapt":
            assert [a["id"] for a in work["channel_adapters"]] == [channel]
            assert work["strategy_bundle"]["offer_plan"]["payload"]
            adapted = True
        engine.submit_work(
            p["id"],
            work["work_id"],
            work["lease_token"],
            work["input_hash"],
            output(work),
        )
    assert adapted
    result = engine.export_campaign(p["id"], run["run_id"])
    assert Path(result["html_path"]).is_file()
