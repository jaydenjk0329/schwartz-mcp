"""Project-scoped immutable evidence reads and reference discovery."""

from .common import DomainError, digest


def read_evidence(store, con, project_id, evidence_id):
    evidence = store.get(con, evidence_id, project_id, "evidence")
    if (
        digest([evidence["content"], evidence["origin"], evidence["scope"]])
        != evidence["hash"]
    ):
        raise DomainError(
            "evidence_integrity",
            "Evidence no longer matches its content/origin/scope hash",
        )
    return evidence


def referenced_evidence(value):
    ids = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("evidence_ids", "evidence_refs") and isinstance(child, list):
                ids.update(i for i in child if isinstance(i, str))
            else:
                ids.update(referenced_evidence(child))
    elif isinstance(value, list):
        for child in value:
            ids.update(referenced_evidence(child))
    return ids
