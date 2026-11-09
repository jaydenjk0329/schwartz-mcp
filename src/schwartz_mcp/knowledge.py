from __future__ import annotations
from collections import defaultdict
from contextlib import closing
import hashlib
import sqlite3
from .common import DomainError, digest, read_json
from .retrieval import VERSION, LEVEL_TECHNIQUES, search_atoms, teaching_priority


class Knowledge:
    retrieval_version = VERSION

    def __init__(self, root):
        self.root = root
        self.atoms = {r["id"]: r for r in read_json(root / "semantic/atoms.json")}
        self.passages = {r["id"]: r for r in read_json(root / "semantic/passages.json")}
        self.techniques = {
            r["id"]: r for r in read_json(root / "inventory/techniques.json")
        }
        self.relations = read_json(root / "semantic/relations.json")
        self.coverage = read_json(root / "semantic/coverage.json")
        self.source_version = digest(
            {
                "source_lock": read_json(root / "semantic/source-lock.json"),
                "review_hashes": self.coverage["review_hashes"],
                "atoms": self.atoms,
                "passages": self.passages,
                "relations": self.relations,
            }
        )
        self.gaps = read_json(root / "inventory/sources.json")[
            "known_gaps"
        ] + read_json(root / "semantic/uncertainties.json")
        self.gaps = list({r["id"]: r for r in self.gaps}.values())
        self.source_version = digest(
            {
                "reviewed_source": self.source_version,
                "gaps": self.gaps,
                "techniques": self.techniques,
            }
        )
        self.links = defaultdict(list)
        for rel in self.relations:
            self.links[rel["from_id"]].append(rel)
            self.links[rel["to_id"]].append(rel)

    def verify(self):
        for name, expected in read_json(self.root / "semantic/source-lock.json")[
            "files"
        ].items():
            if hashlib.sha256((self.root / name).read_bytes()).hexdigest() != expected:
                raise DomainError(
                    "source_changed", f"Reviewed source snapshot changed: {name}"
                )
        for name, expected in self.coverage["review_hashes"].items():
            if (
                hashlib.sha256(
                    (self.root / f"semantic/reviews/{name}.json").read_bytes()
                ).hexdigest()
                != expected
            ):
                raise DomainError(
                    "review_changed",
                    f"Rebuild and validate the reviewed knowledge: {name}",
                )

        reviewed_relations = []
        for name in self.coverage["review_hashes"]:
            review = read_json(self.root / f"semantic/reviews/{name}.json")
            for atom in review["atoms"]:
                if any(
                    self.atoms.get(atom["id"], {}).get(k) != v for k, v in atom.items()
                ):
                    raise DomainError(
                        "derived_knowledge_changed",
                        "Semantic atom differs from its authored review",
                    )
            for passage in review["passages"]:
                if any(
                    self.passages.get(passage["id"], {}).get(k) != v
                    for k, v in passage.items()
                    if k not in ("source_excerpt", "source_text", "text")
                ):
                    raise DomainError(
                        "derived_knowledge_changed",
                        "Semantic passage differs from its authored review",
                    )
            reviewed_relations.extend(review["relations"])
        if reviewed_relations != self.relations:
            raise DomainError(
                "derived_knowledge_changed",
                "Semantic relationships differ from the reviewed records",
            )

    def audit(self):
        return {
            "source_version": self.source_version,
            "coverage": self.coverage,
            "build_readiness": read_json(self.root / "inventory/build-readiness.json"),
            "gap_registry": self.gaps,
            "missing_details_remain_flagged": True,
        }

    def flags(self, pages):
        return [
            g
            for g in self.gaps
            if set(g.get("source_pages", g.get("pdf_pages", []))) & set(pages)
        ]

    def get(self, record_id, source_version=None, include_relations=True):
        if source_version and source_version != self.source_version:
            raise DomainError(
                "source_version_unavailable",
                "This process serves the current pinned knowledge version.",
            )
        ident = record_id.split(":", 1)[-1]
        record = (
            self.atoms.get(ident)
            or self.passages.get(ident)
            or self.techniques.get(ident)
        )
        if not record:
            with closing(
                sqlite3.connect(
                    (self.root / "private/inventory.sqlite3").as_uri() + "?mode=ro",
                    uri=True,
                )
            ) as con:
                row = con.execute(
                    "SELECT payload FROM records WHERE id=?", (record_id,)
                ).fetchone()
            if not row:
                raise DomainError(
                    "not_found", f"Knowledge record {record_id} not found"
                )
            import json

            record = json.loads(row[0])
        pages = record.get(
            "source_pages", [record["pdf_page"]] if "pdf_page" in record else []
        )
        if not pages and record.get("source"):
            pages = list(
                range(record["source"]["pdf_start"], record["source"]["pdf_end"] + 1)
            )
        result = {
            "record": record,
            "source_version": self.source_version,
            "relevant_gap_flags": self.flags(pages),
            "global_limitations": {
                "full_published_book_complete": False,
                "word_level_transcription_certified": False,
                "gap_registry_resource": "schwartz://sources/current",
            },
            "instruction_authority": False,
        }
        if include_relations:
            result["relations"] = self.links.get(ident, [])[:60]
            related = {
                r["to_id"] if r["from_id"] == ident else r["from_id"]
                for r in result["relations"]
                if r["relation"]
                in ("qualifies", "requires", "tension_with", "contrasts_with")
            }
            result["qualifications_and_tensions"] = [
                self.atoms[x] for x in sorted(related) if x in self.atoms
            ][:24]
        return result

    def search(self, query, kind="atom", limit=8, source_version=None):
        if source_version and source_version != self.source_version:
            raise DomainError(
                "source_version_unavailable", "Unknown knowledge snapshot"
            )
        if kind not in ("atom", "passage", "technique", "example", "source"):
            raise DomainError("invalid_kind", kind)
        tokens = query.split()
        if not tokens:
            raise DomainError("empty_query", "Enter at least one search term")
        match = " AND ".join('"' + t.replace('"', '""') + '"' for t in tokens[:30])
        if kind == "atom":
            ids = search_atoms(
                self.atoms, self.techniques, match, max(1, min(limit, 30))
            )
            return {
                "results": [self.get(i) for i in ids],
                "source_version": self.source_version,
                "query": query,
                "retrieval_version": self.retrieval_version,
                "selection": "Stemmed lexical match on source statements, qualifications, operationalization and linked technique titles; general teachings favored. Not a relevance probability.",
            }
        uri = (self.root / "private/inventory.sqlite3").as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as con:
            rows = con.execute(
                "SELECT s.id FROM search s WHERE search MATCH ? AND s.kind=? ORDER BY bm25(search) LIMIT ?",
                (match, kind, max(1, min(limit, 30))),
            ).fetchall()
        return {
            "results": [self.get(r[0]) for r in rows],
            "source_version": self.source_version,
            "query": query,
        }

    def bundle(self, technique_ids, limit=14, seed_atom_ids=(), candidate_level=None):
        technique_ids = list(
            dict.fromkeys(
                LEVEL_TECHNIQUES.get(candidate_level, []) + list(technique_ids)
            )
        )
        # Cited atoms are context, not authority. Bound them so a candidate cannot
        # displace the general teaching used to scrutinize its interpretation.
        selected = list(dict.fromkeys(i for i in seed_atom_ids if i in self.atoms))[
            : min(4, limit)
        ]
        # Round-robin across technique families instead of letting one broad technique consume all context.
        pools = [
            sorted(
                [a for a in self.atoms.values() if t in a["technique_ids"]],
                key=teaching_priority,
            )
            for t in technique_ids
        ]
        for i in range(max((len(p) for p in pools), default=0)):
            for pool in pools:
                if len(selected) >= limit:
                    break
                if i < len(pool) and pool[i]["id"] not in selected:
                    selected.append(pool[i]["id"])
                if len(selected) >= limit:
                    break
            if len(selected) >= limit:
                break
        records = [self.get(i) for i in selected]
        return {
            "source_version": self.source_version,
            "records": records,
            "technique_ids": technique_ids,
            "global_gap_ids": [g["id"] for g in self.gaps],
            "relevant_gap_ids": sorted(
                {g["id"] for r in records for g in r["relevant_gap_flags"]}
            ),
            "selection": "Up to four cited atoms, then general teachings round-robin across candidate-level and stage techniques, plus qualification/tension expansion; retrieve more passages when needed.",
            "retrieval_version": self.retrieval_version,
            "full_published_book_complete": False,
            "instruction_authority": False,
        }
