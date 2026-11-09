from __future__ import annotations
import fcntl
import json
import signal
import sys
import threading
import tomllib
from .common import DomainError
from .provider import load_provider
from .budgets import BatchBudget


def tick(engine, provider, project_id, run_id, batch=None):
    reservation = provider.reservation
    token = batch.reserve(run_id, reservation["reserve_cost_usd"]) if batch else None
    try:
        task = engine.claim_work(
            project_id,
            run_id,
            "provider:" + provider.settings["model"],
            mode="server_managed",
            lease_seconds=300,
            **reservation,
        )
    except Exception:
        if batch:
            batch.settle(token, not_dispatched=True)
        raise
    if "work_id" not in task:
        if batch:
            batch.settle(token, not_dispatched=True)
        return task
    usage = None
    try:
        outputs, usage = provider.perform(task)
        if batch:
            batch.settle(token, actual_cost=usage["cost_usd"])
        return engine.submit_work(
            project_id,
            task["work_id"],
            task["lease_token"],
            task["input_hash"],
            outputs,
            usage,
        )
    except Exception as error:
        if batch:
            batch.settle(
                token
            )  # Keep unknown/ambiguous costs reserved, including crashes.
        code = error.code if isinstance(error, DomainError) else "provider_unexpected"
        if code in ("stale_lease", "run_stopped", "input_changed"):
            return {
                "state": engine.inspect_run(project_id, run_id)["run"]["state"],
                "late_result_rejected": True,
            }
        # Unexpected exceptions may embed headers, inputs or raw provider output.
        message = (
            str(error)
            if isinstance(error, DomainError)
            else "Provider execution failed; private exception details omitted"
        )
        failure = (
            "missing_input"
            if code
            in (
                "missing_input",
                "context_budget",
                "provider_configuration",
                "reservation_exceeded",
            )
            else "provider_error"
        )
        try:
            return engine.fail_work(
                project_id,
                task["work_id"],
                task["lease_token"],
                failure,
                message,
                usage=usage if code == "reservation_exceeded" else None,
            )
        except DomainError as late:
            if late.code in ("stale_lease", "run_stopped"):
                return {
                    "state": engine.inspect_run(project_id, run_id)["run"]["state"],
                    "late_result_rejected": True,
                }
            raise


def run_worker(engine, config, once=False, interval=2):
    with open(config, "rb") as f:
        settings = tomllib.load(f)
    provider = load_provider(config)
    batch = BatchBudget(engine.store, settings.get("worker", {}))
    stop = threading.Event()
    old_handlers = {}
    lock_path = engine.store.path.with_suffix(".worker.lock")
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DomainError(
                "worker_running", "Another worker owns this database"
            ) from None
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                old_handlers[sig] = signal.signal(sig, lambda *_: stop.set())
        try:
            while not stop.is_set():
                with engine.store.tx() as con:
                    rows = con.execute(
                        "SELECT id,project_id FROM runs WHERE state IN ('queued','running') ORDER BY id"
                    ).fetchall()
                for r in rows:
                    if stop.is_set():
                        break
                    try:
                        with engine.store.tx() as con:
                            run = engine._run(con, r["id"], r["project_id"])
                        if run["mode"] != "server_managed":
                            continue
                        result = tick(engine, provider, r["project_id"], r["id"], batch)
                        event = {
                            "event": "worker_tick",
                            "run_id": r["id"],
                            "state": result.get("state"),
                            "accepted": result.get("accepted", False),
                        }
                    except DomainError as error:
                        if error.code == "batch_budget_exhausted":
                            raise
                        event = {
                            "event": "worker_run_blocked",
                            "run_id": r["id"],
                            "code": error.code,
                        }
                        # One obsolete or malformed run must not crash all other runs.
                        if error.code in (
                            "snapshot_changed",
                            "artifact_integrity",
                            "evidence_integrity",
                            "plan_integrity",
                        ):
                            engine.control_run(
                                r["project_id"], r["id"], run["version"], "pause"
                            )
                    print(json.dumps(event), file=sys.stderr, flush=True)
                if once:
                    return
                stop.wait(max(1, min(interval, 60)))
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
