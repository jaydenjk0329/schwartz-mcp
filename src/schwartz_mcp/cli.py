import argparse
import json
import os
from .common import root_path
from .engine import Engine


def _main():
    parser = argparse.ArgumentParser(description="Private Schwartz campaign runtime")
    parser.add_argument("--db", default=os.environ.get("SCHWARTZ_DB"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser(
        "batches", help="Inspect durable provider batch caps and charged attempts"
    )
    backup = sub.add_parser(
        "backup", help="Create a verified SQLite snapshot and checksum manifest"
    )
    backup.add_argument("--destination", required=True)
    restore = sub.add_parser(
        "restore", help="Restore to a new database with active work paused"
    )
    restore.add_argument("--backup", required=True)
    restore.add_argument("--destination", required=True)
    evaluate = sub.add_parser(
        "evaluate", help="Run local development checks without provider calls"
    )
    evaluate.add_argument("--suite", choices=["retrieval"], default="retrieval")
    evaluate.add_argument("--report")
    worker = sub.add_parser("worker")
    worker.add_argument("--config", required=True)
    worker.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.command == "restore":
        from .operations import restore_database

        print(json.dumps(restore_database(args.backup, args.destination), indent=2))
        return
    engine = Engine(root_path(), args.db)
    engine.knowledge.verify()
    if args.command == "doctor":
        import importlib.metadata
        from .operations import verify_database

        print(
            json.dumps(
                {
                    "runtime_version": __import__("schwartz_mcp").__version__,
                    "sdk": importlib.metadata.version("mcp"),
                    "database": str(engine.store.path),
                    "database_health": verify_database(engine.store.path),
                    "source_version": engine.knowledge.source_version,
                    "workflows": len(engine.registry.workflows),
                    "stages": len(engine.registry.stages),
                    "channels": len(engine.registry.channels),
                    "source_gaps_flagged": True,
                    "autonomous_provider": "requires explicit configuration; no credentials checked",
                },
                indent=2,
            )
        )
    elif args.command == "batches":
        with engine.store.tx() as con:
            batches = [
                json.loads(row["body"])
                for row in con.execute(
                    "SELECT body FROM objects WHERE kind='worker_batch' ORDER BY id"
                )
            ]
        print(
            json.dumps(
                {
                    "batches": [
                        {
                            "id": b["id"],
                            "config": b["config"],
                            "model_calls": b["model_calls"],
                            "cost_usd": b["cost_usd"],
                            "unresolved_attempts": sum(
                                a["status"] in ("reserved", "unknown_reserved")
                                for a in b["attempts"].values()
                            ),
                        }
                        for b in batches
                    ]
                },
                indent=2,
            )
        )
    elif args.command == "backup":
        from .operations import backup_database

        print(
            json.dumps(backup_database(engine.store.path, args.destination), indent=2)
        )
    elif args.command == "evaluate":
        from pathlib import Path
        from .common import read_json
        from .evaluation import evaluate_retrieval

        result = evaluate_retrieval(
            engine.knowledge, read_json(root_path() / "evaluation/retrieval-cases.json")
        )
        report = json.dumps(result, indent=2) + "\n"
        if args.report:
            Path(args.report).write_text(report)
        print(report)
        if not result["meets_development_gate"]:
            raise SystemExit(1)
    elif args.command == "worker":
        from .worker import run_worker

        run_worker(engine, args.config, args.once)


def main():
    import sys
    from .common import DomainError

    try:
        _main()
    except DomainError as error:
        print(json.dumps({"error": error.code, "message": str(error)}), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
