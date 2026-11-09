from __future__ import annotations
from contextlib import contextmanager
from contextlib import closing
import json
import os
import sqlite3
from .common import canonical, DomainError, now, digest


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Restrictive creation before SQLite creates WAL/SHM sidecars.
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        with closing(self.connect()) as con:
            existing = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='metadata'"
            ).fetchone()
            if existing:
                version = con.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()
                if not version or version[0] != "1":
                    raise DomainError(
                        "schema_version",
                        "Unsupported database version; no migration performed",
                    )
            elif con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone():
                raise DomainError(
                    "schema_version", "Refusing to initialize an unrecognized database"
                )
            con.executescript("""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT OR IGNORE INTO metadata VALUES('schema_version','1');
CREATE TABLE IF NOT EXISTS objects(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS object_project_kind ON objects(project_id,kind);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,state TEXT NOT NULL,version INTEGER NOT NULL,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS steps(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,position INTEGER NOT NULL,status TEXT NOT NULL,token TEXT,expires REAL,body TEXT NOT NULL,UNIQUE(run_id,position));
CREATE TABLE IF NOT EXISTS requests(scope TEXT NOT NULL,key TEXT NOT NULL,hash TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(scope,key));
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,kind TEXT NOT NULL,at REAL NOT NULL,body TEXT NOT NULL);
""")
            if (
                con.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()[0]
                != "1"
            ):
                raise DomainError("schema_version", "Unsupported database version")

    def connect(self):
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    @contextmanager
    def tx(self):
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def put(self, con, obj, kind, project_id):
        con.execute(
            "INSERT INTO objects VALUES(?,?,?,?)",
            (obj["id"], project_id, kind, canonical(obj)),
        )

    def get(self, con, ident, project_id=None, kind=None):
        row = con.execute("SELECT * FROM objects WHERE id=?", (ident,)).fetchone()
        if (
            not row
            or (project_id is not None and row["project_id"] != project_id)
            or (kind and row["kind"] != kind)
        ):
            raise DomainError("not_found", "Object unavailable in this project/scope")
        obj = json.loads(row["body"])
        verify_object(obj, row["kind"])
        return obj

    def cached(self, con, scope, key, request_hash):
        if not key or len(key) > 200:
            raise DomainError(
                "idempotency_key",
                "A nonempty key of at most 200 characters is required",
            )
        row = con.execute(
            "SELECT * FROM requests WHERE scope=? AND key=?", (scope, key)
        ).fetchone()
        if row and row["hash"] != request_hash:
            raise DomainError(
                "idempotency_conflict", "Key already used for different arguments"
            )
        return json.loads(row["result"]) if row else None

    def remember(self, con, scope, key, request_hash, result):
        con.execute(
            "INSERT INTO requests VALUES(?,?,?,?)",
            (scope, key, request_hash, canonical(result)),
        )

    def event(self, con, run_id, kind, body):
        con.execute(
            "INSERT INTO events(run_id,kind,at,body) VALUES(?,?,?,?)",
            (run_id, kind, now(), canonical(body)),
        )


def verify_object(obj, kind):
    try:
        expected = None
        if kind == "artifact":
            expected = digest(obj["payload"])
        elif kind == "evidence":
            expected = digest([obj["content"], obj["origin"], obj["scope"]])
        elif kind == "plan":
            expected = digest({k: v for k, v in obj.items() if k != "hash"})
        if expected is not None and obj.get("hash") != expected:
            raise DomainError(
                kind + "_integrity",
                "Stored record no longer matches its integrity hash",
            )
    except (KeyError, TypeError, ValueError) as e:
        if isinstance(e, DomainError):
            raise
        raise DomainError(kind + "_integrity", "Malformed stored record") from None
