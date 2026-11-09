"""Local maintenance operations. Restores never overwrite an existing database."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from .common import DomainError, canonical, now
from .storage import verify_object


def connect_readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def verify_database(path):
    try:
        with closing(connect_readonly(path)) as con:
            con.execute("BEGIN")  # Consistent view while live work continues.
            if con.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise DomainError("database_integrity", "SQLite integrity check failed")
            if con.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone() != ("1",):
                raise DomainError("schema_version", "Unsupported runtime schema")
            objects = {}
            for ident, project_id, kind, body in con.execute(
                "SELECT id,project_id,kind,body FROM objects"
            ):
                obj = json.loads(body)
                if obj.get("id") != ident or (
                    kind in ("artifact", "evidence", "plan")
                    and obj.get("project_id") != project_id
                ):
                    raise DomainError(
                        "database_integrity", "Stored object identity mismatch"
                    )
                verify_object(obj, kind)
                objects[ident] = (project_id, kind, obj)
            runs = {}
            states = {}
            for ident, pid, state, version, body in con.execute(
                "SELECT id,project_id,state,version,body FROM runs"
            ):
                run = json.loads(body)
                if (run["id"], run["project_id"], run["state"], run["version"]) != (
                    ident,
                    pid,
                    state,
                    version,
                ):
                    raise DomainError(
                        "database_integrity", "Run columns and body disagree"
                    )
                refs = (
                    [(run["plan_id"], "plan")]
                    + [(a, "artifact") for a in run["bindings"].values()]
                    + [(e, "evidence") for e in run["evidence_ids"]]
                )
                if any(
                    i not in objects or objects[i][:2] != (pid, kind)
                    for i, kind in refs
                ):
                    raise DomainError(
                        "database_integrity",
                        "Run has missing or foreign-project references",
                    )
                runs[ident] = run
                states[state] = states.get(state, 0) + 1
            for pid, kind, obj in objects.values():
                if kind == "artifact" and any(
                    i not in objects or objects[i][:2] != (pid, "artifact")
                    for i in obj["parent_ids"]
                ):
                    raise DomainError(
                        "database_integrity",
                        "Artifact dependency is missing or outside its project",
                    )
            for (run_id,) in con.execute("SELECT DISTINCT run_id FROM steps"):
                if run_id not in runs:
                    raise DomainError("database_integrity", "Work has no owning run")
            stale = con.execute(
                "SELECT count(*) FROM steps WHERE status='leased' AND expires<=?",
                (now(),),
            ).fetchone()[0]
            return {
                "integrity": "ok",
                "schema_version": 1,
                "objects": len(objects),
                "runs": len(runs),
                "run_states": states,
                "expired_leases": stale,
            }
    except (sqlite3.DatabaseError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, DomainError):
            raise
        raise DomainError(
            "database_integrity",
            "Database cannot be read or fails structural validation",
        ) from None


def _sha(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _publish_new(temp, target):
    try:
        os.link(temp, target)  # Atomic refusal to overwrite an existing destination.
    except FileExistsError:
        raise DomainError(
            "destination_exists", "Choose a new backup/restore destination"
        ) from None
    os.unlink(temp)
    fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def backup_database(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    manifest = destination.with_suffix(destination.suffix + ".json")
    if destination.exists() or manifest.exists():
        raise DomainError(
            "destination_exists", "Backup path or manifest already exists"
        )
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with NamedTemporaryFile(
        dir=destination.parent, prefix=".backup-", delete=False
    ) as f:
        temp = Path(f.name)
    try:
        with (
            closing(connect_readonly(source)) as src,
            closing(sqlite3.connect(temp)) as dst,
        ):
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
        health = verify_database(temp)
        result = {
            "format_version": 1,
            "created_at": now(),
            "database_sha256": _sha(temp),
            "health": health,
            "scope": "runtime database only; keep the reviewed source checkout separately",
        }
        with open(temp, "rb") as f:
            os.fsync(f.fileno())
        _publish_new(temp, destination)
        fd = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(result, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        return {"path": str(destination), "manifest_path": str(manifest), **result}
    finally:
        temp.unlink(missing_ok=True)


def restore_database(backup, destination):
    backup, destination = Path(backup).resolve(), Path(destination).resolve()
    if destination.exists():
        raise DomainError(
            "destination_exists",
            "Restore requires a new path; the live database is never overwritten",
        )
    try:
        manifest = json.loads(backup.with_suffix(backup.suffix + ".json").read_text())
        if manifest.get("format_version") != 1 or manifest.get(
            "database_sha256"
        ) != _sha(backup):
            raise DomainError(
                "backup_integrity", "Backup checksum or format is invalid"
            )
    except (OSError, ValueError) as error:
        if isinstance(error, DomainError):
            raise
        raise DomainError(
            "backup_integrity", "Backup manifest is missing or invalid"
        ) from None
    verify_database(backup)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with NamedTemporaryFile(
        dir=destination.parent, prefix=".restore-", delete=False
    ) as f:
        temp = Path(f.name)
    try:
        with (
            closing(connect_readonly(backup)) as src,
            closing(sqlite3.connect(temp)) as dst,
        ):
            src.backup(dst)
            # No automatically dispatched work after disaster recovery. Reservations
            # remain charged because external attempts may have completed already.
            for ident, body in dst.execute(
                "SELECT id,body FROM runs WHERE state IN ('queued','running')"
            ).fetchall():
                run = json.loads(body)
                run["state"] = "paused"
                run["version"] += 1
                run["open_questions"] = [
                    "Restored backup: inspect prior external attempts before resuming."
                ]
                dst.execute(
                    "UPDATE runs SET state=?,version=?,body=? WHERE id=?",
                    ("paused", run["version"], canonical(run), ident),
                )
                dst.execute(
                    "INSERT INTO events(run_id,kind,at,body) VALUES(?,?,?,?)",
                    (
                        ident,
                        "restore_paused",
                        now(),
                        canonical({"reservation_policy": "retained"}),
                    ),
                )
            dst.execute(
                "UPDATE steps SET status='pending',token=NULL,expires=NULL WHERE status='leased'"
            )
            dst.commit()
            dst.execute("PRAGMA journal_mode=DELETE")
        health = verify_database(temp)
        with open(temp, "rb") as f:
            os.fsync(f.fileno())
        _publish_new(temp, destination)
        return {
            "path": str(destination),
            "health": health,
            "previously_active_runs": "paused",
            "reservations": "retained",
        }
    finally:
        temp.unlink(missing_ok=True)
