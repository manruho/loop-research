"""Immutable citation observations and fact-level verification snapshots."""
import json
from .common import ResearchError, identifier, label, now

QUALITY_SCHEMA = """
CREATE TABLE paper_aliases (
    provider TEXT NOT NULL, external_id TEXT NOT NULL, paper_id INTEGER NOT NULL REFERENCES papers(id),
    PRIMARY KEY(provider,external_id)
);
CREATE TABLE citation_edges (
    search_id INTEGER NOT NULL REFERENCES searches(id),
    citing_id INTEGER NOT NULL REFERENCES papers(id), cited_id INTEGER NOT NULL REFERENCES papers(id),
    PRIMARY KEY(search_id,citing_id,cited_id)
);
CREATE TABLE facts (
    verification_id INTEGER NOT NULL REFERENCES artifacts(id), fact_id TEXT NOT NULL,
    draft_id INTEGER NOT NULL REFERENCES artifacts(id), block_id TEXT NOT NULL,
    start INTEGER NOT NULL, end INTEGER NOT NULL, text TEXT NOT NULL,
    verdict TEXT NOT NULL, reason TEXT NOT NULL, qualification TEXT NOT NULL,
    PRIMARY KEY(verification_id,fact_id)
);
CREATE TABLE fact_evidence (
    verification_id INTEGER NOT NULL, fact_id TEXT NOT NULL,
    evidence_id INTEGER NOT NULL REFERENCES evidence(id), document_id INTEGER NOT NULL,
    page INTEGER NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL, quote TEXT NOT NULL,
    PRIMARY KEY(verification_id,fact_id,evidence_id,start,end),
    FOREIGN KEY(verification_id,fact_id) REFERENCES facts(verification_id,fact_id),
    FOREIGN KEY(document_id,page) REFERENCES pages(document_id,page)
);
"""


class QualityStore:
    def citation_graph(self):
        return [{"search_id": label(r[0], "S"), "citing_id": label(r[1], "P"),
                 "cited_id": label(r[2], "P"), "scope": "provider-reported citation, not substantive support"}
                for r in self.db.execute("SELECT * FROM citation_edges ORDER BY search_id,citing_id,cited_id")]

    def verification_rows(self, verification):
        vid = identifier(verification, "U")
        rows = []
        for row in self.db.execute("SELECT * FROM facts WHERE verification_id=? ORDER BY fact_id", (vid,)):
            item = dict(row)
            location = item["block_id"].split(":", 1)
            item["block_id"], item["field"] = location[0], location[1] if len(location) > 1 else "text"
            item["sources"] = [dict(r) for r in self.db.execute(
                "SELECT * FROM fact_evidence WHERE verification_id=? AND fact_id=? ORDER BY evidence_id,start", (vid, item["fact_id"]))]
            rows.append(item)
        return rows

    def apply_verify(self, tid, key, data, result):
        # Semantics and exact offsets are validated before this transaction applies.
        from .workflow_store import encoded
        uid = self._artifact("verify", key, tid, data, result, data["claim_id"])
        vid, did = identifier(uid, "U"), identifier(data["draft"]["id"], "A")
        for fact in result["facts"]:
            self.db.execute("INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (vid, fact["id"], did, fact["block_id"] + ":" + fact["field"], fact["start"], fact["end"], fact["text"],
                             fact["verdict"], fact["reason"], fact["qualification"]))
            for source in fact["sources"]:
                self.db.execute("INSERT INTO fact_evidence VALUES(?,?,?,?,?,?,?,?)",
                                (vid, fact["id"], identifier(source["evidence_id"], "E"),
                                 identifier(source["document_id"], "D"), source["page"], source["start"],
                                 source["end"], source["quote"]))
        self.event("draft.verified", "AIによる事実主張ごとの暫定検証。人間の確認ではない。",
                   {"verification_id": uid, "draft_id": data["draft"]["id"], "facts": len(result["facts"]),
                    "coverage": result["coverage"]})
        self._set_study("事実主張を暫定検証。抽出漏れと支持判定は査読・人間確認が必要。", phase="review")
