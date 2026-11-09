"""Durable provider batch caps; ambiguous attempts retain their reservation."""

import math
from .common import DomainError, canonical, digest, identifier, now


class BatchBudget:
    def __init__(self, store, config):
        if (
            not isinstance(config.get("batch_id"), str)
            or not 1 <= len(config["batch_id"]) <= 100
        ):
            raise DomainError(
                "batch_config",
                "Set a stable, nonempty batch_id (maximum 100 characters)",
            )
        if (
            type(config.get("max_model_calls")) is not int
            or not 1 <= config["max_model_calls"] <= 10000
        ):
            raise DomainError("batch_config", "Batch max_model_calls must be 1–10000")
        cost = config.get("max_cost_usd")
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise DomainError(
                "batch_config", "Batch max_cost_usd must be finite and nonnegative"
            )
        self.store = store
        self.id = "worker_batch_" + digest(config["batch_id"])[:32]
        self.config = {
            k: config[k] for k in ("batch_id", "max_model_calls", "max_cost_usd")
        }
        with store.tx() as con:
            row = con.execute(
                "SELECT body FROM objects WHERE id=?", (self.id,)
            ).fetchone()
            if row:
                batch = store.get(con, self.id, "__operations__", "worker_batch")
                if batch["config"] != self.config:
                    raise DomainError(
                        "batch_config_changed",
                        "Existing batch caps are immutable; inspect the batch before explicitly choosing a new batch ID",
                    )
            else:
                store.put(
                    con,
                    {
                        "id": self.id,
                        "config": self.config,
                        "model_calls": 0,
                        "cost_usd": 0.0,
                        "attempts": {},
                        "created_at": now(),
                    },
                    "worker_batch",
                    "__operations__",
                )

    def _save(self, con, batch):
        con.execute("UPDATE objects SET body=? WHERE id=?", (canonical(batch), self.id))

    def inspect(self):
        with self.store.tx() as con:
            return self.store.get(con, self.id, "__operations__", "worker_batch")

    def reserve(self, run_id, cost):
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise DomainError("reservation", "Invalid batch reservation")
        with self.store.tx() as con:
            b = self.store.get(con, self.id, "__operations__", "worker_batch")
            if (
                b["model_calls"] + 1 > b["config"]["max_model_calls"]
                or b["cost_usd"] + cost > b["config"]["max_cost_usd"]
            ):
                raise DomainError(
                    "batch_budget_exhausted",
                    "Provider batch cap reached; no new request dispatched",
                )
            token = identifier("attempt")
            b["attempts"][token] = {
                "run_id": run_id,
                "cost_usd": cost,
                "status": "reserved",
                "at": now(),
            }
            b["model_calls"] += 1
            b["cost_usd"] += cost
            self._save(con, b)
            return token

    def settle(self, token, actual_cost=None, not_dispatched=False):
        with self.store.tx() as con:
            b = self.store.get(con, self.id, "__operations__", "worker_batch")
            a = b["attempts"][token]
            if a["status"] != "reserved":
                return
            if not_dispatched:
                b["model_calls"] -= 1
                b["cost_usd"] -= a["cost_usd"]
                # No external request occurred. Do not accumulate polling entries.
                del b["attempts"][token]
            else:
                if actual_cost is not None:
                    if (
                        type(actual_cost) not in (int, float)
                        or not math.isfinite(actual_cost)
                        or actual_cost < 0
                    ):
                        raise DomainError("usage", "Invalid reported batch cost")
                    b["cost_usd"] += actual_cost - a["cost_usd"]
                    a["cost_usd"] = actual_cost
                a["status"] = (
                    "reported" if actual_cost is not None else "unknown_reserved"
                )
            # Floating-point cancellation must not create a negative balance.
            b["cost_usd"] = max(0.0, b["cost_usd"])
            self._save(con, b)
