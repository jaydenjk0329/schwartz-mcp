from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import time
import uuid


class DomainError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


def now():
    return time.time()


def root_path():
    root = Path(
        os.environ.get("SCHWARTZ_ROOT", Path(__file__).resolve().parents[2])
    ).resolve()
    if not (root / "semantic/coverage.json").is_file():
        raise DomainError(
            "missing_knowledge", "Set SCHWARTZ_ROOT to the reviewed repository."
        )
    return root


def read_json(path):
    return json.loads(Path(path).read_text())
