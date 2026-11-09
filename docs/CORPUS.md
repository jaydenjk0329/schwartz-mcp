# Reviewed corpus contract

The public demo is original example content, not the private book corpus. Never commit books, extracted passages, customer evidence, runtime databases, or credentials just to make a public checkout work.

The runtime root contains:

- `semantic/atoms.json`: records keyed by `id`, with `kind`, `source_statement`, `qualification`, `operationalization`, `technique_ids`, and `source_pages`.
- `semantic/passages.json`: passage records with source provenance.
- `semantic/relations.json`: relationships with `from_id`, `to_id`, `relation`, and `rationale`.
- `semantic/reviews/<name>.json`: authored `atoms`, `passages`, and `relations`.
- `semantic/coverage.json`: `review_hashes` mapping review names to SHA-256 file hashes, and honest coverage flags.
- `semantic/source-lock.json`: `files` mapping source paths relative to the root to SHA-256 hashes.
- `semantic/uncertainties.json`: explicit unresolved items with stable IDs.
- `inventory/techniques.json`: technique metadata with stable IDs and titles.
- `inventory/sources.json`: `known_gaps` records with stable IDs.
- `inventory/build-readiness.json`: scope and readiness limitations.
- `private/inventory.sqlite3`: derived index for passage, technique, source, and fallback lookup.
- `workflows/catalog.json`, `stages.json`, and `channel-adapters.json`: executable workflow registry.

See `scripts/init_demo.py` for a minimal original example and the index schema. Its initializer refuses to replace a non-demo corpus. Review and rehash changed source material intentionally. `Knowledge.verify()` detects changed locked files and disagreement with authored review records. Passing structural checks does not certify semantic completeness or ownership of the source.
