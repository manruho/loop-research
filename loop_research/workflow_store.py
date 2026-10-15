"""Persistent argument branches, research control, tasks, manuscripts, and reviews."""

import hashlib
import json

from .common import ResearchError, identifier, label, now, required


ARTIFACT_PREFIXES = {
    "orientation": "N", "reading": "L", "comparison": "K",
    "outline": "O", "draft": "A", "review": "V", "verify": "U", "decision": "J", "redesign": "Z",
    "selection": "Y",
}
PREFIX_KINDS = {value: key for key, value in ARTIFACT_PREFIXES.items()}
DEFAULT_SETTINGS = {"provider": "arxiv", "paper_limit": 12, "pages_per_document": 2,
                    "query_limit": 6, "search_budget": 12, "model": None, "timeout": 180, "reasoning_effort": "medium", "review_model": None, "review_effort": "low",
                    "citation_seeds": 3, "citation_limit": 10, "citation_budget": 30,
                    "draft_checkpoint_readings": 2, "semantic_selection": 1, 'selection_page_limit': 16}
OWNER_SCHEMA = """
ALTER TABLE searches ADD COLUMN owner_pid INTEGER;
ALTER TABLE searches ADD COLUMN owner_identity TEXT;
ALTER TABLE fetches ADD COLUMN owner_pid INTEGER;
ALTER TABLE fetches ADD COLUMN owner_identity TEXT;
"""

