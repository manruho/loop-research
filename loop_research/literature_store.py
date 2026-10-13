"""Literature provenance and immutable source documents, independent of transport."""

import hashlib
import json
import re
import os

from .common import ResearchError, canonical_text, identifier, label, now, required
from .runtime import process_identity


LITERATURE_SCHEMA = """
CREATE TABLE papers (
    id INTEGER PRIMARY KEY, canonical_key TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL, external_id TEXT NOT NULL, title TEXT NOT NULL,
    authors_json TEXT NOT NULL, doi TEXT NOT NULL, url TEXT NOT NULL,
    pdf_url TEXT NOT NULL, source_version TEXT NOT NULL, published TEXT NOT NULL,
    abstract TEXT NOT NULL, metadata_json TEXT NOT NULL, discovered_at TEXT NOT NULL
);
CREATE TABLE searches (
    id INTEGER PRIMARY KEY, provider TEXT NOT NULL, query TEXT NOT NULL,
    requested_limit INTEGER NOT NULL, request_url TEXT NOT NULL,
    status TEXT NOT NULL, error TEXT, response_json TEXT,
    started_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE search_results (
    search_id INTEGER NOT NULL REFERENCES searches(id),
    paper_id INTEGER NOT NULL REFERENCES papers(id), rank INTEGER NOT NULL,
    PRIMARY KEY (search_id, paper_id)
);
CREATE TABLE documents (
    id INTEGER PRIMARY KEY, paper_id INTEGER NOT NULL REFERENCES papers(id),
    origin TEXT NOT NULL, source_url TEXT NOT NULL, source_version TEXT NOT NULL,
    sha256 TEXT NOT NULL, content BLOB NOT NULL, status TEXT NOT NULL,
    extractor TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL,
    UNIQUE (paper_id, origin, sha256, extractor)
);
CREATE TABLE pages (
    document_id INTEGER NOT NULL REFERENCES documents(id), page INTEGER NOT NULL,
    text TEXT NOT NULL, PRIMARY KEY (document_id, page)
);
CREATE TABLE fetches (
    id INTEGER PRIMARY KEY, paper_id INTEGER NOT NULL REFERENCES papers(id),
    requested_url TEXT NOT NULL, final_url TEXT, status TEXT NOT NULL,
    document_id INTEGER REFERENCES documents(id), error TEXT,
    started_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE evidence_documents (
    evidence_id INTEGER PRIMARY KEY REFERENCES evidence(id),
    document_id INTEGER NOT NULL, page INTEGER NOT NULL,
    FOREIGN KEY (document_id, page) REFERENCES pages(document_id, page)
);
"""


