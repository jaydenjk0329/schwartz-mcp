from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from threading import Thread
import pytest
from schwartz_mcp.provider import ResponsesProvider
from schwartz_mcp.worker import tick
from schwartz_mcp.common import DomainError
from conftest import plan_start, output


def test_configured_provider_request_worker_commit_and_usage(engine, monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            task = json.loads(body["input"])
            value = {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "status": "completed",
                                        "outputs": output(task),
                                        "questions": [],
                                    }
                                ),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("FIXTURE_PROVIDER_KEY", "not-a-real-key")
    provider = ResponsesProvider(
        {
            "endpoint": f"http://127.0.0.1:{server.server_port}/responses",
            "model": "fixture-model",
            "api_key_env": "FIXTURE_PROVIDER_KEY",
            "input_usd_per_million": 1,
            "output_usd_per_million": 2,
            "max_input_tokens": 500000,
            "max_output_tokens": 4000,
            "timeout_seconds": 30,
        }
    )
    try:
        p, _, r = plan_start(
            engine, "source-study", mode="server_managed", budget={"max_cost_usd": 2}
        )
        with pytest.raises(DomainError, match="mode_conflict"):
            engine.claim_work(p["id"], r["run_id"], "host")
        assert tick(engine, provider, p["id"], r["run_id"])["accepted"]
        assert tick(engine, provider, p["id"], r["run_id"])["state"] == "completed"
        assert requests[0]["store"] is False and requests[0]["text"]["format"]["strict"]
        run = engine.inspect_run(p["id"], r["run_id"])["run"]
        assert (
            run["usage"]["input_tokens"] == 100 and run["usage"]["output_tokens"] == 20
        )
        assert run["usage"]["cost_usd"] == pytest.approx(0.00014)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_missing_provider_credentials_does_not_make_network_request(monkeypatch):
    monkeypatch.delenv("ABSENT_FIXTURE_KEY", raising=False)
    with pytest.raises(DomainError, match="provider_not_configured"):
        ResponsesProvider(
            {
                "endpoint": "https://example.invalid/v1/responses",
                "model": "configured",
                "api_key_env": "ABSENT_FIXTURE_KEY",
                "input_usd_per_million": 1,
                "output_usd_per_million": 1,
                "max_input_tokens": 1000,
                "max_output_tokens": 100,
                "timeout_seconds": 30,
            }
        )


def test_provider_missing_facts_waits_instead_of_fabricating(engine):
    class MissingFacts:
        settings = {"model": "fixture"}
        reservation = {
            "reserve_cost_usd": 0.1,
            "reserve_input_tokens": 1000,
            "reserve_output_tokens": 100,
        }

        def perform(self, task):
            raise DomainError(
                "missing_input", "Provide the actual product specifications"
            )

    p, _, r = plan_start(
        engine, "source-study", mode="server_managed", budget={"max_cost_usd": 1}
    )
    assert (
        tick(engine, MissingFacts(), p["id"], r["run_id"])["state"] == "waiting_input"
    )
    run = engine.inspect_run(p["id"], r["run_id"])["run"]
    assert run["usage"]["cost_usd"] == 0.1
    assert "actual product" in run["open_questions"][0]
