"""Create an explicitly original demo corpus, never a substitute for the book.

Refuses to overwrite a corpus. The private index can be rebuilt on repeat runs.
"""
from pathlib import Path
import hashlib
import json
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2) + '\n')


def main():
    marker = ROOT / 'semantic/demo-marker.json'
    if (ROOT / 'semantic/atoms.json').exists() and not marker.exists():
        raise SystemExit('Existing non-demo corpus found. Refusing to overwrite it.')
    if not marker.exists():
        stages = json.loads((ROOT / 'workflows/stages.json').read_text())
        ids = sorted({t for s in stages for t in s.get('technique_ids', [])})
        # Routing tags are interface identifiers, not imported book teachings.
        from schwartz_mcp.retrieval import LEVEL_TECHNIQUES
        ids = sorted(set(ids) | {t for ts in LEVEL_TECHNIQUES.values() for t in ts})
        atom = {
            'id': 'demo-evidence-boundary', 'kind': 'teaching', 'source_pages': [],
            'source_statement': 'A model preference is a proposal to test. A real customer response is an observation. Keep them separate.',
            'operationalization': 'Label synthetic examples and hypotheses; cite actual evidence for factual claims.',
            'qualification': 'Original demonstration guidance written for this software. Not an excerpt, paraphrase, or certification of Breakthrough Advertising.',
            'technique_ids': ids, 'review_id': 'demo', 'reviewer': 'demo-author',
            'workflow_ids': [], 'passage_ids': [], 'instruction_authority': False,
            'provenance_class': 'original_demo_content'
        }
        review = {'atoms': [atom], 'passages': [], 'relations': []}
        dump('semantic/reviews/demo.json', review)
        dump('semantic/atoms.json', [atom])
        dump('semantic/passages.json', [])
        dump('semantic/relations.json', [])
        dump('inventory/techniques.json', [{'id': t, 'title': t.replace('-', ' '), 'status': 'routing_tag_only_no_book_teaching'} for t in ids])
        dump('inventory/sources.json', {'known_gaps': [{'id': 'public-demo-no-book-corpus', 'description': 'Public release contains only original demo guidance. No book corpus is included.', 'source_pages': []}]})
        dump('inventory/build-readiness.json', {'demo_only': True, 'full_source_certification': False})
        dump('semantic/uncertainties.json', [])
        dump('semantic/source-lock.json', {'files': {}})
        review_hash = hashlib.sha256((ROOT / 'semantic/reviews/demo.json').read_bytes()).hexdigest()
        dump('semantic/coverage.json', {'review_hashes': {'demo': review_hash}, 'full_published_book_complete': False, 'demo_only': True})
        dump('semantic/demo-marker.json', {'original_demo_only': True})
    destination = ROOT / 'private/inventory.sqlite3'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(destination) as con:
        con.executescript('DROP TABLE IF EXISTS records; DROP TABLE IF EXISTS search; CREATE TABLE records(id TEXT PRIMARY KEY,kind TEXT,pdf_page INTEGER,payload TEXT NOT NULL); CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED,kind UNINDEXED,title,body);')
        for kind, path in [('atom', 'semantic/atoms.json'), ('passage', 'semantic/passages.json'), ('technique', 'inventory/techniques.json')]:
            for record in json.loads((ROOT / path).read_text()):
                key = kind + ':' + record['id']
                payload = json.dumps(record)
                con.execute('INSERT INTO records VALUES(?,?,?,?)', (key, kind, None, payload))
                con.execute('INSERT INTO search VALUES(?,?,?,?)', (key, kind, record.get('title', record['id']), payload))
    print('Original demo corpus ready. No book content is bundled.')


if __name__ == '__main__':
    main()
