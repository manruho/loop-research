import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loop_research.common import ResearchError
from loop_research.literature import arxiv_expression, parse_arxiv, parse_crossref
from loop_research.store import ResearchStore, SCHEMA, RUN_SCHEMA
from loop_research.literature_store import LITERATURE_SCHEMA
from loop_research.workflow_store import WORKFLOW_SCHEMA, OWNER_SCHEMA, DEFAULT_SETTINGS
from loop_research.workflow import ResearchWorkflow
from loop_research import roles
from tests.fixtures import FixtureEngine


class IntegrityAcceptance(unittest.TestCase):
    def test_all_supported_old_schemas_keep_claims_and_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            for version in (1, 2, 3, 4, 5):
                with self.subTest(version=version):
                    path = Path(directory) / f"v{version}.sqlite3"
                    schema = SCHEMA.replace("PRAGMA user_version = 6;", f"PRAGMA user_version = {version};")
                    if version < 5:
                        schema = schema.replace(OWNER_SCHEMA, "")
                    if version < 4:
                        schema = schema.replace(WORKFLOW_SCHEMA, "")
                    if version < 3:
                        schema = schema.replace(LITERATURE_SCHEMA, "")
                    if version < 2:
                        schema = schema.replace(RUN_SCHEMA, "")
                    schema = schema.replace("UNIQUE (paper_id, origin, sha256, extractor)", "UNIQUE (paper_id, origin, sha256)")
                    db = sqlite3.connect(path)
                    db.executescript(schema)
                    db.execute("INSERT INTO project VALUES('Old question','old-time')")
                    db.execute("INSERT INTO claims VALUES(1,1,'Old claim','candidate')")
                    db.execute("INSERT INTO revisions VALUES(1,1,'Old claim','old-reason','old-time')")
                    db.execute("INSERT INTO evidence VALUES(1,1,1,'Old source','','old location','old quote','old interpretation','supports','abstract','checked','old-time')")
                    if version >= 3:
                        db.execute("INSERT INTO papers VALUES(1,'fixture:old','fixture','old','Old source','[]','','https://fixture.invalid','','','','','{}','old-time')")
                        db.execute("INSERT INTO documents VALUES(1,1,'text','file:///old','','old-hash',?,'extracted','old-extractor',NULL,'old-time')",
                                   (b'old quote',))
                        db.execute("INSERT INTO pages VALUES(1,1,'old quote')")
                        db.execute("INSERT INTO evidence_documents VALUES(1,1,1)")
                    if version >= 4:
                        db.execute("INSERT INTO study_state VALUES(1,'Old question','Old audience','Old venue','held','comparison','Old hold',1,1,NULL,?,'old-time')",
                                   (json.dumps(DEFAULT_SETTINGS),))
                    db.commit()
                    db.close()
                    store = ResearchStore(path)
                    self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 6)
                    self.assertEqual(store.claim_row("C001")["text"], "Old claim")
                    self.assertEqual(store.evidence_row("E001")["quote"], "old quote")
                    self.assertEqual(store.evidence_row("E001")["status"], "checked")
                    if version >= 3:
                        self.assertEqual(store.document_page("D001", 1), "old quote")
                        self.assertEqual(store.evidence_provenance("E001")["document_id"], "D001")
                        self.assertEqual(list(store.db.execute("PRAGMA foreign_key_check")), [])
                    if version >= 4:
                        self.assertEqual(store.study_state()["status"], "held")
                    store.close()

    def test_arxiv_queries_preserve_phrases_and_explicit_boolean_intent(self):
        self.assertEqual(arxiv_expression('"AI Scientist"'), 'all:"AI Scientist"')
        self.assertEqual(arxiv_expression('"self-correction" AND "large language models"'),
                         'all:"self-correction" AND all:"large language models"')
        self.assertEqual(arxiv_expression('ti:"AI Scientist" OR abs:uncertainty'),
                         'ti:"AI Scientist" OR abs:"uncertainty"')
        with self.assertRaises(ResearchError):
            arxiv_expression('"Unclosed query')

    def test_error_metadata_cannot_turn_into_papers(self):
        with self.assertRaises(ResearchError):
            parse_arxiv(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors</id><title>Error</title></entry></feed>')
        with self.assertRaises(ResearchError):
            parse_crossref(b'{"message":{"items":[{"title":["No DOI"]}]}}')

    def test_reader_cannot_invent_a_quotation(self):
        result = {"findings": [{"excerpt": "Invented text."}]}
        with self.assertRaises(ResearchError):
            roles.validate_semantics("reading", {"page_text": "Actual source."}, result)

    def test_empirical_claim_needs_evidence_and_limited_evidence_needs_qualification(self):
        data = {"claim_id": "C001", "context": {"evidence": {"E001": {"level": "abstract", "status": "unchecked", "document_source": {}}},
                                               "claims": {"C001": {}}, "papers": {}}}
        block = {"text": "The study observed an effect.", "kind": "fact", "evidence_ids": [],
                 "claim_ids": ["C001"], "qualification": ""}
        draft = {"abstract_blocks": [block],
                 "sections": [{"role": name, "blocks": [dict(block)]} for name in
                              ("introduction", "related_work", "position", "counterarguments",
                               "implications", "limitations", "conclusion")]}
        with self.assertRaises(ResearchError):
            roles.validate_semantics("draft", data, draft)
        for item in roles.blocks(draft):
            item["evidence_ids"] = ["E001"]
        with self.assertRaises(ResearchError):
            roles.validate_semantics("draft", data, draft)
        for item in roles.blocks(draft):
            item["qualification"] = "Only a supplied abstract was inspected."
        roles.validate_semantics("draft", data, draft)

    def test_exploration_cannot_claim_unread_passages_as_verified_overlap(self):
        candidate = {"supporting_evidence_ids": [], "opposing_evidence_ids": [],
                     "text": "A limited proposal",
                     "overlaps": [{"paper_id": "P001", "verification_scope": "passages"}]}
        with self.assertRaises(ResearchError):
            roles.validate_semantics("exploration",
                                     {"context": {"papers": {"P001": {"seen_scope": "metadata"}}, "evidence": {}}},
                                     {"candidates": [candidate, dict(candidate, text="Another proposal")]})

    def test_malformed_human_draft_does_not_modify_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResearchStore(Path(directory) / "study.sqlite3", initialize=True)
            store.init("Synthetic question")
            path = Path(directory) / "bad.json"
            path.write_text('{"abstract_blocks":[],"sections":[1]}')
            with self.assertRaises(ResearchError):
                ResearchWorkflow(store, FixtureEngine()).import_draft(path, "Malformed import")
            self.assertEqual(store.list_artifacts("draft"), [])
            store.close()


if __name__ == "__main__":
    unittest.main()
