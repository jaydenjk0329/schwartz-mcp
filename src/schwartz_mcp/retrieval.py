"""Deterministic lexical retrieval over meaning-bearing fields, not routing metadata."""

from contextlib import closing
import sqlite3

VERSION = "source-fields-v1"
LEVEL_TECHNIQUES = {
    "headline_and_lead": ["headline-job", "awareness-routing", "creative-gamble"],
    "strategic_angle": [
        "dominant-desire",
        "awareness-routing",
        "sophistication-routing",
    ],
    "market_hypothesis": ["existing-desire", "dominant-desire", "awareness-routing"],
    "structure": ["belief-architecture", "headline-job", "intensification-principle"],
    "complete_asset": [
        "headline-job",
        "belief-architecture",
        "intensification-principle",
    ],
}


def teaching_priority(atom):
    return {
        "definition": 0,
        "teaching": 0,
        "rule": 0,
        "procedure": 1,
        "qualification": 1,
        "distinction": 1,
        "example_lesson": 2,
        "example": 3,
    }.get(atom["kind"], 2)


def search_atoms(atoms, techniques, match, limit):
    # Per-call connection: MCP tools may execute on different threads. The tiny
    # private corpus needs no persistent, potentially stale derived index.
    with closing(sqlite3.connect(":memory:")) as con:
        con.execute(
            "CREATE VIRTUAL TABLE source_search USING fts5(id UNINDEXED, statement, qualification, guidance, technique, tokenize='porter unicode61')"
        )
        con.executemany(
            "INSERT INTO source_search VALUES(?,?,?,?,?)",
            [
                (
                    a["id"],
                    a["source_statement"],
                    a.get("qualification", ""),
                    a.get("operationalization", ""),
                    " ".join(
                        techniques[t]["title"]
                        for t in a["technique_ids"]
                        if t in techniques
                    ),
                )
                for a in atoms.values()
            ],
        )
        rows = con.execute(
            "SELECT id, bm25(source_search,0,6,2,1,3) FROM source_search WHERE source_search MATCH ?",
            (match,),
        ).fetchall()
    # Broad queries favor general teachings over incidental historical examples.
    # Explicit example terms still work; no unmatched atom is introduced.
    rows.sort(key=lambda r: (r[1] / (1 + teaching_priority(atoms[r[0]])), r[0]))
    return [r[0] for r in rows[:limit]]