class LiteratureStore:
    def paper_row(self, paper):
        row = self.db.execute("SELECT * FROM papers WHERE id=?", (identifier(paper, "P"),)).fetchone()
        if row is None:
            raise ResearchError(f"文献 {paper} が見つかりません。")
        return dict(row)

    def _insert_paper(self, data):
        existing = self.db.execute("SELECT id FROM papers WHERE canonical_key=?", (data["canonical_key"],)).fetchone()
        if data["provider"] == "openalex" and not existing:
            existing = self.db.execute("SELECT paper_id FROM paper_aliases WHERE provider='openalex' AND external_id=?", (data["external_id"],)).fetchone()
        if existing:
            if data["provider"] == "openalex":
                self.db.execute("INSERT OR IGNORE INTO paper_aliases VALUES('openalex',?,?)", (data["external_id"], existing[0]))
            return existing[0]
        result = self.db.execute(
            "INSERT INTO papers(canonical_key,provider,external_id,title,authors_json,doi,url,pdf_url,source_version,published,abstract,metadata_json,discovered_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (data["canonical_key"], data["provider"], data["external_id"], required(data["title"], "文献名"),
             json.dumps(data.get("authors", []), ensure_ascii=False), data.get("doi", ""), data["url"],
             data.get("pdf_url", ""), data.get("source_version", ""), data.get("published", ""),
             canonical_text(data.get("abstract", "")), json.dumps(data.get("metadata", {}), ensure_ascii=False), now()),
        )
        if data["provider"] == "openalex":
            self.db.execute("INSERT OR IGNORE INTO paper_aliases VALUES('openalex',?,?)", (data["external_id"], result.lastrowid))
        return result.lastrowid

    def add_paper(self, data):
        with self.db:
            pid = self._insert_paper(data)
            self.event("paper.registered", "文献を登録。本文の読解は未実施。", {"paper_id": label(pid, "P"), "source": data["url"]})
        return self.show_paper(label(pid, "P"))

    def show_paper(self, paper):
        row = self.paper_row(paper)
        row["id"] = label(row["id"], "P")
        row["authors"] = json.loads(row.pop("authors_json"))
        row["metadata"] = json.loads(row.pop("metadata_json"))
        pid = identifier(paper, "P")
        row["discoveries"] = [dict(r) for r in self.db.execute(
            "SELECT s.id,s.provider,s.query,s.started_at,r.rank FROM searches s JOIN search_results r ON s.id=r.search_id WHERE r.paper_id=? ORDER BY s.id", (pid,)
        )]
        for discovery in row["discoveries"]:
            discovery["id"] = label(discovery["id"], "S")
        row["documents"] = self.list_documents(paper_id=pid)
        row["fetches"] = []
        for r in self.db.execute("SELECT id,status,requested_url,final_url,document_id,error,started_at,finished_at FROM fetches WHERE paper_id=? ORDER BY id", (pid,)):
            item = dict(r)
            item["id"] = label(item["id"], "F")
            if item["document_id"] is not None:
                item["document_id"] = label(item["document_id"], "D")
            row["fetches"].append(item)
        return row

    def list_papers(self):
        result = []
        for r in self.db.execute("SELECT id,title,provider,external_id,source_version,url FROM papers ORDER BY id"):
            row = dict(r)
            row["id"] = label(row["id"], "P")
            row["documents"] = self.list_documents(paper_id=identifier(row["id"], "P"))
            result.append(row)
        return result

    def start_search(self, provider, query, limit, url):
        with self.db:
            result = self.db.execute("INSERT INTO searches(provider,query,requested_limit,request_url,status,started_at,owner_pid,owner_identity) VALUES(?,?,?,?,'running',?,?,?)",
                                     (provider, query, limit, url, now(), os.getpid(), process_identity(os.getpid())))
            sid = result.lastrowid
            self.event("search.started", "文献探索を開始", {"search_id": label(sid, "S"), "provider": provider, "query": query, "requested_limit": limit, "url": url})
        return sid

    def finish_search(self, sid, papers=None, response=None, error=None, interrupted=False):
        with self.db:
            status = "interrupted" if interrupted else "failed" if error else "completed"
            for rank, data in enumerate(papers or [], 1):
                pid = self._insert_paper(data)
                self.db.execute("INSERT OR IGNORE INTO search_results VALUES(?,?,?)", (sid, pid, rank))
            self.db.execute("UPDATE searches SET status=?,error=?,response_json=?,finished_at=? WHERE id=? AND status='running'", (status, error, json.dumps(response, ensure_ascii=False) if response is not None else None, now(), sid))
            self.event("search.finished", error or "検索結果を保存。検索範囲は限定的で、新規性の証明ではない。", {"search_id": label(sid, "S"), "status": status})
        return self.show_search(label(sid, "S"))

    def show_search(self, search):
        row = self.db.execute("SELECT * FROM searches WHERE id=?", (identifier(search, "S"),)).fetchone()
        if row is None:
            raise ResearchError(f"検索 {search} が見つかりません。")
        item = dict(row)
        item["id"] = label(item["id"], "S")
        raw = item.pop("response_json")
        item["response"] = json.loads(raw) if raw is not None else None
        item["results"] = [{"paper_id": label(r[0], "P"), "rank": r[1]} for r in self.db.execute("SELECT paper_id,rank FROM search_results WHERE search_id=? ORDER BY rank", (identifier(search, "S"),))]
        return item

    def list_searches(self):
        result = []
        for r in self.db.execute("SELECT id,provider,query,requested_limit,status,error,started_at,finished_at FROM searches ORDER BY id"):
            row = dict(r)
            row["id"] = label(row["id"], "S")
            result.append(row)
        return result

    def start_fetch(self, paper, url):
        pid = self.paper_row(paper)["id"]
        with self.db:
            result = self.db.execute("INSERT INTO fetches(paper_id,requested_url,status,started_at,owner_pid,owner_identity) VALUES(?,?,'running',?,?,?)",
                                     (pid, url, now(), os.getpid(), process_identity(os.getpid())))
            fid = result.lastrowid
            self.event("fetch.started", "文献資料の取得を開始", {"fetch_id": label(fid, "F"), "paper_id": paper, "url": url})
        return fid

    def save_document(self, fid, paper, content, origin, source_url, pages, extractor, error=None):
        metadata = self.paper_row(paper)
        digest = hashlib.sha256(content).hexdigest()
        with self.db:
            row = self.db.execute("SELECT id FROM documents WHERE paper_id=? AND origin=? AND sha256=? AND extractor=?", (metadata["id"], origin, digest, extractor)).fetchone()
            if row:
                did = row[0]
                previous = self.document_row(label(did, "D"))
                if previous["status"] == "extraction_failed" and error is None:
                    self.db.execute("UPDATE documents SET status='extracted',extractor=?,error=NULL WHERE id=?", (extractor, did))
                    for page, text in enumerate(pages, 1):
                        self.db.execute("INSERT INTO pages VALUES(?,?,?)", (did, page, canonical_text(text)))
            else:
                result = self.db.execute(
                    "INSERT INTO documents(paper_id,origin,source_url,source_version,sha256,content,status,extractor,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (metadata["id"], origin, source_url, metadata["source_version"], digest, content,
                     "extraction_failed" if error else "extracted", extractor, error, now()),
                )
                did = result.lastrowid
                for page, text in enumerate(pages, 1):
                    self.db.execute("INSERT INTO pages VALUES(?,?,?)", (did, page, canonical_text(text)))
            status = "failed" if error else "completed"
            self.db.execute("UPDATE fetches SET final_url=?,status=?,document_id=?,error=?,finished_at=? WHERE id=?", (source_url, status, did, error, now(), fid))
            self.event("document.saved", error or "原資料と抽出本文を保存。精読済み・支持関係確認済みとは扱わない。", {"document_id": label(did, "D"), "paper_id": paper, "sha256": digest, "origin": origin, "extractor": extractor})
        return self.show_document(label(did, "D"))

    def fail_fetch(self, fid, error, interrupted=False):
        with self.db:
            status = "interrupted" if interrupted else "failed"
            self.db.execute("UPDATE fetches SET status=?,error=?,finished_at=? WHERE id=?", (status, error, now(), fid))
            self.event("fetch.failed", error, {"fetch_id": label(fid, "F"), "status": status})

    def document_row(self, document, include_content=False):
        columns = "*" if include_content else "id,paper_id,origin,source_url,source_version,sha256,status,extractor,error,created_at"
        row = self.db.execute(f"SELECT {columns} FROM documents WHERE id=?", (identifier(document, "D"),)).fetchone()
        if row is None:
            raise ResearchError(f"資料 {document} が見つかりません。")
        return dict(row)

    def document_page(self, document, page):
        if not isinstance(page, int) or page <= 0:
            raise ResearchError("ページは正の整数で指定してください。")
        doc = self.document_row(document)
        if doc["status"] != "extracted":
            raise ResearchError("この資料は本文抽出に失敗しています。精読済みとして扱えません。")
        row = self.db.execute("SELECT text FROM pages WHERE document_id=? AND page=?", (doc["id"], page)).fetchone()
        if row is None:
            raise ResearchError(f"資料 {document} にページ {page} はありません。")
        return row[0]

    def show_document(self, document, page=None):
        row = self.document_row(document)
        row["id"] = label(row["id"], "D")
        row["paper_id"] = label(row["paper_id"], "P")
        row["pages"] = [{"page": r[0], "characters": len(r[1])} for r in self.db.execute("SELECT page,text FROM pages WHERE document_id=? ORDER BY page", (identifier(document, "D"),))]
        if page is not None:
            row["page"] = page
            row["text"] = self.document_page(document, page)
        return row

    def list_documents(self, paper_id=None):
        rows = self.db.execute("SELECT id FROM documents" + (" WHERE paper_id=?" if paper_id is not None else "") + " ORDER BY id", (paper_id,) if paper_id is not None else ())
        return [self.show_document(label(r[0], "D")) for r in rows]

    def find_passages(self, query, document=None, limit=5):
        query = canonical_text(required(query, "本文検索語"))
        if not 1 <= limit <= 20:
            raise ResearchError("検索件数は1〜20件で指定してください。")
        if document:
            self.document_row(document)
        rows = self.db.execute(
            "SELECT p.document_id,p.page,p.text,d.origin FROM pages p JOIN documents d ON d.id=p.document_id WHERE d.status='extracted'"
            + (" AND p.document_id=?" if document else "") + " ORDER BY p.document_id,p.page",
            (identifier(document, "D"),) if document else (),
        )
        result = []
        for row in rows:
            # Search positions in original text to avoid Unicode casefold offsets.
            match = re.search(re.escape(query), row[2], re.IGNORECASE)
            if match:
                start, end = max(0, match.start() - 160), min(len(row[2]), match.end() + 240)
                result.append({"document_id": label(row[0], "D"), "page": row[1], "origin": row[3], "excerpt": row[2][start:end]})
                if len(result) == limit:
                    break
        return {"query": query, "method": "literal phrase, case-insensitive", "results": result}

    def evidence_provenance(self, evidence):
        row = self.db.execute("SELECT document_id,page FROM evidence_documents WHERE evidence_id=?", (identifier(evidence, "E"),)).fetchone()
        if row is None:
            return None
        doc = self.document_row(label(row[0], "D"))
        return {"document_id": label(row[0], "D"), "paper_id": label(doc["paper_id"], "P"), "page": row[1], "origin": doc["origin"], "sha256": doc["sha256"], "source_url": doc["source_url"], "source_version": doc["source_version"], "extractor": doc["extractor"], "match": "exact substring of whitespace-normalized extracted text"}

    def add_document_evidence(self, claim, document, page, quote, interpretation, relation):
        doc = self.document_row(document)
        paper = self.paper_row(label(doc["paper_id"], "P"))
        level = "abstract" if doc["origin"] == "abstract" else "passage"
        locator = f"{document}, " + (f"PDF physical page {page}" if doc["origin"] == "pdf" else f"{doc['origin']} unit {page}")
        return self.add_evidence(claim, paper["title"] + " — " + paper["url"], doc["source_version"], locator,
                                 canonical_text(quote), interpretation, relation, level, "unchecked", document=document, page=page)