WORKFLOW_SCHEMA = """
CREATE TABLE study_state (
    id INTEGER PRIMARY KEY CHECK(id=1), question TEXT NOT NULL,
    audience TEXT NOT NULL, venue TEXT NOT NULL,
    status TEXT NOT NULL, phase TEXT NOT NULL, reason TEXT NOT NULL,
    selected_claim_id INTEGER REFERENCES claims(id), selected_revision INTEGER,
    anchor_claim_id INTEGER REFERENCES claims(id), settings_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, semantic_key TEXT NOT NULL,
    status TEXT NOT NULL, input_json TEXT NOT NULL, prompt TEXT NOT NULL,
    schema_json TEXT NOT NULL, config_json TEXT NOT NULL, result_json TEXT,
    execution_json TEXT, error TEXT, process_id INTEGER, process_identity TEXT,
    started_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE profiles (
    claim_id INTEGER NOT NULL, revision INTEGER NOT NULL, content_json TEXT NOT NULL,
    task_id INTEGER REFERENCES tasks(id),
    PRIMARY KEY(claim_id,revision),
    FOREIGN KEY(claim_id,revision) REFERENCES revisions(claim_id,revision)
);
CREATE TABLE claim_edges (
    id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL, parent_revision INTEGER NOT NULL,
    child_id INTEGER NOT NULL, child_revision INTEGER NOT NULL,
    kind TEXT NOT NULL, reason TEXT NOT NULL, task_id INTEGER REFERENCES tasks(id),
    FOREIGN KEY(parent_id,parent_revision) REFERENCES revisions(claim_id,revision),
    FOREIGN KEY(child_id,child_revision) REFERENCES revisions(claim_id,revision)
);
CREATE TABLE evidence_annotations (
    evidence_id INTEGER PRIMARY KEY REFERENCES evidence(id),
    derived_from INTEGER REFERENCES evidence(id), kind TEXT NOT NULL,
    statement TEXT NOT NULL, limitations_json TEXT NOT NULL,
    task_id INTEGER REFERENCES tasks(id)
);
CREATE TABLE artifacts (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, semantic_key TEXT NOT NULL,
    claim_id INTEGER, claim_revision INTEGER, task_id INTEGER REFERENCES tasks(id),
    content_json TEXT NOT NULL, input_json TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(claim_id,claim_revision) REFERENCES revisions(claim_id,revision)
);
CREATE TABLE issues (
    id INTEGER PRIMARY KEY, review_id INTEGER NOT NULL REFERENCES artifacts(id),
    draft_id INTEGER NOT NULL REFERENCES artifacts(id), severity TEXT NOT NULL,
    status TEXT NOT NULL, content_json TEXT NOT NULL,
    response TEXT NOT NULL, remaining_uncertainty TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


class WorkflowStore:
    def study_state(self):
        row = self.db.execute("SELECT * FROM study_state WHERE id=1").fetchone()
        if row is None:
            project = self.db.execute("SELECT title FROM project").fetchone()
            if project is None:
                raise ResearchError("研究を初期化してください。")
            with self.db:
                self.db.execute(
                    "INSERT INTO study_state VALUES(1,?,?,?,'active','orientation',?,NULL,NULL,NULL,?,?)",
                    (project[0], "Academic readers in ML, HCI, and information science",
                     "unspecified", "研究を開始", encoded(DEFAULT_SETTINGS), now()),
                )
            row = self.db.execute("SELECT * FROM study_state WHERE id=1").fetchone()
        result = dict(row)
        result["settings"] = dict(DEFAULT_SETTINGS, **json.loads(result.pop("settings_json")))
        for key in ("selected_claim_id", "anchor_claim_id"):
            if result[key] is not None:
                result[key] = label(result[key], "C")
        return result

    def _set_study(self, reason, **values):
        allowed = {"question", "audience", "venue", "status", "phase", "selected_claim_id",
                   "selected_revision", "anchor_claim_id", "settings_json"}
        if not set(values) <= allowed:
            raise ResearchError("研究状態の更新項目が不正です。")
        values.update(reason=reason, updated_at=now())
        self.db.execute("UPDATE study_state SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=1",
                        tuple(values.values()))

    def configure_study(self, reason, question=None, audience=None, venue=None, settings=None):
        reason = required(reason, "変更理由")
        before = self.study_state()
        changes = {}
        for key, value in (("question", question), ("audience", audience), ("venue", venue)):
            if value is not None:
                changes[key] = required(value, key)
        if settings:
            merged = dict(before["settings"])
            merged.update(settings)
            if merged["provider"] not in ("arxiv", "crossref", "openalex"):
                raise ResearchError("検索先が不正です。")
            for key, maximum in (("paper_limit", 20), ("pages_per_document", 20), ("query_limit", 10), ("search_budget", 100), ("citation_seeds", 10), ("citation_limit", 20), ("citation_budget", 100)):
                if type(merged[key]) is not int or not (0 if key.startswith("citation_") else 1) <= merged[key] <= maximum:
                    raise ResearchError(f"{key}が有効な範囲にありません。")
            for key, maximum in (("draft_checkpoint_readings", 20), ("semantic_selection", 1)):
                if type(merged[key]) is not int or not 0 <= merged[key] <= maximum:
                    raise ResearchError(f"{key}が有効な範囲にありません。")
            if type(merged['selection_page_limit']) is not int or not 1 <= merged['selection_page_limit'] <= 60:
                raise ResearchError('selection_page_limitは1〜60の整数です。')
            if merged["review_effort"] not in ("low", "medium", "high", "xhigh", "max", "ultra"):
                raise ResearchError("査読の推論の強さが不正です。")
            if merged["review_model"] and merged["model"] == merged["review_model"]:
                raise ResearchError("執筆と査読には異なるモデルを指定してください。")
            from .codex import validate_review_options
            validate_review_options(merged["timeout"], merged["model"])
            validate_review_options(merged["timeout"], merged["review_model"])
            if merged["reasoning_effort"] not in ("low", "medium", "high", "xhigh", "max", "ultra"):
                raise ResearchError("推論の強さが不正です。")
            changes["settings_json"] = encoded(merged)
        research_changed = any(key in changes and changes[key] != before[key]
                               for key in ("question", "audience", "venue"))
        if not research_changed and ("settings_json" not in changes
                                     or json.loads(changes["settings_json"]) == before["settings"]):
            return before
        if "question" in changes and changes["question"] != before["question"]:
            changes.update(selected_claim_id=None, selected_revision=None, anchor_claim_id=None,
                           status="active", phase="orientation")
        elif any(key in changes for key in ("audience", "venue")):
            changes.update(status="active", phase="reconsider")
        if settings and any(before["settings"].get(k) != settings[k] for k in ("model", "review_model", "review_effort") if k in settings):
            if before["status"] in ("ready_for_human", "finalized"):
                changes.update(status="active", phase="review")
        with self.db:
            self._set_study(reason, **changes)
            self.event("study.configured", reason, {"before": before, "after": self.study_state(),
                       "actor": "human", "research_changed": research_changed})
        return self.study_state()

    def control_study(self, status, reason, actor="system"):
        reason = required(reason, "判断理由")
        if status not in ("active", "held", "needs_human", "budget_exhausted", "interrupted",
                          "ready_for_human", "finalized", "withdrawn"):
            raise ResearchError("研究の状態が不正です。")
        before = self.study_state()
        with self.db:
            self._set_study(reason, status=status)
            self.event("study.controlled", reason, {"before": before, "after": self.study_state(), "actor": actor})
        return self.study_state()

    def select_claim(self, claim, reason):
        reason = required(reason, "採用理由")
        current = self.claim_row(claim)
        if current["status"] in ("held", "withdrawn"):
            raise ResearchError("保留・撤回された主張は、先に明示的に再検討してください。")
        self.study_state()
        with self.db:
            self._set_study(reason, selected_claim_id=current["id"], selected_revision=current["revision"],
                            status="active", phase="argument")
            self.event("study.claim_selected", reason, {"claim_id": claim, "revision": current["revision"], "actor": "human"}, claim_id=current["id"])
        return self.study_state()

    def profile(self, claim, revision=None):
        current = self.claim_row(claim)
        row = self.db.execute("SELECT content_json FROM profiles WHERE claim_id=? AND revision=?",
                              (current["id"], revision or current["revision"])).fetchone()
        return json.loads(row[0]) if row else None

    def claim_graph(self):
        result = []
        for row in self.db.execute("SELECT * FROM claim_edges ORDER BY id"):
            item = dict(row)
            item["parent_id"], item["child_id"] = label(item["parent_id"], "C"), label(item["child_id"], "C")
            if item["task_id"]:
                item["task_id"] = label(item["task_id"], "T")
            result.append(item)
        return result

    def _new_claim(self, text, reason, profile=None, task_id=None):
        text = required(text, "主張")
        cursor = self.db.execute("INSERT INTO claims(revision,text,status) VALUES(1,?,'candidate')", (text,))
        cid = cursor.lastrowid
        self.db.execute("INSERT INTO revisions VALUES(?,1,?,?,?)", (cid, text, reason, now()))
        if profile is not None:
            profile = dict(profile, _question=self.study_state()["question"])
            self.db.execute("INSERT INTO profiles VALUES(?,1,?,?)", (cid, encoded(profile), task_id))
        self.event("claim.added", reason, {"text": text, "revision": 1, "actor": "AI" if task_id else "human"}, claim_id=cid)
        return cid

    def ensure_anchor(self):
        state = self.study_state()
        if state["anchor_claim_id"]:
            return state["anchor_claim_id"]
        with self.db:
            cid = self._new_claim("研究の問い: " + state["question"], "文献の読解と分岐を結び付ける問い", {"kind": "research_question", "scope": state["question"]})
            self._set_study("研究の問いを記録", anchor_claim_id=cid)
        return label(cid, "C")

    def _copy_evidence(self, source_id, claim_id, revision, task_id=None):
        source = self.evidence_row(source_id)
        if source["status"] in ("disputed", "inaccessible"):
            raise ResearchError("問題あり・取得不能の根拠は新しい主張へ流用できません。")
        row = self.db.execute(
            "INSERT INTO evidence(claim_id,claim_revision,source,source_version,locator,quote,interpretation,relation,level,status,acquired_at) VALUES(?,?,?,?,?,?,?,'unassessed',?,'unchecked',?)",
            (claim_id, revision, source["source"], source["source_version"], source["locator"], source["quote"],
             "改訂・分岐した主張との関係は再評価が必要。元の根拠: " + source_id, source["level"], now()),
        )
        eid = row.lastrowid
        link = self.db.execute("SELECT document_id,page FROM evidence_documents WHERE evidence_id=?", (source["id"],)).fetchone()
        if link:
            self.db.execute("INSERT INTO evidence_documents VALUES(?,?,?)", (eid, link[0], link[1]))
        annotation = self.db.execute("SELECT kind,statement,limitations_json FROM evidence_annotations WHERE evidence_id=?", (source["id"],)).fetchone()
        self.db.execute("INSERT INTO evidence_annotations VALUES(?,?,?,?,?,?)",
                        (eid, source["id"], annotation[0] if annotation else "unclassified",
                         annotation[1] if annotation else source["interpretation"],
                         annotation[2] if annotation else encoded(["分岐した主張との支持関係は未確認"]), task_id))
        self.event("evidence.derived", "根拠を共有するが支持関係・手動確認状態は継承しない",
                   {"evidence_id": label(eid, "E"), "derived_from": source_id, "revision": revision}, claim_id=claim_id, evidence_id=eid)
        return label(eid, "E")

    def fork_claim(self, parents, text, reason, kind="fork"):
        reason = required(reason, "分岐・統合の理由")
        if not parents or len(set(parents)) != len(parents):
            raise ResearchError("親となる主張を重複なく指定してください。")
        before = [self.claim_row(parent) for parent in parents]
        with self.db:
            cid = self._new_claim(text, reason, {"kind": "hypothesis", "scope": text,
                                               "open_questions": ["分岐した立場の根拠と独自性は未確認"]})
            sources = set()
            for parent in before:
                self.db.execute("INSERT INTO claim_edges(parent_id,parent_revision,child_id,child_revision,kind,reason) VALUES(?,?,?,1,?,?)",
                                (parent["id"], parent["revision"], cid, kind, reason))
                for row in self.db.execute("SELECT id FROM evidence WHERE claim_id=? AND claim_revision=? AND status NOT IN ('disputed','inaccessible')", (parent["id"], parent["revision"])):
                    sources.add(label(row[0], "E"))
            for source in sorted(sources):
                self._copy_evidence(source, cid, 1)
            self.event("claim.branched", reason, {"parents": parents, "child": label(cid, "C"), "kind": kind}, claim_id=cid)
        return self.show_claim(label(cid, "C"))

    def domain_fingerprint(self):
        state = self.study_state()
        selected = {key: state[key] for key in (
            "question", "audience", "venue", "status", "selected_claim_id",
            "selected_revision", "anchor_claim_id", "settings")}
        def rows(sql):
            return [tuple(row) for row in self.db.execute(sql)]
        return digest({
            "study": selected,
            "claims": rows("SELECT * FROM claims ORDER BY id"),
            "profiles": rows("SELECT * FROM profiles ORDER BY claim_id,revision"),
            "papers": rows("SELECT id,title,doi,url,pdf_url,source_version,abstract FROM papers ORDER BY id"),
            "documents": rows("SELECT id,sha256,status,extractor FROM documents ORDER BY id"),
            "evidence": rows("SELECT * FROM evidence ORDER BY id"),
            "links": rows("SELECT * FROM evidence_documents ORDER BY evidence_id"),
            "citation_edges": rows("SELECT * FROM citation_edges ORDER BY search_id,citing_id,cited_id"),
            "artifacts": rows("SELECT id,kind,semantic_key FROM artifacts WHERE kind!='decision' ORDER BY id"),
            "issues": rows("SELECT id,status,response,remaining_uncertainty FROM issues ORDER BY id"),
            "operator_decisions": rows("SELECT id,action,reason FROM events WHERE action='study.guidance' OR action='issue.responded' OR (action='study.claim_selected' AND json_extract(payload,'$.actor')='human') OR (action='study.configured' AND json_extract(payload,'$.research_changed')=1) ORDER BY id"),
            "searches": rows("SELECT id,provider,query,status FROM searches ORDER BY id"),
            "fetches": rows("SELECT id,paper_id,status,document_id FROM fetches ORDER BY id"),
        })

    def start_task(self, kind, semantic_key, data, prompt, schema, config, expected_fingerprint):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if self.domain_fingerprint() != expected_fingerprint:
                raise ResearchError("作業準備中に研究状態が変わりました。最新の状態で再実行してください。")
            cursor = self.db.execute(
                "INSERT INTO tasks(kind,semantic_key,status,input_json,prompt,schema_json,config_json,started_at) VALUES(?,?,'running',?,?,?,?,?)",
                (kind, semantic_key, encoded({"fingerprint": expected_fingerprint, "data": data}),
                 prompt, encoded(schema), encoded(config), now()),
            )
            tid = cursor.lastrowid
            self.event("task.started", f"{kind}の作業を開始", {"task_id": label(tid, "T"), "kind": kind, "semantic_key": semantic_key})
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.show_task(label(tid, "T"))

    def task_process(self, task, pid, identity):
        with self.db:
            cursor = self.db.execute("UPDATE tasks SET process_id=?,process_identity=? WHERE id=? AND status='running'",
                                     (pid, identity, identifier(task, "T")))
            if cursor.rowcount != 1:
                raise ResearchError("作業はすでに中断されています。")

    def task_row(self, task):
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (identifier(task, "T"),)).fetchone()
        if row is None:
            raise ResearchError(f"作業 {task} が見つかりません。")
        return dict(row)

    def show_task(self, task):
        row = self.task_row(task)
        row["id"] = label(row["id"], "T")
        for key in ("input", "schema", "config", "result", "execution"):
            raw = row.pop(key + "_json")
            row[key] = json.loads(raw) if raw is not None else None
        return row

    def list_tasks(self):
        result = []
        for row in self.db.execute("SELECT id,kind,status,semantic_key,error,started_at,finished_at FROM tasks ORDER BY id"):
            item = dict(row)
            item["id"] = label(item["id"], "T")
            result.append(item)
        return result

    def finish_task(self, task, status, result=None, execution=None, error=None, apply=None):
        if status not in ("completed", "failed", "timed_out", "interrupted"):
            raise ResearchError("作業の終了状態が不正です。")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.task_row(task)
            if row["status"] != "running":
                raise ResearchError("終了済みの作業を上書きできません。")
            original = json.loads(row["input_json"])
            if status == "completed" and self.domain_fingerprint() != original["fingerprint"]:
                status, error = "stale", "研究状態が変わったため、結果は保存しますが適用しません。"
            if status == "completed" and apply:
                apply(row["id"], row["semantic_key"], original["data"], result)
            self.db.execute(
                "UPDATE tasks SET status=?,result_json=?,execution_json=?,error=?,finished_at=? WHERE id=?",
                (status, encoded(result) if result is not None else None,
                 encoded(execution) if execution is not None else None, error, now(), row["id"]),
            )
            self.event("task.finished", error or f"{row['kind']}の暫定結果を保存",
                       {"task_id": task, "kind": row["kind"], "status": status})
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.show_task(task)

    def _artifact(self, kind, key, task_id, data, content, claim=None):
        current = self.claim_row(claim) if claim else None
        cursor = self.db.execute(
            "INSERT INTO artifacts(kind,semantic_key,claim_id,claim_revision,task_id,content_json,input_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (kind, key, current["id"] if current else None, current["revision"] if current else None,
             task_id, encoded(content), encoded(data), now()),
        )
        return label(cursor.lastrowid, ARTIFACT_PREFIXES[kind])

    def artifact_row(self, artifact):
        prefix = str(artifact).upper()[:1]
        if prefix not in PREFIX_KINDS:
            raise ResearchError("成果物のIDが不正です。")
        row = self.db.execute("SELECT * FROM artifacts WHERE id=? AND kind=?",
                              (identifier(artifact, prefix), PREFIX_KINDS[prefix])).fetchone()
        if row is None:
            raise ResearchError(f"成果物 {artifact} が見つかりません。")
        return dict(row)

    def show_artifact(self, artifact):
        row = self.artifact_row(artifact)
        row["id"] = label(row["id"], ARTIFACT_PREFIXES[row["kind"]])
        row["content"] = json.loads(row.pop("content_json"))
        row["input"] = json.loads(row.pop("input_json"))
        if row["claim_id"]:
            row["claim_id"] = label(row["claim_id"], "C")
        if row["task_id"]:
            row["task_id"] = label(row["task_id"], "T")
        return row

    def latest_artifact(self, kind, key=None):
        row = self.db.execute("SELECT id FROM artifacts WHERE kind=?" + (" AND semantic_key=?" if key else "") + " ORDER BY id DESC LIMIT 1",
                              (kind, key) if key else (kind,)).fetchone()
        return self.show_artifact(label(row[0], ARTIFACT_PREFIXES[kind])) if row else None

    def list_artifacts(self, kind=None):
        rows = self.db.execute("SELECT id,kind,claim_id,claim_revision,task_id,created_at FROM artifacts"
                               + (" WHERE kind=?" if kind else "") + " ORDER BY id",
                               (kind,) if kind else ())
        result = []
        for row in rows:
            item = dict(row)
            item["id"] = label(item["id"], ARTIFACT_PREFIXES[item["kind"]])
            if item["claim_id"]:
                item["claim_id"] = label(item["claim_id"], "C")
            if item["task_id"]:
                item["task_id"] = label(item["task_id"], "T")
            result.append(item)
        return result

    def list_issues(self, draft=None):
        aid = self.artifact_row(draft)["id"] if draft else None
        rows = self.db.execute("SELECT * FROM issues" + (" WHERE draft_id=?" if aid else "") + " ORDER BY id",
                               (aid,) if aid else ())
        result = []
        for row in rows:
            item = dict(row)
            item["id"], item["review_id"], item["draft_id"] = label(item["id"], "I"), label(item["review_id"], "V"), label(item["draft_id"], "A")
            item["content"] = json.loads(item.pop("content_json"))
            result.append(item)
        return result

    def respond_issue(self, issue, status, reason, uncertainty):
        reason = required(reason, "応答理由")
        if status not in ("addressed", "deferred", "rejected"):
            raise ResearchError("指摘への応答状態が不正です。")
        with self.db:
            row = self.db.execute("SELECT * FROM issues WHERE id=?", (identifier(issue, "I"),)).fetchone()
            if row is None:
                raise ResearchError(f"指摘 {issue} が見つかりません。")
            self.db.execute("UPDATE issues SET status=?,response=?,remaining_uncertainty=?,updated_at=? WHERE id=?",
                            (status, reason, uncertainty, now(), row["id"]))
            self.event("issue.responded", reason, {"issue_id": issue, "before_status": row["status"],
                       "after_status": status, "remaining_uncertainty": uncertainty, "actor": "human"})
        return next(item for item in self.list_issues() if item["id"] == issue.upper())

    def apply_orientation(self, tid, key, data, result):
        self._artifact("orientation", key, tid, data, result)
        status = {"proceed": "active", "consult": "needs_human", "hold": "held"}[result["recommendation"]]
        self._set_study(result["reason"], status=status, phase="literature")

    def apply_selection(self, tid, key, data, result):
        self._artifact('selection', key, tid, data, result)
        self.event('study.selection', '意味的選別を暫定記録。原文の読解・科学的支持とは区別。',
                   {'target': data['target'], 'task_id': label(tid, 'T')})

    def apply_reading(self, tid, key, data, result):
        doc = self.document_row(data["document_id"])
        paper = self.paper_row(label(doc["paper_id"], "P"))
        claim = self.claim_row(data["claim_id"])
        content = json.loads(encoded(result))
        for finding in content["findings"]:
            if finding["excerpt"] not in self.document_page(data["document_id"], data["page"]):
                raise ResearchError("読解の引用が保存資料と一致しません。")
            level = "abstract" if doc["origin"] == "abstract" else "passage"
            cursor = self.db.execute(
                "INSERT INTO evidence(claim_id,claim_revision,source,source_version,locator,quote,interpretation,relation,level,status,acquired_at) VALUES(?,?,?,?,?,?,?,'unassessed',?,'unchecked',?)",
                (claim["id"], claim["revision"], paper["title"] + " — " + paper["url"],
                 doc["source_version"], f"{data['document_id']}, {doc['origin']} unit/page {data['page']}",
                 finding["excerpt"], finding["statement"], level, now()),
            )
            eid = cursor.lastrowid
            self.db.execute("INSERT INTO evidence_documents VALUES(?,?,?)", (eid, doc["id"], data["page"]))
            self.db.execute("INSERT INTO evidence_annotations VALUES(?,NULL,?,?,?,?)",
                            (eid, finding["kind"], finding["statement"], encoded(finding["limitations"]), tid))
            finding["evidence_id"] = label(eid, "E")
            self.event("evidence.read", "原文の抜粋とAIの解釈を区別して保存。手動確認は未実施。",
                       {"evidence_id": finding["evidence_id"], "document_id": data["document_id"],
                        "page": data["page"], "kind": finding["kind"], "reading_scope": level},
                       claim_id=claim["id"], evidence_id=eid)
        self._artifact("reading", key, tid, data, content, data["claim_id"])
        self._set_study("指定箇所の読解を記録", phase="positions")

    def apply_exploration(self, tid, key, data, result):
        parent = self.claim_row(data["anchor_claim_id"])
        content = json.loads(encoded(result))
        for candidate in content["candidates"]:
            cid = self._new_claim(candidate["text"], result["reason"], task_id=tid)
            self.db.execute("INSERT INTO claim_edges(parent_id,parent_revision,child_id,child_revision,kind,reason,task_id) VALUES(?,?,?,1,'explore',?,?)",
                            (parent["id"], parent["revision"], cid, result["reason"], tid))
            sources = sorted(set(candidate["supporting_evidence_ids"] + candidate["opposing_evidence_ids"]))
            mapping = {source: self._copy_evidence(source, cid, 1, tid) for source in sources}
            profile = dict(candidate)
            profile["_question"] = self.study_state()["question"]
            for field in ("supporting_evidence_ids", "opposing_evidence_ids"):
                profile[field] = [mapping[source] for source in candidate[field]]
            self.db.execute("INSERT INTO profiles VALUES(?,1,?,?)", (cid, encoded(profile), tid))
            candidate["claim_id"] = label(cid, "C")
            candidate["current_evidence_mapping"] = mapping
        self._artifact("decision", key, tid, data, content)
        if not content["candidates"]:
            self._set_study(result["reason"], status="held", phase="no_viable_position")
        else:
            self._set_study(result["reason"], phase="comparison")

    def apply_comparison(self, tid, key, data, result):
        self._artifact("comparison", key, tid, data, result)
        if result["decision"] == "proceed":
            selected = self.claim_row(result["selected_claim_id"])
            self._set_study(result["reason"], selected_claim_id=selected["id"], selected_revision=selected["revision"],
                            phase="argument", status="active")
            self.event("study.claim_selected", result["reason"], {"claim_id": result["selected_claim_id"],
                       "revision": selected["revision"], "actor": "AI", "assessment": "tentative"},
                       claim_id=selected["id"])
        else:
            if result["decision"] == "hold" and result["requested_searches"]:
                self._set_study(result["reason"], status="active", phase="investigation")
            else:
                self._set_study(result["reason"], status="held" if result["decision"] == "hold" else "withdrawn", phase="comparison")

    def apply_outline(self, tid, key, data, result):
        self._artifact("outline", key, tid, data, result, data["claim_id"])
        self._set_study("中心主張と根拠から論証を設計", phase="draft")

    def apply_draft(self, tid, key, data, result):
        from .roles import blocks
        content = json.loads(encoded(result))
        for number, block in enumerate(blocks(content), 1):
            block["id"] = label(number, "B")
        artifact = self._artifact("draft", key, tid, data, content, data["claim_id"])
        self.event("draft.created", "根拠と主張に対応する英語草稿を保存",
                   {"draft_id": artifact, "claim_id": data["claim_id"], "basis": data["basis"]})
        self._set_study("草稿は作成済み。学術的な確認と内部査読は未完了。", phase="review")

    def apply_revise_draft(self, tid, key, data, result):
        self.apply_draft(tid, key, data, result)
        self.event("draft.revised", "査読に基づく草稿の修正。中心主張の版は保持し、再検証・再査読する。",
                   {"previous_draft": data["previous_draft"]["id"], "review_id": data["review"]["id"], "claim_id": data["claim_id"]})

    def apply_review(self, tid, key, data, result):
        draft = self.artifact_row(data["draft"]["id"])
        review = self._artifact("review", key, tid, data, result, data["claim_id"])
        for finding in result["findings"]:
            self.db.execute("INSERT INTO issues(review_id,draft_id,severity,status,content_json,response,remaining_uncertainty,updated_at) VALUES(?,?,?,'open',?,'',?,?)",
                            (identifier(review, "V"), draft["id"], finding["severity"], encoded(finding),
                             finding["remaining_uncertainty"], now()))
        for assessment in result["prior_issue_assessments"]:
            status = {"resolved": "addressed", "persists": "open", "not_applicable": "not_applicable"}[assessment["status"]]
            self.db.execute("UPDATE issues SET status=?,response=response || ?,remaining_uncertainty=?,updated_at=? WHERE id=?",
                            (status, "\n内部査読 " + review + ": " + assessment["reason"],
                             assessment["remaining_uncertainty"], now(), identifier(assessment["issue_id"], "I")))
            self.event("issue.reassessed", assessment["reason"], {"issue_id": assessment["issue_id"],
                       "review_id": review, "status": status, "remaining_uncertainty": assessment["remaining_uncertainty"]})
        if result["decision"] == "ready_for_human":
            if data.get('discovery_deferred'):
                self._set_study('暫定草稿の査読を記録。予算内で保留した探索へ戻る。', status='active', phase='literature')
            else:
                self._set_study(result["synthesis"], status="ready_for_human", phase="human_review")
        elif result["decision"] == "hold":
            self._set_study(result["synthesis"], status="needs_human", phase="review")
        elif result["decision"] == "withdraw":
            selected = self.claim_row(data["claim_id"])
            self.db.execute("UPDATE claims SET status='withdrawn' WHERE id=?", (selected["id"],))
            self.event("claim.decided", result["synthesis"], {"claim_id": data["claim_id"], "status": "withdrawn", "actor": "AI"},
                       claim_id=selected["id"])
            others = self.db.execute("SELECT 1 FROM claims c JOIN profiles p ON c.id=p.claim_id AND c.revision=p.revision WHERE c.id!=? AND c.status NOT IN ('held','withdrawn') AND json_extract(p.content_json,'$.kind')!='research_question'",
                                     (selected["id"],)).fetchone()
            self._set_study(result["synthesis"], selected_claim_id=None, selected_revision=None,
                            status="active" if others else "withdrawn", phase="comparison")
        else:
            self._set_study(result["synthesis"], status="active", phase="redesign")

    def apply_redesign(self, tid, key, data, result):
        before = self.claim_row(data["claim_id"])
        decision = result["decision"]
        if decision in ("revise", "fork", "merge"):
            result = dict(result, text=required(result["text"], "主張"))
            if decision == "revise":
                cid, revision = before["id"], before["revision"] + 1
                self.db.execute("UPDATE claims SET text=?,revision=?,status='needs_review' WHERE id=?",
                                (result["text"], revision, cid))
                self.db.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (cid, revision, result["text"], result["reason"], now()))
                parents = [before]
            else:
                cid, revision = self._new_claim(result["text"], result["reason"], task_id=tid), 1
                parents = [self.claim_row(parent) for parent in result["parent_claim_ids"]] if decision == "merge" else [before]
            for parent in parents:
                self.db.execute("INSERT INTO claim_edges(parent_id,parent_revision,child_id,child_revision,kind,reason,task_id) VALUES(?,?,?,?,?,?,?)",
                                (parent["id"], parent["revision"], cid, revision, decision, result["reason"], tid))
            copied = [self._copy_evidence(source, cid, revision, tid) for source in result["evidence_ids"]]
            profile = {key: result[key] for key in ("kind", "scope", "assumptions", "counterarguments", "novelty", "open_questions")}
            profile.update(text=result["text"], supporting_evidence_ids=copied, opposing_evidence_ids=[],
                           audience=self.study_state()["audience"], _question=self.study_state()["question"])
            self.db.execute("INSERT INTO profiles VALUES(?,?,?,?)", (cid, revision, encoded(profile), tid))
            self._set_study(result["reason"], selected_claim_id=cid, selected_revision=revision,
                            status="active", phase="argument")
            self.event("claim.redesigned", result["reason"], {"before": before, "after": self.claim_row(label(cid, "C")),
                       "decision": decision, "evidence_ids": copied}, claim_id=cid)
        elif decision == "withdraw":
            self.db.execute("UPDATE claims SET status='withdrawn' WHERE id=?", (before["id"],))
            self._set_study(result["reason"], selected_claim_id=None, selected_revision=None, status="active", phase="comparison")
            self.event("claim.decided", result["reason"], {"claim_id": data["claim_id"], "status": "withdrawn", "actor": "AI"}, claim_id=before["id"])
        elif decision == "hold":
            self.db.execute("UPDATE claims SET status='held' WHERE id=?", (before["id"],))
            self._set_study(result["reason"], status="held", phase="redesign")
        else:
            self._set_study(result["reason"], phase="investigation")
        for response in result["issue_responses"]:
            self.db.execute("UPDATE issues SET status=?,response=?,remaining_uncertainty=?,updated_at=? WHERE id=?",
                            ({"research": "investigating", "hold": "deferred", "withdraw": "withdrawn_not_resolved"}.get(decision, "revised_pending_review"),
                             response["response"], response["remaining_uncertainty"], now(), identifier(response["issue_id"], "I")))
            self.event("issue.response_proposed", response["response"], {"issue_id": response["issue_id"],
                       "remaining_uncertainty": response["remaining_uncertainty"], "task_id": label(tid, "T"),
                       "resolution": "not_verified"})
        self._artifact("redesign", key, tid, data, result, label(before["id"], "C"))

    def apply_decision(self, tid, key, data, result):
        self._artifact("decision", key, tid, data, result)
        self.event("study.next_action", result["reason"], {"choice_id": result["choice_id"],
                   "uncertainty": result["uncertainty"], "task_id": label(tid, "T")})
