from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sqlite3
import subprocess
from threading import Event, Thread
import pytest
from schwartz_mcp.budgets import BatchBudget
from schwartz_mcp.common import DomainError, canonical
from schwartz_mcp.engine import Engine
from schwartz_mcp.operations import backup_database, restore_database, verify_database
from schwartz_mcp.provider import ResponsesProvider
from schwartz_mcp.storage import Store
from schwartz_mcp.worker import tick
from conftest import ROOT, plan_start, output


@contextmanager
def endpoint(reply):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            task = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            code, headers, body = reply(task)
            self.send_response(code)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(json.dumps(body).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/responses"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def settings(url):
    return {
        "endpoint": url,
        "model": "fixture",
        "api_key_env": "FIXTURE_KEY",
        "input_usd_per_million": 1,
        "output_usd_per_million": 2,
        "max_input_tokens": 500000,
        "max_output_tokens": 4000,
        "timeout_seconds": 10,
    }


def response(task):
    return {
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


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {"status": "completed", "output": None},
        {"status": "completed", "output": [None]},
        {"status": "completed", "output": [{"content": None}]},
        {"status": "completed", "output": [{"content": [None]}]},
        {
            "status": "completed",
            "output": [{"content": [{"type": "output_text", "text": 3}]}],
        },
    ],
)
def test_malformed_provider_envelope_never_crashes_worker(engine, monkeypatch, bad):
    monkeypatch.setenv("FIXTURE_KEY", "secret-fixture")
    p, _, r = plan_start(
        engine, "source-study", mode="server_managed", budget={"max_cost_usd": 2}
    )
    with endpoint(lambda _: (200, {}, bad)) as url:
        result = tick(engine, ResponsesProvider(settings(url)), p["id"], r["run_id"])
    assert result["state"] == "queued"
    assert (
        engine.claim_work(p["id"], r["run_id"], "provider", mode="server_managed")[
            "state"
        ]
        == "retry_wait"
    )
    restarted = Engine(ROOT, engine.store.path)
    assert (
        restarted.claim_work(p["id"], r["run_id"], "provider", mode="server_managed")[
            "state"
        ]
        == "retry_wait"
    )


def test_bearer_is_never_forwarded_on_redirect(engine, monkeypatch):
    monkeypatch.setenv("FIXTURE_KEY", "secret-fixture")
    hits = []
    p, _, r = plan_start(
        engine, "source-study", mode="server_managed", budget={"max_cost_usd": 2}
    )
    with endpoint(lambda req: hits.append(req) or (200, {}, {})) as target:
        with endpoint(lambda _: (307, {"Location": target}, {})) as url:
            result = tick(
                engine, ResponsesProvider(settings(url)), p["id"], r["run_id"]
            )
    assert not hits and result["state"] == "waiting_input"
    assert "secret-fixture" not in canonical(engine.inspect_run(p["id"], r["run_id"]))


def test_unexpected_exception_is_redacted_and_other_run_can_continue(engine):
    class Broken:
        settings = {"model": "fixture"}
        reservation = {
            "reserve_cost_usd": 0.1,
            "reserve_input_tokens": 1000,
            "reserve_output_tokens": 100,
        }

        def perform(self, task):
            raise RuntimeError("secret-fixture private request")

    p, _, r = plan_start(
        engine, "source-study", mode="server_managed", budget={"max_cost_usd": 2}
    )
    result = tick(engine, Broken(), p["id"], r["run_id"])
    assert result["state"] == "queued"
    assert "secret-fixture" not in canonical(engine.inspect_run(p["id"], r["run_id"]))


def test_usage_overrun_is_charged_and_stops_dispatch(engine):
    class Overrun:
        settings = {"model": "fixture"}
        reservation = {
            "reserve_cost_usd": 0.1,
            "reserve_input_tokens": 1000,
            "reserve_output_tokens": 100,
        }

        def perform(self, task):
            return output(task), {
                "cost_usd": 0.3,
                "input_tokens": 1200,
                "output_tokens": 150,
            }

    p, _, r = plan_start(
        engine, "source-study", mode="server_managed", budget={"max_cost_usd": 2}
    )
    batch = BatchBudget(
        engine.store, {"batch_id": "test", "max_model_calls": 2, "max_cost_usd": 1}
    )
    assert (
        tick(engine, Overrun(), p["id"], r["run_id"], batch)["state"]
        == "budget_exhausted"
    )
    run = engine.inspect_run(p["id"], r["run_id"])["run"]
    assert run["usage"]["cost_usd"] == pytest.approx(0.3)
    assert run["usage"]["input_tokens"] == 1200 and batch.inspect()[
        "cost_usd"
    ] == pytest.approx(0.3)


