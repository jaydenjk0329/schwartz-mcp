"""Local, inert review exports and cross-channel package consistency checks."""

from html import escape
import json
import os
from tempfile import NamedTemporaryFile
from .common import DomainError, digest
from .evidence import read_evidence, referenced_evidence

OFFER_FIELDS = (
    "price",
    "terms",
    "delivery",
    "guarantee",
    "limitations_of_offer",
    "reason_for_terms",
)


def collect_artifacts(engine, con, project_id, ids):
    artifacts, included, pending = [], set(), list(ids)
    while pending:
        aid = pending.pop()
        if aid in included:
            continue
        a = engine.store.get(con, aid, project_id, "artifact")
        if digest(a["payload"]) != a["hash"]:
            raise DomainError(
                "artifact_integrity", "Artifact payload no longer matches its hash"
            )
        included.add(aid)
        artifacts.append(a)
        pending.extend(a["parent_ids"])
        if a["kind"] == "campaign_package":
            pending.extend(a["payload"]["artifact_ids"])
    return sorted(artifacts, key=lambda a: a["id"])


def render_report(package, artifacts, variants, evidence=()):
    byid = {a["id"]: a for a in artifacts}
    chunks = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<title>Campaign review package</title><style>body{overflow-wrap:anywhere;font:16px/1.55 system-ui;margin:0;background:#f4f3ef;color:#222}main{max-width:1000px;margin:auto;padding:40px 24px}h1,h2,h3{line-height:1.2}section,article,details{background:white;padding:24px;margin:18px 0;border:1px solid #d6d3cb;border-radius:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.5 ui-monospace,monospace}.copy{white-space:pre-wrap;font-size:20px}.note{color:#624a14}code{overflow-wrap:anywhere}summary{cursor:pointer}li{margin:8px 0}@media print{body{background:white}section,article{break-inside:avoid}}</style></head><body><main>",
        '<h1>Campaign review package</h1><p class="note">Review copy only. Not published. Judge preferences are not measured advertising performance. Source gaps remain unresolved.</p>',
        "<p>Package <code>" + escape(package["id"]) + "</code></p>",
    ]
    for index, variant in enumerate(variants, 1):
        selected = byid[variant["selected_artifact_id"]]
        chunks.append(
            "<section><h2>" + str(index) + ". " + escape(variant["channel"]) + "</h2>"
        )
        chunks.append(
            "<p>Run <code>"
            + escape(variant["run_id"])
            + "</code>; asset version <code>"
            + escape(selected["id"])
            + "</code></p>"
        )
        for c in selected["payload"]["candidates"]:
            chunks.append(
                "<article><h3>"
                + escape(c["id"])
                + '</h3><div class="copy">'
                + escape(c["content"])
                + "</div>"
            )
            chunks.append(
                "<p><strong>Rationale:</strong> " + escape(c["rationale"]) + "</p>"
            )
            chunks.append(
                "<details><summary>Claims, evidence, source basis, and limitations</summary><pre>"
                + escape(
                    json.dumps(
                        {k: v for k, v in c.items() if k != "content"},
                        indent=2,
                        ensure_ascii=False,
                    )
                )
                + "</pre></details></article>"
            )
        offer = byid[variant["offer_artifact_id"]]["payload"]
        chunks.append(
            "<h3>Offer terms to preserve</h3><pre>"
            + escape(
                json.dumps(
                    {k: offer[k] for k in OFFER_FIELDS}, indent=2, ensure_ascii=False
                )
            )
            + "</pre></section>"
        )
    chunks.append(
        "<section><h2>Source limitations</h2><p>Complete published-book coverage and word-level transcription are not certified.</p><ul>"
    )
    chunks.extend("<li>" + escape(g) + "</li>" for g in package["gap_ids"])
    chunks.append(
        "</ul></section><section><h2>Audit artifacts</h2><p>The accompanying JSON contains the full dependency history, review findings, and tournament judgments.</p>"
    )
    for a in artifacts:
        chunks.append(
            "<details><summary>"
            + escape(a["kind"] + " · " + a["id"])
            + "</summary><pre>"
            + escape(json.dumps(a, indent=2, ensure_ascii=False))
            + "</pre></details>"
        )
    chunks.append(
        "</section><section><h2>Cited evidence</h2><p>Supplied records with origin, scope and integrity hashes. Inclusion does not independently verify their truth.</p>"
    )
    for e in evidence:
        chunks.append(
            "<details><summary>"
            + escape(e["id"])
            + "</summary><pre>"
            + escape(json.dumps(e, indent=2, ensure_ascii=False))
            + "</pre></details>"
        )
    chunks.append("</section></main></body></html>")
    return "".join(chunks)


