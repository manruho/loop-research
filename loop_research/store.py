"""Persistent research operations. All mutations and audit events are atomic."""

import json
import sqlite3
from pathlib import Path
from .common import ResearchError, identifier, label, now, required
from .quality_store import QUALITY_SCHEMA, QualityStore
from .literature_store import LITERATURE_SCHEMA, LiteratureStore
from .workflow_store import WORKFLOW_SCHEMA, OWNER_SCHEMA, WorkflowStore, DEFAULT_SETTINGS, encoded


RUN_SCHEMA = """
CREATE TABLE runs (
    id INTEGER PRIMARY KEY, task TEXT NOT NULL, status TEXT NOT NULL,
    claim_id INTEGER NOT NULL, claim_revision INTEGER NOT NULL,
    evidence_id INTEGER NOT NULL REFERENCES evidence(id),
    input_json TEXT NOT NULL, config_json TEXT NOT NULL,
    result_json TEXT, error TEXT, started_at TEXT NOT NULL, finished_at TEXT,
    FOREIGN KEY (claim_id, claim_revision) REFERENCES revisions(claim_id, revision)
);
"""


SCHEMA = """
CREATE TABLE project (title TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE claims (
    id INTEGER PRIMARY KEY, revision INTEGER NOT NULL,
    text TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE revisions (
    claim_id INTEGER NOT NULL REFERENCES claims(id), revision INTEGER NOT NULL,
    text TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (claim_id, revision)
);
CREATE TABLE evidence (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL, claim_revision INTEGER NOT NULL,
    source TEXT NOT NULL, source_version TEXT NOT NULL,
    locator TEXT NOT NULL, quote TEXT NOT NULL, interpretation TEXT NOT NULL,
    relation TEXT NOT NULL, level TEXT NOT NULL, status TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    FOREIGN KEY (claim_id, claim_revision) REFERENCES revisions(claim_id, revision)
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
    action TEXT NOT NULL, claim_id INTEGER, evidence_id INTEGER,
    reason TEXT NOT NULL, payload TEXT NOT NULL
);
""" + RUN_SCHEMA + LITERATURE_SCHEMA + WORKFLOW_SCHEMA + OWNER_SCHEMA + QUALITY_SCHEMA + "PRAGMA user_version = 7;"


