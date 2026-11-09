import os
import sys
import json
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from conftest import ROOT


async def test_actual_stdio_client_roundtrip_and_reconnect(tmp_path):
    config = json.loads((ROOT / "examples/mcp-config.json").read_text())["mcpServers"][
        "schwartz"
    ]
    assert config["command"] == "uv"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "schwartz_mcp.server"],
        cwd=str(ROOT),
        env={
            **os.environ,
            "SCHWARTZ_ROOT": str(ROOT),
            "SCHWARTZ_DB": str(tmp_path / "mcp.sqlite3"),
        },
    )
    async with Client(parameters, raise_exceptions=True) as client:
        listing = await client.list_tools()
        names = {t.name for t in listing.tools}
        assert len(names) == 27
        assert {
            "search_knowledge",
            "claim_work",
            "submit_work",
            "run_tournament",
            "audit_coverage",
            "get_evidence",
        } <= names
        result = await client.call_tool("audit_coverage", {})
        assert result.structured_content["missing_details_remain_flagged"]
        assert not result.structured_content["coverage"]["full_published_book_complete"]
        result = await client.call_tool(
            "create_project",
            {
                "brief": {"product": "Protocol fixture"},
                "settings": {},
                "idempotency_key": "stdio-fixture",
            },
        )
        pid = result.structured_content["id"]
        added = await client.call_tool(
            "add_evidence",
            {
                "project_id": pid,
                "content": {"fact": "Protocol fixture"},
                "origin": "stdio test",
                "scope": "test only",
                "idempotency_key": "evidence",
            },
        )
        evidence = await client.call_tool(
            "get_evidence",
            {"project_id": pid, "evidence_id": added.structured_content["id"]},
        )
        assert evidence.structured_content == added.structured_content
        search = await client.call_tool(
            "search_knowledge", {"query": "headline-laboratory"}
        )
        assert search.structured_content["results"] == []
        result = await client.call_tool(
            "plan_run", {"project_id": pid, "workflow_id": "source-study"}
        )
        plan = result.structured_content
        result = await client.call_tool(
            "start_run",
            {
                "project_id": pid,
                "plan_id": plan["id"],
                "plan_hash": plan["hash"],
                "mode": "host_driven",
                "idempotency_key": "run-fixture",
            },
        )
        rid = result.structured_content["run_id"]
        resources = await client.list_resources()
        assert any(
            str(r.uri) == "schwartz://sources/current" for r in resources.resources
        )
    async with Client(parameters, raise_exceptions=True) as client:
        projects = await client.call_tool("list_projects", {})
        assert pid in {p["id"] for p in projects.structured_content["projects"]}
        runs = await client.call_tool("list_runs", {"project_id": pid})
        assert rid in {r["id"] for r in runs.structured_content["runs"]}
        result = await client.call_tool(
            "inspect_run", {"project_id": pid, "run_id": rid}
        )
        assert result.structured_content["run"]["state"] == "queued"
        result = await client.call_tool(
            "claim_work",
            {"project_id": pid, "run_id": rid, "worker_identity": "stdio-host"},
        )
        work = result.structured_content
        assert (
            work["stage_id"] == "study" and work["knowledge_bundle"]["global_gap_ids"]
        )
        from conftest import output

        result = await client.call_tool(
            "submit_work",
            {
                "project_id": pid,
                "work_id": work["work_id"],
                "lease_token": work["lease_token"],
                "input_hash": work["input_hash"],
                "outputs": output(work),
            },
        )
        assert result.structured_content["accepted"]
        result = await client.call_tool(
            "claim_work",
            {"project_id": pid, "run_id": rid, "worker_identity": "stdio-host"},
        )
        assert result.structured_content["state"] == "completed"