def atomic_write(path, text):
    temporary = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix="export-",
            suffix=".tmp",
            delete=False,
        ) as f:
            temporary = f.name
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def write_export(engine, project_id, package, artifacts, variants):
    # Resolve only actual citations, never all evidence currently in a project.
    # Frozen artifacts may predate evidence added after their campaign run.
    ids = referenced_evidence([package["payload"]] + [a["payload"] for a in artifacts])
    with engine.store.tx() as con:
        evidence = [
            read_evidence(engine.store, con, project_id, i) for i in sorted(ids)
        ]
    folder = engine.store.path.parent / "exports" / project_id / package["id"]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "campaign.json"
    html = folder / "review.html"
    atomic_write(
        target,
        json.dumps(
            {
                "package": package,
                "artifacts": artifacts,
                "variants": variants,
                "evidence": evidence,
            },
            indent=2,
            ensure_ascii=False,
        ),
    )
    atomic_write(html, render_report(package, artifacts, variants, evidence))
    return {
        "path": str(target),
        "html_path": str(html),
        "package_id": package["id"],
        "evidence_count": len(evidence),
        "full_published_book_complete": False,
        "published": False,
    }


def variant_from_run(engine, con, project_id, run):
    if "campaign_package" not in run["bindings"]:
        raise DomainError(
            "package_not_ready", "Every channel run needs a ready campaign package"
        )
    if (
        not {"offer_plan", "selected_candidates", "campaign_brief"}
        <= run["bindings"].keys()
    ):
        raise DomainError(
            "package_contract",
            "Package needs offer, selected assets, and channel brief",
        )
    brief = engine.store.get(
        con, run["bindings"]["campaign_brief"], project_id, "artifact"
    )["payload"]
    if len(brief["channels"]) != 1:
        raise DomainError(
            "channel_context", "Each variant must have exactly one channel"
        )
    return {
        "run_id": run["id"],
        "channel": brief["channels"][0],
        "package_id": run["bindings"]["campaign_package"],
        "selected_artifact_id": run["bindings"]["selected_candidates"],
        "offer_artifact_id": run["bindings"]["offer_plan"],
    }


def assemble_campaign(engine, project_id, ordered_run_ids, idempotency_key):
    if not 2 <= len(ordered_run_ids) <= 14 or len(set(ordered_run_ids)) != len(
        ordered_run_ids
    ):
        raise DomainError(
            "channel_bundle", "Provide 2–14 distinct ready run IDs in delivery order"
        )
    with engine.store.tx() as con:
        engine._project(con, project_id)
        h = digest(ordered_run_ids)
        old = engine.store.cached(con, "assemble:" + project_id, idempotency_key, h)
        if old:
            return old
        variants, contexts, offers, runs = [], [], [], []
        for rid in ordered_run_ids:
            run = engine._run(con, rid, project_id)
            variant = variant_from_run(engine, con, project_id, run)
            plan = engine.store.get(con, run["plan_id"], project_id, "plan")
            request = {k: v for k, v in plan["request"].items() if k != "channels"}
            contexts.append(
                digest(
                    [
                        request,
                        run["source_version"],
                        run["workflow_version"],
                        run["evidence_snapshot_hash"],
                    ]
                )
            )
            offer = engine.store.get(
                con, variant["offer_artifact_id"], project_id, "artifact"
            )["payload"]
            offers.append(digest({k: offer[k] for k in OFFER_FIELDS}))
            variants.append(variant)
            runs.append(run)
        if len(set(v["channel"] for v in variants)) != len(variants):
            raise DomainError(
                "channel_bundle", "Use one ready run per distinct channel"
            )
        if len(set(contexts)) != 1:
            raise DomainError(
                "context_mismatch",
                "Product, audience, objective, constraints, source/workflow version, and evidence must match across variants",
            )
        if len(set(offers)) != 1:
            raise DomainError(
                "offer_mismatch",
                "Material offer fields differ across channel variants; reconcile them before assembly",
            )
        payload = {
            "variants": variants,
            "sequence": "caller_supplied_run_order",
            "context_hash": contexts[0],
            "offer_terms_hash": offers[0],
            "cross_channel_ranking": False,
            "limitations": [
                "Field equality checks term continuity, not factual truth or persuasion quality.",
                "No automatic cross-channel winner is chosen.",
                "Source gaps remain flagged.",
            ],
        }
        inherited_gaps = sorted(
            {
                g
                for v in variants
                for g in engine.store.get(con, v["package_id"], project_id, "artifact")[
                    "gap_ids"
                ]
            }
        )
        package = engine._artifact(
            con,
            {**runs[0], "gap_ids": inherited_gaps},
            "multi_channel_package",
            payload,
            [v["package_id"] for v in variants],
        )
        engine.store.remember(
            con, "assemble:" + project_id, idempotency_key, h, package
        )
        return package


def export_bundle(engine, project_id, artifact_id):
    with engine.store.tx() as con:
        package = engine.store.get(con, artifact_id, project_id, "artifact")
        if package["kind"] != "multi_channel_package":
            raise DomainError(
                "package_contract", "Expected a multi_channel_package artifact"
            )
        variants = package["payload"]["variants"]
        ids = (
            package["parent_ids"]
            + [v["selected_artifact_id"] for v in variants]
            + [v["offer_artifact_id"] for v in variants]
        )
        artifacts = collect_artifacts(engine, con, project_id, ids)
    return write_export(engine, project_id, package, artifacts, variants)