class ResearchStore(LiteratureStore, WorkflowStore, QualityStore):
    def __init__(self, path, initialize=False):
        self.path = Path(path).expanduser()
        if not initialize and not self.path.is_file():
            raise ResearchError("研究が未初期化です。まず research init を実行してください。")
        if initialize:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3, 4, 5, 6, 7) or (not initialize and version == 0):
            self.db.close()
            raise ResearchError("対応していない研究DBです。上書きせず停止しました。")
        if version in (1, 2, 3, 4, 5) and not initialize:
            try:
                # Rebuilding the document uniqueness constraint must not rewrite child FKs.
                self.db.execute("PRAGMA foreign_keys = OFF")
                self.db.executescript("BEGIN IMMEDIATE;\n" + (RUN_SCHEMA if version == 1 else "")
                                     + (LITERATURE_SCHEMA if version < 3 else "")
                                     + (WORKFLOW_SCHEMA if version < 4 else "")
                                     + (OWNER_SCHEMA if version < 5 else ""))
                if version >= 3:
                    definition = self.db.execute("SELECT sql FROM sqlite_master WHERE name='documents'").fetchone()[0]
                    if "sha256, extractor" not in definition:
                        self.db.execute("""CREATE TABLE documents_next (
                            id INTEGER PRIMARY KEY, paper_id INTEGER NOT NULL REFERENCES papers(id),
                            origin TEXT NOT NULL, source_url TEXT NOT NULL, source_version TEXT NOT NULL,
                            sha256 TEXT NOT NULL, content BLOB NOT NULL, status TEXT NOT NULL,
                            extractor TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL,
                            UNIQUE(paper_id, origin, sha256, extractor))""")
                        self.db.execute("INSERT INTO documents_next SELECT * FROM documents")
                        self.db.execute("DROP TABLE documents")
                        self.db.execute("ALTER TABLE documents_next RENAME TO documents")
                if self.db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ResearchError("既存の参照関係に問題があります。移行を取り消しました。")
                self.event("database.migrated", "研究制御・作業の所有情報・抽出方法ごとの資料版を追加", {"from": version, "to": 6})
                self.db.execute("PRAGMA user_version = 6")
                self.db.commit()
                self.db.execute("PRAGMA foreign_keys = ON")
            except Exception:
                self.db.rollback()
                self.db.close()
                raise
        if not initialize and self.db.execute("PRAGMA user_version").fetchone()[0] == 6:
            try:
                self.db.executescript("BEGIN IMMEDIATE;\n" + QUALITY_SCHEMA)
                self.event("database.migrated", "引用関係と事実主張の検証を追加。旧版は履歴として保持。", {"from": 6, "to": 7})
                legacy = self.db.execute("SELECT status FROM study_state WHERE id=1").fetchone()
                if legacy and legacy[0] in ("ready_for_human", "finalized"):
                    self._set_study("旧版の判断は履歴に保持。事実検証と別モデル査読を再実行してください。", status="needs_human", phase="verification")
                    self.event("study.completion_invalidated", "新しい検証工程が未実施", {"previous_status": legacy[0]})
                self.db.execute("PRAGMA user_version = 7")
                if self.db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ResearchError("移行後の参照関係に問題があります。")
                self.db.commit()
            except Exception:
                self.db.rollback()
                self.db.close()
                raise
        if not initialize:
            self.study_state()

    def close(self):
        self.db.close()

    def event(self, action, reason, payload, claim_id=None, evidence_id=None):
        self.db.execute(
            "INSERT INTO events(created_at,action,claim_id,evidence_id,reason,payload) VALUES(?,?,?,?,?,?)",
            (now(), action, claim_id, evidence_id, reason, json.dumps(payload, ensure_ascii=False)),
        )

    def init(self, title):
        title = required(title, "研究テーマ")
        if self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
            raise ResearchError("既存のDBは初期化できません。別の --db を指定してください。")
        # An explicit transaction also rolls back partially created schema on failure.
        try:
            self.db.executescript("BEGIN;\n" + SCHEMA)
            self.db.execute("INSERT INTO project VALUES(?,?)", (title, now()))
            self.db.execute(
                "INSERT INTO study_state VALUES(1,?,?,?,'active','orientation',?,NULL,NULL,NULL,?,?)",
                (title, "Academic readers in ML, HCI, and information science", "unspecified",
                 "研究を開始", encoded(DEFAULT_SETTINGS), now()),
            )
            self.event("project.initialized", "研究を開始", {"title": title})
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return {"title": title, "database": str(self.path)}

    def claim_row(self, claim):
        row = self.db.execute("SELECT * FROM claims WHERE id=?", (identifier(claim, "C"),)).fetchone()
        if row is None:
            raise ResearchError(f"主張 {claim} が見つかりません。")
        return dict(row)

    def evidence_row(self, evidence):
        row = self.db.execute("SELECT * FROM evidence WHERE id=?", (identifier(evidence, "E"),)).fetchone()
        if row is None:
            raise ResearchError(f"根拠 {evidence} が見つかりません。")
        return dict(row)

    def add_claim(self, text):
        text = required(text, "主張")
        with self.db:
            result = self.db.execute("INSERT INTO claims(revision,text,status) VALUES(1,?,'candidate')", (text,))
            cid = result.lastrowid
            self.db.execute("INSERT INTO revisions VALUES(?,1,?,?,?)", (cid, text, "初期案", now()))
            self.db.execute("INSERT INTO profiles VALUES(?,1,?,NULL)",
                            (cid, encoded({"kind": "unclassified", "scope": text, "_question": self.study_state()["question"]})))
            self.event("claim.added", "初期案", {"text": text, "revision": 1}, claim_id=cid)
        return self.show_claim(label(cid, "C"))

    def show_claim(self, claim):
        row = self.claim_row(claim)
        row["id"] = label(row["id"], "C")
        cid = identifier(claim, "C")
        row["revisions"] = [dict(r) for r in self.db.execute(
            "SELECT revision,text,reason,created_at FROM revisions WHERE claim_id=? ORDER BY revision", (cid,)
        )]
        row["evidence"] = [self.show_evidence(label(r[0], "E")) for r in self.db.execute(
            "SELECT id FROM evidence WHERE claim_id=? ORDER BY id", (cid,)
        )]
        row["profile"] = self.profile(claim)
        row["edges"] = [edge for edge in self.claim_graph() if row["id"] in (edge["parent_id"], edge["child_id"])]
        return row

    def revise_claim(self, claim, text, reason):
        text, reason = required(text, "主張"), required(reason, "変更理由")
        with self.db:
            before = self.claim_row(claim)
            if text == before["text"]:
                raise ResearchError("主張の内容が同じです。状態変更には claim decide を使ってください。")
            previous_profile = self.profile(claim) or {}
            original_question = self.db.execute("SELECT title FROM project").fetchone()[0]
            # Preserve membership, not the previous revision's substantive assessments.
            profile = {"kind": previous_profile.get("kind", "unclassified"), "scope": text,
                       "_question": previous_profile.get("_question", original_question)}
            revision = before["revision"] + 1
            self.db.execute("UPDATE claims SET text=?,revision=?,status='needs_review' WHERE id=?", (text, revision, before["id"]))
            self.db.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (before["id"], revision, text, reason, now()))
            self.db.execute("INSERT INTO profiles VALUES(?,?,?,NULL)",
                            (before["id"], revision, encoded(profile)))
            self.event("claim.revised", reason, {"before": before, "after": self.claim_row(claim)}, claim_id=before["id"])
            state = self.study_state()
            if state["selected_claim_id"] == label(before["id"], "C"):
                changes = {"selected_revision": revision, "phase": "argument"}
                if state["status"] in ("ready_for_human", "finalized"):
                    changes["status"] = "active"
                self._set_study("採用主張が改訂されたため論証と根拠を再確認: " + reason, **changes)
                self.event("study.revision_followed", reason,
                           {"claim_id": claim, "revision": revision, "before": state,
                            "after": self.study_state(), "actor": "human"}, claim_id=before["id"])
        return self.show_claim(claim)

    def decide_claim(self, claim, status, reason):
        reason = required(reason, "判断理由")
        if status not in ("candidate", "held", "withdrawn"):
            raise ResearchError("判断は candidate / held / withdrawn を指定してください。")
        with self.db:
            before = self.claim_row(claim)
            if status == "candidate" and self.db.execute(
                "SELECT 1 FROM evidence WHERE claim_id=? AND claim_revision=? AND status IN ('disputed','inaccessible')",
                (before["id"], before["revision"]),
            ).fetchone():
                raise ResearchError("現行版に問題のある根拠が残っています。根拠を再確認するか主張を改訂してください。")
            self.db.execute("UPDATE claims SET status=? WHERE id=?", (status, before["id"]))
            self.event("claim.decided", reason, {"before": before, "after": self.claim_row(claim)}, claim_id=before["id"])
        return self.show_claim(claim)

    def add_evidence(self, claim, source, source_version, locator, quote, interpretation, relation, level, status, *, document=None, page=None):
        source = required(source, "出典")
        interpretation = required(interpretation, "解釈")
        if relation not in ("supports", "opposes", "qualifies", "unassessed") or level not in ("search", "abstract", "passage"):
            raise ResearchError("根拠の関係または確認範囲が不正です。")
        if status not in ("unchecked", "checked", "inaccessible"):
            raise ResearchError("根拠の初期状態が不正です。")
        if status == "checked":
            required(quote, "確認した原文")
            required(locator, "原文箇所")
        with self.db:
            current = self.claim_row(claim)
            if document is not None:
                if not required(quote, "原文") in self.document_page(document, page):
                    raise ResearchError("引用が保存された本文に一致しません。根拠を登録しません。")
            result = self.db.execute(
                "INSERT INTO evidence(claim_id,claim_revision,source,source_version,locator,quote,interpretation,relation,level,status,acquired_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (current["id"], current["revision"], source, source_version.strip(), locator.strip(), quote.strip(), interpretation, relation, level, status, now()),
            )
            eid = result.lastrowid
            if document is not None:
                self.db.execute("INSERT INTO evidence_documents VALUES(?,?,?)", (eid, identifier(document, "D"), page))
            if current["status"] not in ("held", "withdrawn"):
                self.db.execute("UPDATE claims SET status='needs_review' WHERE id=?", (current["id"],))
            self.event("evidence.added", "根拠を登録。主張との支持関係の確認状態は別途記録。", {
                "evidence": self.evidence_row(label(eid, "E")), "provenance": self.evidence_provenance(label(eid, "E")), "claim_before": current, "claim_after": self.claim_row(claim)
            }, claim_id=current["id"], evidence_id=eid)
        return self.show_evidence(label(eid, "E"))

    def show_evidence(self, evidence):
        row = self.evidence_row(evidence)
        row["id"] = label(row["id"], "E")
        row["claim_id"] = label(row["claim_id"], "C")
        current = self.claim_row(row["claim_id"])
        row["applies_to_current_revision"] = row["claim_revision"] == current["revision"]
        row["ai_reviews"] = self.list_runs(evidence_id=identifier(evidence, "E"))
        row["document_source"] = self.evidence_provenance(evidence)
        annotation = self.db.execute("SELECT * FROM evidence_annotations WHERE evidence_id=?", (identifier(evidence, "E"),)).fetchone()
        row["annotation"] = dict(annotation) if annotation else None
        if row["annotation"]:
            item = row["annotation"]
            item["limitations"] = json.loads(item.pop("limitations_json"))
            if item["derived_from"]:
                item["derived_from"] = label(item["derived_from"], "E")
            if item["task_id"]:
                item["task_id"] = label(item["task_id"], "T")
        return row

    def assess_evidence(self, evidence, status, reason):
        reason = required(reason, "確認理由")
        if status not in ("unchecked", "checked", "disputed", "inaccessible"):
            raise ResearchError("根拠の確認状態が不正です。")
        with self.db:
            before = self.evidence_row(evidence)
            if status == "checked":
                required(before["quote"], "確認した原文（原文を添えた根拠を新たに登録してください）")
                required(before["locator"], "原文箇所")
            claim = label(before["claim_id"], "C")
            current = self.claim_row(claim)
            self.db.execute("UPDATE evidence SET status=? WHERE id=?", (status, before["id"]))
            # Even a historical evidence problem can affect today's argument.
            # Resolving evidence never automatically resolves a claim.
            if current["status"] not in ("held", "withdrawn"):
                self.db.execute("UPDATE claims SET status='needs_review' WHERE id=?", (before["claim_id"],))
            self.event("evidence.assessed", reason, {
                "before": before, "after": self.evidence_row(evidence),
                "claim_before": current, "claim_after": self.claim_row(claim)
            }, claim_id=before["claim_id"], evidence_id=before["id"])
            if status in ("disputed", "inaccessible"):
                descendants = self.db.execute(
                    "WITH RECURSIVE affected(id) AS (SELECT evidence_id FROM evidence_annotations WHERE derived_from=? UNION SELECT a.evidence_id FROM evidence_annotations a JOIN affected d ON a.derived_from=d.id) SELECT e.id,e.claim_id FROM evidence e JOIN affected d ON e.id=d.id",
                    (before["id"],),
                ).fetchall()
                for descendant in descendants:
                    self.db.execute("UPDATE evidence SET status=? WHERE id=?", (status, descendant["id"]))
                    self.db.execute("UPDATE claims SET status='needs_review' WHERE id=? AND status NOT IN ('held','withdrawn')", (descendant["claim_id"],))
                    self.event("evidence.invalidated", reason,
                               {"evidence_id": label(descendant["id"], "E"), "source_evidence_id": evidence, "status": status},
                               claim_id=descendant["claim_id"], evidence_id=descendant["id"])
            state = self.study_state()
            affected_claims = {before["claim_id"]}
            if status in ("disputed", "inaccessible"):
                affected_claims.update(row["claim_id"] for row in descendants)
            if state["selected_claim_id"] and identifier(state["selected_claim_id"], "C") in affected_claims:
                if state["status"] in ("ready_for_human", "finalized"):
                    self._set_study("採用立場の根拠が変更されたため再確認", status="active", phase="evidence_review")
        return self.show_evidence(evidence)

    def status(self):
        project = dict(self.db.execute("SELECT * FROM project").fetchone())
        claims = []
        for r in self.db.execute("SELECT * FROM claims ORDER BY id"):
            row = dict(r)
            cid = row["id"]
            row["id"] = label(cid, "C")
            row["current_evidence_count"] = self.db.execute(
                "SELECT count(*) FROM evidence WHERE claim_id=? AND claim_revision=?", (cid, row["revision"])
            ).fetchone()[0]
            row["unresolved_current_evidence"] = [label(e[0], "E") for e in self.db.execute(
                "SELECT id FROM evidence WHERE claim_id=? AND claim_revision=? AND status!='checked' ORDER BY id", (cid, row["revision"])
            )]
            claims.append(row)
        return {"project": project, "study": self.study_state(), "claims": claims,
                "tasks": self.list_tasks(), "artifacts": self.list_artifacts(), "issues": self.list_issues(),
                "runs": self.list_runs(), "literature": {"papers": self.list_papers(), "searches": self.list_searches()},
                "notice": "candidateは候補であり、学術的な正しさや新規性の保証ではありません。checkedは指定範囲の手動確認です。AI評価は別の暫定記録です。"}

    def start_review(self, evidence, config):
        # Snapshot and run creation must refer to the same committed research state.
        try:
            self.db.execute("BEGIN IMMEDIATE")
            source = self.evidence_row(evidence)
            claim = self.claim_row(label(source["claim_id"], "C"))
            if source["claim_revision"] != claim["revision"]:
                raise ResearchError("根拠は旧版の主張に対応しています。現行版との関係を再検討して根拠を登録してください。")
            required(source["quote"], "Codexへ渡す原文")
            required(source["locator"], "原文箇所")
            if len(source["quote"]) > 30000:
                raise ResearchError("原文は30,000文字以内の該当箇所に限定してください。")
            if source["status"] == "inaccessible":
                raise ResearchError("取得不能の資料は確認できません。資料を取得してから根拠を再登録してください。")
            snapshot = {"claim": claim, "evidence": source}
            provenance = self.evidence_provenance(evidence)
            if provenance is not None:
                if source["quote"] not in self.document_page(provenance["document_id"], provenance["page"]):
                    raise ResearchError("引用と保存資料の対応を確認できません。")
                snapshot["document_source"] = provenance
            cursor = self.db.execute(
                "INSERT INTO runs(task,status,claim_id,claim_revision,evidence_id,input_json,config_json,started_at) VALUES('evidence_review','running',?,?,?,?,?,?)",
                (claim["id"], claim["revision"], source["id"], json.dumps(snapshot, ensure_ascii=False), json.dumps(config, ensure_ascii=False), now()),
            )
            rid = cursor.lastrowid
            self.event("run.started", "登録された原文と主張の関係をCodexに確認依頼", {"run_id": label(rid, "R"), "config": config}, claim_id=claim["id"], evidence_id=source["id"])
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.show_run(label(rid, "R"))

    def run_row(self, run):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (identifier(run, "R"),)).fetchone()
        if row is None:
            raise ResearchError(f"実行 {run} が見つかりません。")
        return dict(row)

    def show_run(self, run):
        row = self.run_row(run)
        row["id"] = label(row["id"], "R")
        row["claim_id"] = label(row["claim_id"], "C")
        row["evidence_id"] = label(row["evidence_id"], "E")
        row["input"] = json.loads(row.pop("input_json"))
        row["config"] = json.loads(row.pop("config_json"))
        raw = row.pop("result_json")
        row["result"] = json.loads(raw) if raw is not None else None
        return row

    def list_runs(self, evidence_id=None):
        rows = self.db.execute(
            "SELECT id,task,status,claim_id,claim_revision,evidence_id,started_at,finished_at,error FROM runs"
            + (" WHERE evidence_id=?" if evidence_id is not None else "") + " ORDER BY id",
            (evidence_id,) if evidence_id is not None else (),
        )
        result = []
        for item in rows:
            row = dict(item)
            for key, prefix in (("id", "R"), ("claim_id", "C"), ("evidence_id", "E")):
                row[key] = label(row[key], prefix)
            result.append(row)
        return result

    def finish_run(self, run, status, result=None, error=None):
        if status not in ("completed", "failed", "timed_out", "interrupted"):
            raise ResearchError("実行の終了状態が不正です。")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.run_row(run)
            if row["status"] != "running":
                raise ResearchError("この実行は終了済みです。結果を上書きしません。")
            before = self.claim_row(label(row["claim_id"], "C"))
            snapshot = json.loads(row["input_json"])
            if status == "completed":
                # Late results are preserved but never silently applied to newer input.
                if before != snapshot["claim"] or self.evidence_row(label(row["evidence_id"], "E")) != snapshot["evidence"]:
                    status = "stale"
                    error = "処理中に主張または根拠が変更されました。結果は保存しましたが現行状態へ適用していません。"
                elif before["status"] not in ("held", "withdrawn"):
                    self.db.execute("UPDATE claims SET status='needs_review' WHERE id=?", (row["claim_id"],))
            self.db.execute("UPDATE runs SET status=?,result_json=?,error=?,finished_at=? WHERE id=?", (
                status, json.dumps(result, ensure_ascii=False) if result is not None else None,
                error, now(), row["id"],
            ))
            self.event("run.finished", error or "AIによる暫定評価を保存。手動確認状態は変更しない。", {
                "run_id": run, "status": status, "result": result,
                "claim_before": before, "claim_after": self.claim_row(label(row["claim_id"], "C")),
            }, claim_id=row["claim_id"], evidence_id=row["evidence_id"])
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.show_run(run)

    def abandon_run(self, run, reason):
        reason = required(reason, "中断理由")
        return self.finish_run(run, "interrupted", error="手動で記録を中断: " + reason)

    def history(self, claim=None):
        cid = self.claim_row(claim)["id"] if claim else None
        rows = self.db.execute(
            "SELECT * FROM events" + (" WHERE claim_id=?" if cid is not None else "") + " ORDER BY id",
            (cid,) if cid is not None else (),
        )
        result = []
        for r in rows:
            row = dict(r)
            row["payload"] = json.loads(row["payload"])
            if row["claim_id"] is not None:
                row["claim_id"] = label(row["claim_id"], "C")
            if row["evidence_id"] is not None:
                row["evidence_id"] = label(row["evidence_id"], "E")
            result.append(row)
        return result