def test_batch_reservations_survive_restart_and_caps_cannot_silently_change(engine):
    config = {"batch_id": "test", "max_model_calls": 2, "max_cost_usd": 1}
    batch = BatchBudget(engine.store, config)
    first = batch.reserve("r1", 0.6)
    with pytest.raises(DomainError, match="batch_budget_exhausted"):
        BatchBudget(Store(engine.store.path), config).reserve("r2", 0.6)
    batch.settle(first, actual_cost=0.2)
    second = batch.reserve("r2", 0.6)
    batch.settle(second)
    assert batch.inspect()["cost_usd"] == pytest.approx(0.8)
    with pytest.raises(DomainError, match="batch_budget_exhausted"):
        batch.reserve("r3", 0.01)
    with pytest.raises(DomainError, match="batch_config_changed"):
        BatchBudget(engine.store, {**config, "max_cost_usd": 2})


def test_atomic_claim_with_competing_clients(engine):
    p, _, r = plan_start(engine, "source-study")

    def claim(i):
        return engine.claim_work(p["id"], r["run_id"], str(i))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(16)))
    assert sum("work_id" in x for x in results) == 1
    assert engine.inspect_run(p["id"], r["run_id"])["run"]["usage"]["model_calls"] == 1


def test_backup_restore_pauses_inflight_work_keeps_charge_and_resumes(engine, tmp_path):
    p, _, r = plan_start(engine, "source-study")
    task = engine.claim_work(p["id"], r["run_id"], "host")
    backup = tmp_path / "backups" / "snapshot.sqlite3"
    backup_database(engine.store.path, backup)
    restored = tmp_path / "restored.sqlite3"
    result = restore_database(backup, restored)
    assert result["health"]["run_states"] == {"paused": 1}
    new = Engine(ROOT, restored)
    run = new.inspect_run(p["id"], r["run_id"])["run"]
    assert run["usage"]["model_calls"] == 1
    with pytest.raises(DomainError, match="stale_lease"):
        new.submit_work(
            p["id"],
            task["work_id"],
            task["lease_token"],
            task["input_hash"],
            output(task),
        )
    new.control_run(p["id"], r["run_id"], run["version"], "resume")
    task = new.claim_work(p["id"], r["run_id"], "restored-host")
    new.submit_work(
        p["id"], task["work_id"], task["lease_token"], task["input_hash"], output(task)
    )
    assert new.claim_work(p["id"], r["run_id"], "restored-host")["state"] == "completed"
    assert verify_database(restored)["integrity"] == "ok"
    assert os.stat(restored).st_mode & 0o777 == 0o600
    with pytest.raises(DomainError, match="destination_exists"):
        restore_database(backup, engine.store.path)
    backup.write_bytes(backup.read_bytes() + b"corrupt")
    with pytest.raises(DomainError, match="backup_integrity"):
        restore_database(backup, tmp_path / "other.sqlite3")


def test_unknown_schema_is_not_mutated(tmp_path):
    path = tmp_path / "unknown.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE metadata(key TEXT,value TEXT)")
        con.execute("INSERT INTO metadata VALUES('schema_version','999')")
    with pytest.raises(DomainError, match="schema_version"):
        Store(path)
    with sqlite3.connect(path) as con:
        assert con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("metadata",)]


def test_modified_evidence_is_rejected_before_provider_dispatch(engine):
    p, _, r = plan_start(engine, "source-study")
    # A frozen input artifact must not be usable after disk mutation.
    run = engine.inspect_run(p["id"], r["run_id"])["run"]
    aid = run["bindings"]["source_request"]
    with engine.store.tx() as con:
        obj = engine.store.get(con, aid, p["id"], "artifact")
        obj["payload"] = {"tampered": True}
        con.execute("UPDATE objects SET body=? WHERE id=?", (canonical(obj), aid))
    with pytest.raises(DomainError, match="artifact_integrity"):
        engine.claim_work(p["id"], r["run_id"], "host")
    with pytest.raises(DomainError, match="artifact_integrity"):
        verify_database(engine.store.path)


def test_killed_provider_worker_keeps_batch_cap_on_restart(engine, tmp_path):
    arrived, release = Event(), Event()
    requests = []

    def reply(body):
        requests.append(body)
        arrived.set()
        release.wait(10)
        return 200, {}, response(json.loads(body["input"]))

    p, _, r = plan_start(
        engine, "source-study", mode="server_managed", budget={"max_cost_usd": 2}
    )
    with endpoint(reply) as url:
        cfg = tmp_path / "provider.toml"
        lines = ["[provider]"] + [
            f"{k} = {json.dumps(v)}" for k, v in settings(url).items()
        ]
        lines += [
            "[worker]",
            'batch_id = "kill-test"',
            "max_model_calls = 1",
            "max_cost_usd = 1.0",
        ]
        cfg.write_text("\n".join(lines))
        command = [
            str(ROOT / ".venv/bin/schwartz"),
            "--db",
            str(engine.store.path),
            "worker",
            "--config",
            str(cfg),
            "--once",
        ]
        env = {
            **os.environ,
            "SCHWARTZ_ROOT": str(ROOT),
            "FIXTURE_KEY": "secret-fixture",
        }
        proc = subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        try:
            assert arrived.wait(10)
            proc.kill()
            proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)
            release.set()
        again = subprocess.run(command, env=env, capture_output=True, timeout=15)
        assert again.returncode != 0 and b"batch_budget_exhausted" in again.stderr
        assert b"secret-fixture" not in again.stderr and len(requests) == 1
    run = engine.inspect_run(p["id"], r["run_id"])["run"]
    assert run["usage"]["model_calls"] == 1
    assert run["usage"]["cost_usd"] > 0


@pytest.mark.parametrize(
    "budget",
    [
        {"max_model_calls": True},
        {"max_tool_calls": 1.5},
        {"max_input_tokens": float("inf")},
    ],
)
def test_noninteger_and_nonfinite_budget_caps_rejected(engine, budget):
    from conftest import project

    p = project(engine)
    with pytest.raises(DomainError, match="budget"):
        engine.plan_run(p["id"], "source-study", budget=budget)


def test_host_reported_overrun_is_visible_and_stops_new_dispatch(engine):
    p, _, r = plan_start(engine, "source-study", budget={"max_cost_usd": 0.1})
    task = engine.claim_work(p["id"], r["run_id"], "host")
    result = engine.submit_work(
        p["id"],
        task["work_id"],
        task["lease_token"],
        task["input_hash"],
        output(task),
        {"cost_usd": 0.2, "input_tokens": 1, "output_tokens": 1},
    )
    assert result["state"] == "budget_exhausted"
    assert (
        engine.claim_work(p["id"], r["run_id"], "host")["state"] == "budget_exhausted"
    )


def test_provider_boolean_usage_is_not_a_token_measurement(engine, monkeypatch):
    monkeypatch.setenv("FIXTURE_KEY", "secret-fixture")
    p, _, r = plan_start(
        engine, "source-study", mode="server_managed", budget={"max_cost_usd": 2}
    )

    def reply(body):
        result = response(json.loads(body["input"]))
        result["usage"]["input_tokens"] = True
        return 200, {}, result

    with endpoint(reply) as url:
        assert (
            tick(engine, ResponsesProvider(settings(url)), p["id"], r["run_id"])[
                "state"
            ]
            == "queued"
        )
    assert (
        engine.inspect_run(p["id"], r["run_id"])["run"]["usage"]["input_tokens"]
        == 500000
    )


def test_project_and_run_discovery_is_paginated_and_scoped(engine):
    from conftest import project

    p, _, r = plan_start(engine, "source-study")
    other = project(engine, "other")
    page = engine.list_projects(limit=1)
    assert len(page["projects"]) == 1 and page["next_cursor"]
    next_page = engine.list_projects(after_id=page["next_cursor"], limit=1)
    assert {page["projects"][0]["id"], next_page["projects"][0]["id"]} == {
        p["id"],
        other["id"],
    }
    assert next_page["next_cursor"] is None
    assert engine.list_runs(other["id"])["runs"] == []
    assert engine.list_runs(p["id"])["runs"][0]["id"] == r["run_id"]
