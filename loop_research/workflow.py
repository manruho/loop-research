"""State-driven research loop, bounded execution, and explicit human intervention."""

import contextlib
import hashlib
import json
import os
import re
import signal
import sqlite3
import sys
import time

from . import roles, verification
from .common import ResearchError, identifier, label, now
from .engine import CodexEngine, EngineFailure, process_alive
from .literature import fetch_paper, search_papers
from .schema import validate
from .workflow_store import digest, encoded


SCHEMAS = {
    "orientation": roles.ORIENTATION, "reading": roles.READING, "exploration": roles.EXPLORATION,
    "comparison": roles.COMPARISON, "outline": roles.OUTLINE, "draft": roles.DRAFT, "revise_draft": roles.DRAFT,
    "review": roles.REVIEW, "verify": verification.VERIFY, "redesign": roles.REDESIGN, "decision": roles.DECISION,
}
HANDLERS = {kind: "apply_" + kind for kind in SCHEMAS}
HANDLERS["exploration"] = "apply_exploration"


@contextlib.contextmanager
def study_lock(store):
    try:
        import fcntl
    except ImportError as exc:
        raise ResearchError("研究の自律実行は現在UbuntuなどUnix環境に対応しています。") from exc
    path = store.path.resolve().with_suffix(store.path.suffix + ".lock")
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ResearchError("この研究は別のプロセスで実行中です。状態確認や保留は別のターミナルで行えます。") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextlib.contextmanager
def termination_as_interrupt():
    old = signal.getsignal(signal.SIGTERM)
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, old)


class ResearchWorkflow:
    def __init__(self, store, engine=None):
        self.store = store
        state = store.study_state()
        self.deadline = None
        self.injected_engine = engine is not None
        self.engine = engine or CodexEngine(state["settings"]["model"], state["settings"]["timeout"],
                                           state["settings"]["reasoning_effort"])


    def engine_for(self, kind):
        if self.injected_engine:
            return self.engine
        settings = self.store.study_state()["settings"]
        if not settings["model"] or not settings["review_model"]:
            raise ResearchError("--model と --review-model を明示してください。再開時は保存済み設定を使います。")
        if settings["model"] == settings["review_model"]:
            raise ResearchError("執筆と査読には異なるモデルが必要です。")
        reviewer = kind in ("review", "verify")
        timeout = settings["timeout"]
        if self.deadline is not None:
            timeout = min(timeout, self.deadline - time.monotonic())
            if timeout <= 0:
                raise ResearchError("今回の実行時間上限に達しました。")
        return CodexEngine(settings["review_model"] if reviewer else settings["model"], timeout,
                           settings["review_effort"] if reviewer else settings["reasoning_effort"])

    def current_verification(self):
        draft = self.current_artifact("draft")
        if not draft:
            return None
        artifact = self.current_artifact("verify")
        if not artifact or artifact["input"]["draft"]["id"] != draft["id"]:
            return None
        if artifact["input"].get("contract_version") != roles.PROMPT_VERSIONS["verify"]:
            return None
        settings = self.store.study_state()["settings"]
        expected = self.engine.config if self.injected_engine else {"model": settings["review_model"], "reasoning_effort": settings["review_effort"]}
        task = self.store.show_task(artifact["task_id"]) if artifact["task_id"] else None
        if not task or any(task["config"].get(k) != expected.get(k) for k in ("model", "reasoning_effort")):
            return None
        return artifact

    def human_guidance(self):
        relevant = {"study.configured", "study.claim_selected", "study.controlled", "issue.responded",
                    "claim.revised", "claim.decided", "study.guidance"}
        result = []
        for event in self.store.history():
            if event["action"] not in relevant:
                continue
            if event["action"] == "study.configured":
                before, after = event["payload"].get("before", {}), event["payload"].get("after", {})
                if not any(before.get(key) != after.get(key) for key in ("question", "audience", "venue")):
                    continue
            if event["action"] == "study.controlled":
                before, after = event["payload"].get("before", {}), event["payload"].get("after", {})
                if after.get("status") != "active" or before.get("status") not in ("held", "needs_human"):
                    continue
            actor = event["payload"].get("actor")
            if actor == "human" or (actor is None and event["action"] in ("claim.revised", "claim.decided")):
                result.append({"action": event["action"], "reason": event["reason"], "created_at": event["created_at"]})
        return result[-10:]

    def candidates(self):
        result = {}
        state = self.store.study_state()
        original_question = self.store.db.execute("SELECT title FROM project").fetchone()[0]
        for row in self.store.db.execute("SELECT * FROM claims WHERE status NOT IN ('held','withdrawn') ORDER BY id"):
            item = dict(row)
            cid = label(item["id"], "C")
            profile = self.store.profile(cid)
            if profile and profile.get("kind") == "research_question":
                continue
            bound_question = profile.get("_question", original_question) if profile else original_question
            if bound_question != state["question"] and cid != state["selected_claim_id"]:
                continue
            item["id"], item["profile"] = cid, profile
            result[cid] = item
        return result

    def scope(self):
        readings = []
        for item in self.store.list_artifacts("reading"):
            artifact = self.store.show_artifact(item["id"])
            data = artifact["input"]
            readings.append({"reading_id": item["id"], "document_id": data["document_id"],
                             "page": data["page"], "start": data.get("start", 0),
                             "characters": len(data["page_text"]), "relevance": artifact["content"]["relevance"],
                             "research_question": data["question"]})
        return {"searches": self.store.list_searches(), "readings": readings, "citation_graph": self.store.citation_graph(),
                "notice": "限定的な検索・指定箇所のAI読解。全文精読・新規性の証明・人間の確認ではない。"}

    def prompt_scope(self):
        """Keep search boundaries without repeating raw provider responses in prompts."""
        scope = self.scope()
        return {
            "searches": [{key: search.get(key) for key in
                          ("id", "provider", "query", "status", "error")}
                         for search in scope["searches"]],
            "readings": scope["readings"],
            "citation_graph": scope["citation_graph"],
            "limits": self.store.study_state()["settings"],
            "notice": scope["notice"],
            "omitted_from_prompt": "Raw search responses remain in the study database and export.",
        }

    def context(self, only_claim=None, for_comparison=False):
        state = self.store.study_state()
        if only_claim:
            row = self.store.claim_row(only_claim)
            claims = {only_claim: {"id": only_claim, "text": row["text"], "revision": row["revision"],
                                  "status": row["status"], "profile": self.store.profile(only_claim)}}
        else:
            claims = self.candidates()
        selected_ids = {identifier(cid, "C") for cid in claims}
        if not only_claim:
            anchor = state["anchor_claim_id"]
            if anchor:
                selected_ids.add(identifier(anchor, "C"))
        evidence, excerpts, paper_scopes = {}, {}, {}
        for row in self.store.db.execute("SELECT id,claim_id,claim_revision FROM evidence WHERE status NOT IN ('disputed','inaccessible') ORDER BY id"):
            if row["claim_id"] not in selected_ids:
                continue
            current = self.store.claim_row(label(row["claim_id"], "C"))
            if row["claim_revision"] != current["revision"]:
                continue
            eid = label(row["id"], "E")
            item = self.store.show_evidence(eid)
            if not item["quote"].strip():
                continue
            source = item["document_source"]
            xid = "X" + digest([item["quote"], item["source"], item["locator"]])[:12]
            excerpts[xid] = {"text": item["quote"], "source": item["source"], "locator": item["locator"]}
            evidence[eid] = {key: item[key] for key in (
                "claim_id", "claim_revision", "interpretation", "relation", "level", "status",
                "document_source", "annotation")}
            evidence[eid]["excerpt_id"] = xid
            if source:
                pid = source["paper_id"]
                level = 1 if source["origin"] == "abstract" else 2
                paper_scopes[pid] = max(level, paper_scopes.get(pid, 0))
        papers = {}
        for item in self.store.list_papers():
            raw = self.store.paper_row(item["id"])
            papers[item["id"]] = {
                "title": raw["title"], "url": raw["url"], "doi": raw["doi"],
                "source_version": raw["source_version"], "authors": json.loads(raw["authors_json"]),
                "seen_scope": ("metadata", "abstract", "passages")[paper_scopes.get(item["id"], 0)],
                "documents": [{"id": doc["id"], "origin": doc["origin"], "status": doc["status"],
                               "sha256": doc["sha256"]} for doc in item["documents"]],
            }
        return {"question": state["question"], "audience": state["audience"], "venue": state["venue"],
                "claims": claims, "evidence": evidence, "excerpts": excerpts, "papers": papers,
                "scope": self.prompt_scope(), "human_guidance": self.human_guidance()}

    def selected(self):
        state = self.store.study_state()
        cid = state["selected_claim_id"]
        if not cid:
            raise ResearchError("立場が未選択です。study compare または study select を実行してください。")
        current = self.store.claim_row(cid)
        if current["status"] in ("held", "withdrawn"):
            raise ResearchError("選択した立場が保留・撤回されています。")
        return cid

    def basis(self, claim):
        return digest(self.context(only_claim=claim))

    def current_artifact(self, kind):
        cid = self.selected()
        basis = self.basis(cid)
        for entry in reversed(self.store.list_artifacts(kind)):
            if entry["claim_id"] == cid and entry["claim_revision"] == self.store.claim_row(cid)["revision"]:
                artifact = self.store.show_artifact(entry["id"])
                if artifact["input"].get("basis") == basis:
                    return artifact
        return None

    def latest_relevant_review(self):
        state = self.store.study_state()
        cid = state["selected_claim_id"]
        if not cid:
            return None
        for entry in reversed(self.store.list_artifacts("review")):
            if entry["claim_id"] == cid and entry["claim_revision"] == self.store.claim_row(cid)["revision"]:
                artifact = self.store.show_artifact(entry["id"])
                if artifact["input"].get("contract_version") != roles.PROMPT_VERSIONS["review"]:
                    continue
                settings = state["settings"]
                config = self.engine.config if self.injected_engine else {"model": settings["review_model"], "reasoning_effort": settings["review_effort"]}
                task = self.store.show_task(artifact["task_id"]) if artifact["task_id"] else None
                if task and all(task["config"].get(k) == config.get(k) for k in ("model", "reasoning_effort")):
                    verified = self.current_verification()
                    if verified and artifact["input"].get("verification", {}).get("id") == verified["id"]:
                        return artifact
        return None

    def data_for(self, kind, document=None, page=None, choices=None):
        state = self.store.study_state()
        if kind == "orientation":
            return {"question": state["question"], "audience": state["audience"], "venue": state["venue"],
                    "preferred_provider": state["settings"]["provider"], "query_limit": state["settings"]["query_limit"],
                    "available_providers": ["arxiv", "crossref", "openalex"],
                    "human_guidance": self.human_guidance(),
                    "existing_positions": self.candidates()}
        if kind == "reading":
            text = self.store.document_page(document, page)
            if not text.strip():
                raise ResearchError("指定ページに読解できる本文がありません。")
            doc = self.store.show_document(document)
            paper = self.store.show_paper(doc["paper_id"])
            return {"question": state["question"], "claim_id": state["anchor_claim_id"],
                    "document_id": document, "page": page, "start": 0, "page_text": text[:30000],
                    "omitted_characters": max(0, len(text) - 30000),
                    "source": {"title": paper["title"], "url": doc["source_url"], "sha256": doc["sha256"],
                               "source_version": doc["source_version"], "origin": doc["origin"], "extractor": doc["extractor"],
                               "total_units": len(doc["pages"]),
                               "caution": "指定箇所の部分読解。図表・数式・抽出順の誤りや付録の例示に注意。"}}
        if kind == "exploration":
            return {"anchor_claim_id": state["anchor_claim_id"], "context": self.context()}
        if kind == "comparison":
            context = self.context(for_comparison=True)
            if not context["claims"]:
                raise ResearchError("比較する立場がありません。先にstudy exploreを実行してください。")
            return {"context": context}
        if kind == "decision":
            return {"state": {key: state[key] for key in ("question", "status", "phase", "reason")},
                    "choices": choices, "scope": self.scope()}
        cid = self.selected()
        context = self.context(only_claim=cid)
        common = {"claim_id": cid, "basis": self.basis(cid), "context": context}
        if kind == "outline":
            return common
        if kind in ("draft", "revise_draft"):
            outline = self.current_artifact("outline")
            if not outline:
                raise ResearchError("現行状態に対応する論証を先に作成してください。")
            common["outline"] = {"id": outline["id"], "content": outline["content"]}
            if kind == "revise_draft":
                draft, review = self.current_artifact("draft"), self.latest_relevant_review()
                if not draft or not review:
                    raise ResearchError("草稿の修正には現行草稿の検証と査読が必要です。")
                common["previous_draft"] = {"id": draft["id"], "content": draft["content"]}
                common["review"] = {"id": review["id"], "content": review["content"]}
                common["verification"] = self.current_verification()["content"]
            return common
        if kind in ("review", "verify"):
            draft = self.current_artifact("draft")
            if not draft:
                raise ResearchError("現行状態に対応する草稿を先に作成してください。")
            if not self.injected_engine and draft["task_id"]:
                writer = self.store.show_task(draft["task_id"])
                if writer["config"].get("model") == state["settings"]["review_model"]:
                    raise ResearchError("現行草稿の執筆モデルと査読モデルが同じです。別の査読モデルを指定してください。")
            common["draft"] = {"id": draft["id"], "content": draft["content"]}
            common["contract_version"] = roles.PROMPT_VERSIONS[kind]
            if kind == "verify":
                spans = {}
                for eid, evidence in context["evidence"].items():
                    source = evidence.get("document_source")
                    if not source:
                        continue
                    stored = self.store.evidence_row(eid)
                    doc = self.store.document_row(source["document_id"])
                    start, end = verification.locate(self.store.document_page(source["document_id"], source["page"]), stored["quote"])
                    spans[eid] = {"document_id": source["document_id"], "page": source["page"], "start": start, "end": end,
                                  "quote": stored["quote"], "sha256": doc["sha256"], "source_version": doc["source_version"],
                                  "level": stored["level"], "human_status": stored["status"], "extractor": doc["extractor"],
                                  "linkage": "host-validated evidence_documents mapping and exact page substring",
                                  "source_url": doc["source_url"] if doc["source_url"].startswith(("https://", "http://")) else "local immutable document snapshot"}
                common["source_spans"] = spans
                # Verification needs actual manuscript and source spans, not repeated abstracts/quotes.
                common["context"] = {"question": context["question"], "claims": context["claims"],
                                     "scope": context["scope"], "human_guidance": context["human_guidance"],
                                     "evidence": {eid: {"claim_id": item["claim_id"], "level": item["level"], "status": item["status"]}
                                                  for eid, item in context["evidence"].items()}}
            else:
                verified = self.current_verification()
                if not verified:
                    raise ResearchError("現行草稿の事実主張検証を先に行ってください。")
                common["verification"] = {"id": verified["id"], "content": verified["content"]}
                common["prior_issues"] = self.store.list_issues()
            return common
        if kind == "redesign":
            review = self.latest_relevant_review()
            if not review:
                raise ResearchError("再設計の対象となる査読がありません。")
            common["context"] = self.context()
            common["review"] = {"id": review["id"], "content": review["content"]}
            common["issues"] = [issue for issue in self.store.list_issues()
                        if issue["review_id"] == review["id"] and issue["status"] not in ("addressed", "rejected", "not_applicable")]
            return common
        raise ResearchError("研究作業の種類が不正です。")

    def prepare(self, kind, **parameters):
        if kind in ("reading", "exploration"):
            self.store.ensure_anchor()
        try:
            self.store.db.execute("BEGIN")
            data = self.data_for(kind, **parameters)
            fingerprint = self.store.domain_fingerprint()
            self.store.db.commit()
        except Exception:
            self.store.db.rollback()
            raise
        config = self.engine_for(kind).config
        key = digest({"kind": kind, "data": data, "prompt": roles.PROMPTS[kind], "schema": SCHEMAS[kind],
                      "prompt_version": roles.PROMPT_VERSIONS[kind], "model": config.get("model"),
                      "reasoning_effort": config.get("reasoning_effort")})
        return data, key, fingerprint

    def perform(self, kind, force=False, **parameters):
        if kind not in SCHEMAS:
            raise ResearchError("作業が不正です。")
        data, key, fingerprint = self.prepare(kind, **parameters)
        if not force:
            row = self.store.db.execute("SELECT id FROM tasks WHERE kind=? AND semantic_key=? AND status='completed' ORDER BY id DESC LIMIT 1", (kind, key)).fetchone()
            if row:
                return self.store.show_task(label(row[0], "T"))
        engine = self.engine_for(kind)
        task = self.store.start_task(kind, key, data, roles.PROMPTS[kind], SCHEMAS[kind],
                                     dict(engine.config, prompt_version=roles.PROMPT_VERSIONS[kind]), fingerprint)
        task_id = task["id"]
        print(f"{task_id}: {kind}を実行しています…", file=sys.stderr, flush=True)
        raw = None
        execution = None
        try:
            raw, execution = engine.execute(
                kind, data, roles.PROMPTS[kind], SCHEMAS[kind],
                on_spawn=lambda pid, identity: self.store.task_process(task_id, pid, identity),
            )
            validate(raw, SCHEMAS[kind])
            if kind == "verify":
                raw = verification.normalize(data, raw)
            else:
                if kind == "review":
                    for item in verification.blocking(data["verification"]["content"]):
                        target_type = "fact" if "verdict" in item else "block"
                        target = item["id"] if target_type == "fact" else item["block_id"]
                        if not any(f["severity"] in ("major", "critical") and ((f["target_type"] == target_type and f["target_id"] == target) or (f["target_type"] == "block" and f["target_id"] == item["block_id"])) for f in raw["findings"]):
                            raw["findings"].append({"severity": "major", "category": "citation", "target_type": target_type, "target_id": target,
                                                   "explanation": "Host fact gate: " + item.get("verdict", item.get("status", "")) + ": " + item["reason"],
                                                   "evidence_ids": [], "suggested_action": "revise", "remaining_uncertainty": item.get("qualification") or item["reason"]})
                    if verification.blocking(data["verification"]["content"]) and raw["decision"] == "ready_for_human":
                        raw["decision"] = "revise"
                    validate(raw, SCHEMAS[kind])
                roles.validate_semantics(kind, data, raw)
            result = self.store.finish_task(task_id, "completed", result=raw, execution=execution,
                                            apply=getattr(self.store, HANDLERS[kind]))
            if result["status"] == "stale":
                raise EngineFailure(f"{task_id}: 入力が更新されたため適用しません。", status="stale")
            return result
        except KeyboardInterrupt:
            failure = EngineFailure("利用者が中断しました。", status="interrupted")
        except EngineFailure as exc:
            failure = exc
        except (ResearchError, OSError, sqlite3.Error) as exc:
            failure = EngineFailure(str(exc), raw_result=raw)
        if self.store.task_row(task_id)["status"] == "running":
            self.store.finish_task(task_id, failure.status if failure.status in ("interrupted", "timed_out") else "failed",
                                   result=failure.raw_result if failure.raw_result is not None else raw,
                                   execution=execution, error=str(failure))
        raise EngineFailure(f"{task_id}: {failure}", status=failure.status) from failure

    def orientation(self):
        state = self.store.study_state()
        for item in reversed(self.store.list_artifacts("orientation")):
            artifact = self.store.show_artifact(item["id"])
            data = artifact["input"]
            if artifact["task_id"] and self.store.show_task(artifact["task_id"])["config"].get("prompt_version") != roles.PROMPT_VERSIONS["orientation"]:
                continue
            if all(data.get(key) == state[key] for key in ("question", "audience", "venue")) \
                    and data.get("preferred_provider") == state["settings"]["provider"]:
                return artifact
        return None

    def exploration_done(self):
        state = self.store.study_state()
        for item in reversed(self.store.list_artifacts("decision")):
            artifact = self.store.show_artifact(item["id"])
            if "candidates" in artifact["content"] and artifact["input"].get("context", {}).get("question") == state["question"]:
                return True
        return False

    def read_pages(self, document):
        state = self.store.study_state()
        seen = set()
        for item in self.scope()["readings"]:
            if item["document_id"] == document and item["research_question"] == state["question"]:
                seen.add(item["page"])
        return seen

    def ranked_pages(self, document):
        orientation = self.orientation()
        terms = orientation["content"]["key_terms"] if orientation else []
        words = {word.lower() for term in terms for word in re.findall(r"[A-Za-z]{4,}", term)}
        document_id = identifier(document, "D")
        scored = []
        for row in self.store.db.execute("SELECT page,text FROM pages WHERE document_id=?", (document_id,)):
            if not row["text"].strip():
                continue
            text = row["text"].lower()
            score = sum(min(text.count(word), 8) for word in words)
            score += 8 if row["page"] == 1 else 0
            if re.search(r"\breferences\b|\bbibliography\b", text[:200]):
                score -= 20
            scored.append((score, row["page"]))
        return [page for _, page in sorted(scored, key=lambda item: (-item[0], item[1]))]

    def ranked_papers(self):
        orientation = self.orientation()
        terms = orientation["content"]["key_terms"] if orientation else []
        words = {word.lower() for term in terms for word in re.findall(r"[A-Za-z]{4,}", term)}
        scores = []
        targeted = set()
        review = self.latest_relevant_review()
        if review:
            targeted.update((query["provider"], query["query"]) for query in review["content"]["requested_searches"])
        redesign = self.store.latest_artifact("redesign")
        if redesign and redesign["input"]["context"]["question"] == self.store.study_state()["question"]:
            targeted.update((query["provider"], query["query"]) for query in redesign["content"]["requested_searches"])
        comparison = self.store.latest_artifact("comparison")
        if comparison and comparison["input"]["context"]["question"] == self.store.study_state()["question"]:
            targeted.update((query["provider"], query["query"]) for query in comparison["content"]["requested_searches"])
        for item in self.store.list_papers():
            raw = self.store.paper_row(item["id"])
            text = (raw["title"] + " " + raw["abstract"]).lower()
            score = sum(min(text.count(word), 5) for word in words)
            if raw["provider"] == "manual":
                score += 5
            discoveries = self.store.show_paper(item["id"])["discoveries"]
            if any(entry["query"].startswith("graph:") for entry in discoveries):
                score += 6
            if any((entry["provider"], entry["query"]) in targeted for entry in discoveries):
                score += 100
            scores.append((score, identifier(item["id"], "P"), item))
        ranked = [item for _, _, item in sorted(scores, key=lambda row: (-row[0], row[1]))]
        if targeted or any(d["query"].startswith("graph:") for item in ranked for d in self.store.show_paper(item["id"])["discoveries"]):
            return ranked
        # Avoid spending the entire acquisition budget on one adjacent field.
        # Represent distinct successful query paths before filling by lexical relevance.
        representative, used = [], set()
        for query in (orientation["content"]["search_queries"] if orientation else []):
            for item in ranked:
                if item["id"] in used:
                    continue
                discoveries = self.store.show_paper(item["id"])["discoveries"]
                if any(entry["query"] == query["query"] and entry["provider"] == query["provider"] for entry in discoveries):
                    representative.append(item)
                    used.add(item["id"])
                    break
        return representative + [item for item in ranked if item["id"] not in used]

    def pending_queries(self):
        orientation = self.orientation()
        if not orientation:
            return []
        limit = self.store.study_state()["settings"]["query_limit"]
        queries = list(orientation["content"]["search_queries"][:limit])
        review = self.latest_relevant_review()
        if review:
            queries.extend(review["content"]["requested_searches"])
        comparison = self.store.latest_artifact("comparison")
        if comparison and comparison["input"]["context"]["question"] == self.store.study_state()["question"]:
            queries.extend(comparison["content"]["requested_searches"])
        redesign = self.store.latest_artifact("redesign")
        if redesign and redesign["input"]["context"]["question"] == self.store.study_state()["question"]:
            queries.extend(redesign["content"]["requested_searches"])
        done = {(row["provider"], row["query"]) for row in self.store.list_searches()}
        result, seen = [], set()
        for query in queries:
            signature = (query["provider"], query["query"])
            if signature not in done and signature not in seen:
                result.append(query)
                seen.add(signature)
            failures = [row for row in self.store.list_searches()
                        if (row["provider"], row["query"]) == signature and row["status"] == "failed"]
            alternative = "crossref" if query["provider"] == "arxiv" else "arxiv"
            fallback = (alternative, query["query"])
            if failures and fallback not in done and fallback not in seen:
                result.append(dict(query, provider=alternative, purpose="検索先の障害・制限に対する別の探索範囲"))
                seen.add(fallback)
        return result

    def unlinked_evidence(self, claim):
        state = self.store.study_state()
        anchor = state["anchor_claim_id"]
        if not anchor:
            return []
        current = self.store.claim_row(claim)
        owned = set()
        for row in self.store.db.execute("SELECT id FROM evidence WHERE claim_id=? AND claim_revision=?", (current["id"], current["revision"])):
            evidence = self.store.evidence_row(label(row[0], "E"))
            owned.add(digest([evidence["quote"], evidence["source"], evidence["locator"]]))
        source_claim = self.store.claim_row(anchor)
        result = []
        for row in self.store.db.execute("SELECT id FROM evidence WHERE claim_id=? AND claim_revision=? AND status NOT IN ('disputed','inaccessible')",
                                         (source_claim["id"], source_claim["revision"])):
            eid = label(row[0], "E")
            source = self.store.evidence_row(eid)
            signature = digest([source["quote"], source["source"], source["locator"]])
            if signature not in owned and self.store.evidence_provenance(eid):
                result.append(eid)
                owned.add(signature)
        return result

    def choices(self):
        state = self.store.study_state()
        result = []
        def add(kind, reason, **parameters):
            result.append({"choice_id": f"choice_{len(result)+1:02d}", "kind": kind,
                           "parameters": parameters, "reason": reason})
        if not self.orientation():
            add("orientation", "問いと隣接領域・競合する立場の探索方針を整理する")
            return result
        pending = self.pending_queries()
        for query in pending:
            add("search", query["purpose"], query=query["query"], provider=query["provider"])
        settings = state["settings"]
        attempted_papers = {label(row[0], "P") for row in self.store.db.execute("SELECT DISTINCT paper_id FROM fetches")}
        papers = self.ranked_papers()[:settings["paper_limit"]]
        papers += [p for p in self.store.list_papers() if p["id"] in attempted_papers and p["id"] not in {r["id"] for r in papers}]
        for item in papers:
            paper = self.store.show_paper(item["id"])
            documents = [doc for doc in paper["documents"] if doc["status"] == "extracted"]
            # Prefer the newest extraction of the same bytes; keep prior snapshots for citations.
            snapshots = {}
            for doc in documents:
                snapshots[(doc["origin"], doc["sha256"])] = doc
            documents = list(snapshots.values())
            attempts = paper["fetches"]
            if not documents:
                if not attempts and len(attempted_papers) < settings["paper_limit"]:
                    add("fetch", "取得できる本文を確認し、未取得の資料を読んだことにしない", paper=item["id"], abstract=False)
                elif paper["abstract"] and not any(doc["origin"] == "abstract" for doc in paper["documents"]):
                    add("fetch", "本文の取得失敗を残したまま、抄録だけを別の範囲として確認", paper=item["id"], abstract=True)
            for doc in documents:
                seen = self.read_pages(doc["id"])
                wanted = self.ranked_pages(doc["id"])[:settings["pages_per_document"]]
                for page in wanted:
                    if page not in seen:
                        add("reading", "取得資料の関連箇所を読み、原文・解釈・未確認事項を分けて記録", document=doc["id"], page=page)
        if not pending:
            attempted = {(row["provider"], row["query"]) for row in self.store.list_searches()}
            graph_new = self.store.db.execute("SELECT COUNT(DISTINCT paper_id) FROM search_results WHERE search_id IN (SELECT id FROM searches WHERE query LIKE 'graph:%') AND paper_id NOT IN (SELECT paper_id FROM search_results WHERE search_id IN (SELECT id FROM searches WHERE query NOT LIKE 'graph:%'))").fetchone()[0]
            seeds = [p for p in self.store.list_papers() if any(not d["query"].startswith("graph:") for d in self.store.show_paper(p["id"])["discoveries"])]
            for seed in seeds[:settings["citation_seeds"]]:
                for direction in ("references", "citations"):
                    signature = ("openalex", "graph:" + seed["id"] + ":" + direction)
                    if signature not in attempted and graph_new < settings["citation_budget"]:
                        add("related", "重要な先行研究の引用・被引用関係を限定的に追跡する", paper=seed["id"], direction=direction,
                            limit=settings["citation_limit"], remaining=settings["citation_budget"] - graph_new)
        selected = state["selected_claim_id"]
        if selected:
            claim = self.store.claim_row(selected)
            if claim["status"] == "held":
                return [{"choice_id": "choice_01", "kind": "hold", "parameters": {}, "reason": "人間が選択した立場を保留している"}]
            if claim["status"] == "withdrawn":
                with self.store.db:
                    self.store._set_study("選択された立場が撤回されたため代替案へ戻る", selected_claim_id=None, selected_revision=None, phase="comparison")
                selected = None
            elif claim["revision"] != state["selected_revision"]:
                with self.store.db:
                    self.store._set_study("人間による改訂を引き継ぎ、論証と根拠を再確認", selected_revision=claim["revision"], phase="argument")
                    self.store.event("study.revision_followed", "人間による改訂版を引き継ぐ", {"claim_id": selected, "revision": claim["revision"]})
        review = self.latest_relevant_review() if selected else None
        if selected and review:
            current_draft = self.current_artifact("draft")
            if current_draft and current_draft["id"] != review["input"]["draft"]["id"]:
                # Old criticism remains in the audit trail and is passed to the next reviewer.
                review = None
        if selected and review:
            draft_id = review["input"]["draft"]["id"]
            blocking = [issue for issue in self.store.list_issues(draft_id)
                        if issue["severity"] in ("critical", "major") and issue["status"] not in ("addressed", "rejected", "not_applicable")]
            if blocking or review["content"]["decision"] in ("revise", "hold"):
                unlinked = self.unlinked_evidence(selected)
                if unlinked:
                    add("ground", "新たな原文を主張へ暫定的に結び付け、査読に基づく再設計へ渡す", claim=selected)
                if blocking and all(issue["content"]["category"] == "citation" for issue in blocking) and review["content"]["decision"] == "revise" and self.current_artifact("outline"):
                    add("revise_draft", "引用・事実の問題を草稿で修正し、中心主張は保持して再検証する")
                else:
                    add("redesign", "重大な査読指摘へ応答し、再調査・主張の限定・撤回を選ぶ")
                return result
        if result:
            return result
        context = self.context()
        if not context["evidence"]:
            add("consult", "取得・読解できた根拠がありません。文献の追加、検索語の変更、取得不能資料の確認が必要です。")
            return result
        candidates = self.candidates()
        if not self.exploration_done() and len(candidates) < 2:
            add("exploration", "根拠と反論から複数の立場を分岐させ、元の案へ固執しない")
            return result
        if not selected:
            if not candidates:
                add("withdraw", "検討した立場がすべて保留・撤回され、有効な候補が残っていない")
            else:
                data, key, _ = self.prepare("comparison")
                previous = self.store.latest_artifact("comparison", key)
                if previous and previous["content"]["decision"] != "proceed":
                    add("hold", previous["content"]["reason"])
                else:
                    add("comparison", "新規性の暫定評価、根拠の強さ、最も強い反論を比較して採否を決める")
            return result
        if self.unlinked_evidence(selected):
            add("ground", "未評価の取得資料を選択立場へ結び付け、反対根拠も含めた論証の入力にする", claim=selected)
        elif not self.current_artifact("outline"):
            add("outline", "現行の主張・根拠・限定条件から論証を設計する")
        elif not self.current_artifact("draft"):
            add("draft", "根拠と主張に結び付いた英語の草稿を作る")
        elif not self.current_verification():
            add("verify", "個々の事実主張と原資料の範囲を対応づけ、支持を暫定検証する")
        else:
            draft = self.current_artifact("draft")
            if not review or review["input"]["draft"]["id"] != draft["id"] or review["input"].get("basis") != self.basis(selected):
                add("review", "執筆と別の文脈で引用・論理・新規性・反例を批判する")
            elif review["content"]["decision"] == "ready_for_human":
                add("finish", "内部査読後の草稿と未解決事項を人間の判断へ渡す")
            else:
                add("redesign", "査読指摘を前工程へ差し戻す")
        return result

    def ground(self, claim):
        if self.store.study_state()["selected_claim_id"] != claim:
            raise ResearchError("採用立場が変わったため、根拠の結び付けを中止します。")
        sources = self.unlinked_evidence(claim)
        current = self.store.claim_row(claim)
        with self.store.db:
            copied = [self.store._copy_evidence(source, current["id"], current["revision"]) for source in sources]
            if current["status"] not in ("held", "withdrawn"):
                self.store.db.execute("UPDATE claims SET status='needs_review' WHERE id=?", (current["id"],))
            self.store.event("study.evidence_linked", "反対・支持関係を未評価のまま取得資料を結び付ける",
                             {"claim_id": claim, "evidence_ids": copied, "source_ids": sources}, claim_id=current["id"])
        return {"claim_id": claim, "evidence_ids": copied}

    def execute_choice(self, choice):
        kind, parameters = choice["kind"], choice["parameters"]
        self.store.event("study.action_chosen", choice["reason"], {"kind": kind, "parameters": parameters})
        self.store.db.commit()
        if kind == "search":
            return search_papers(self.store, parameters["query"], parameters["provider"],
                                 self.store.study_state()["settings"]["paper_limit"])
        if kind == "related":
            from .discovery import related_papers
            return related_papers(self.store, parameters["paper"], parameters["direction"], parameters["limit"], parameters["remaining"])
        if kind == "fetch":
            return fetch_paper(self.store, parameters["paper"], abstract=parameters["abstract"])
        if kind == "ground":
            return self.ground(parameters["claim"])
        if kind in ("hold", "consult", "withdraw", "finish"):
            status = {"hold": "held", "consult": "needs_human", "withdraw": "withdrawn", "finish": "ready_for_human"}[kind]
            return self.store.control_study(status, choice["reason"])
        return self.perform(kind, **parameters)

    def cancel_task(self, task, reason):
        row = self.store.task_row(task)
        if row["status"] != "running":
            raise ResearchError("作業はすでに終了しています。")
        if process_alive(row["process_id"], row["process_identity"]):
            try:
                os.killpg(row["process_id"], signal.SIGKILL)
            except ProcessLookupError:
                pass
        return self.store.finish_task(task, "interrupted", error="人間による停止: " + reason)

    def pause(self, reason):
        self.store.control_study("held", reason, actor="human")
        for task in self.store.list_tasks():
            if task["status"] == "running":
                self.cancel_task(task["id"], reason)
        return self.summary()

    def recover(self):
        for task in self.store.list_tasks():
            if task["status"] != "running":
                continue
            row = self.store.task_row(task["id"])
            if process_alive(row["process_id"], row["process_identity"]):
                raise ResearchError(f"{task['id']}のCodexプロセスが実行中です。task cancelで停止してから再開してください。")
            self.store.finish_task(task["id"], "interrupted", error="実行プロセスの停止を確認し、中断記録を回復")
        with self.store.db:
            searches = self.store.db.execute("SELECT id,owner_pid,owner_identity FROM searches WHERE status='running'").fetchall()
            for row in searches:
                if row["owner_pid"] is None or process_alive(row["owner_pid"], row["owner_identity"]):
                    continue
                self.store.db.execute("UPDATE searches SET status='interrupted',error=?,finished_at=? WHERE id=?",
                                      ("制御プロセス停止後の検索記録を回復", now(), row[0]))
                self.store.event("search.recovered", "中断した検索の記録を回復", {"search_id": label(row[0], "S")})
            fetches = self.store.db.execute("SELECT id,owner_pid,owner_identity FROM fetches WHERE status='running'").fetchall()
            for row in fetches:
                if row["owner_pid"] is None or process_alive(row["owner_pid"], row["owner_identity"]):
                    continue
                self.store.db.execute("UPDATE fetches SET status='interrupted',error=?,finished_at=? WHERE id=?",
                                      ("制御プロセス停止後の取得記録を回復", now(), row[0]))
                self.store.event("fetch.recovered", "中断した取得の記録を回復", {"fetch_id": label(row[0], "F")})

    def summary(self):
        state = self.store.study_state()
        artifact = self.store.latest_artifact("draft")
        current_draft = None
        if state["selected_claim_id"]:
            try:
                current_draft = self.current_artifact("draft")
            except ResearchError:
                pass
        verified = self.current_verification() if current_draft else None
        from collections import Counter
        return {
            "current_verification": verified["id"] if verified else None,
            "fact_verdicts": dict(Counter(f["verdict"] for f in verified["content"]["facts"])) if verified else None,
            "reviewer_independence": "different explicitly requested model; same provider, actual model may be unconfirmed",
            "state": state, "candidates": list(self.candidates().values()),
            "tasks": self.store.list_tasks(), "artifacts": self.store.list_artifacts(),
            "issues": self.store.list_issues(), "scope": self.scope(),
            "latest_draft": artifact["id"] if artifact else None,
            "current_draft": current_draft["id"] if current_draft else None,
            "notice": "草稿の完成、内部査読の完了、学術的な裏付け、人間の最終判断は別の状態です。",
        }

    def run(self, steps=20, resume_reason=None, max_seconds=None):
        if max_seconds is not None and (not isinstance(max_seconds, (int, float)) or isinstance(max_seconds, bool) or not 0 < max_seconds < float("inf")):
            raise ResearchError("実行時間上限は有限の正数で指定してください。")
        self.deadline = time.monotonic() + max_seconds if max_seconds is not None else None
        if type(steps) is not int or not 1 <= steps <= 1000:
            raise ResearchError("作業上限は1〜1000で指定してください。")
        with study_lock(self.store), termination_as_interrupt():
            self.recover()
            state = self.store.study_state()
            self.engine_for("orientation")
            if state["status"] in ("ready_for_human", "finalized") and not self.current_verification():
                self.store.control_study("needs_human", "旧版の確定判断は履歴に保持。事実検証と別モデル査読を再実行してください。")
                state = self.store.study_state()
            if state["status"] != "active":
                if not resume_reason:
                    return self.summary()
                self.store.control_study("active", resume_reason, actor="human")
                selected = self.store.study_state()["selected_claim_id"]
                if selected and self.store.claim_row(selected)["status"] == "held":
                    with self.store.db:
                        self.store.db.execute("UPDATE claims SET status='needs_review' WHERE id=?", (identifier(selected, "C"),))
                        self.store.event("claim.reconsidered", resume_reason, {"claim_id": selected, "actor": "human"})
            elif resume_reason:
                self.store.control_study("active", resume_reason, actor="human")
            self.store.ensure_anchor()
            completed = 0
            unchanged = 0
            try:
                for step in range(steps):
                    if self.deadline is not None and time.monotonic() >= self.deadline:
                        self.store.control_study("budget_exhausted", "今回の実行時間上限に達しました。")
                        break
                    if self.store.study_state()["status"] != "active":
                        break
                    choices = self.choices()
                    if not choices:
                        self.store.control_study("needs_human", "次の有効な作業を特定できません。")
                        break
                    if len(choices) == 1:
                        choice = choices[0]
                    elif all(item["kind"] in ("search", "related", "fetch", "reading") for item in choices):
                        # The orientation/search plan and host ranking already determine these operations.
                        # Execute discovery before acquisition; don't spend an extra model call per source.
                        order = {"search": 0, "related": 1, "fetch": 2, "reading": 3}
                        choice = min(choices, key=lambda item: order[item["kind"]])
                    else:
                        decision = self.perform("decision", choices=choices)
                        choice = next(item for item in choices if item["choice_id"] == decision["result"]["choice_id"])
                        choice = dict(choice, reason=decision["result"]["reason"])
                    print(f"step {step+1}/{steps}: {choice['kind']} — {choice['reason']}", file=sys.stderr, flush=True)
                    before = self.store.domain_fingerprint()
                    try:
                        self.execute_choice(choice)
                    except ResearchError as exc:
                        if choice["kind"] not in ("search", "fetch", "related"):
                            raise
                        print(f"{choice['kind']}の失敗を保存: {exc}", file=sys.stderr, flush=True)
                    after = self.store.domain_fingerprint()
                    completed += 1
                    unchanged = unchanged + 1 if before == after else 0
                    if unchanged >= 2:
                        self.store.control_study("needs_human", "同じ研究状態で有効な進展が得られません。方針変更が必要です。")
                        break
                if self.store.study_state()["status"] == "active":
                    self.store.control_study("budget_exhausted", "今回の作業上限に達しました。研究が完成したとは扱いません。")
            except (KeyboardInterrupt, EngineFailure) as exc:
                state = self.store.study_state()
                if state["status"] == "active":
                    status = "interrupted" if isinstance(exc, KeyboardInterrupt) or getattr(exc, "status", None) == "interrupted" else "needs_human"
                    if self.deadline is not None and time.monotonic() >= self.deadline and not isinstance(exc, KeyboardInterrupt):
                        status = "budget_exhausted"
                    self.store.control_study(status, str(exc) or "人間が研究を中断")
            except (ResearchError, OSError, sqlite3.Error) as exc:
                if self.store.study_state()["status"] == "active":
                    self.store.control_study("needs_human", str(exc))
            result = self.summary()
            result["steps_executed"] = completed
            return result

    def finalize(self, reason):
        draft = self.current_artifact("draft")
        if not draft:
            raise ResearchError("現行の主張・根拠に対応する草稿がありません。")
        verified = self.current_verification()
        if not verified or verification.blocking(verified["content"]):
            raise ResearchError("現行草稿の検証に未支持の事実主張または未完了の抽出が残っています。")
        review = self.latest_relevant_review()
        if not review or review["input"]["draft"]["id"] != draft["id"]:
            raise ResearchError("現行草稿の内部査読がありません。")
        if review["content"]["decision"] != "ready_for_human":
            raise ResearchError("現行査読が追加調査・再設計・保留を求めています。対応して再査読してください。")
        if any(issue["severity"] in ("critical", "major") and issue["status"] not in ("addressed", "rejected", "not_applicable")
               for issue in self.store.list_issues()):
            raise ResearchError("重大な指摘への人間の応答が未完了です。")
        cited = {eid for block in roles.blocks(draft["content"]) for eid in block["evidence_ids"]}
        if not cited or any(self.store.evidence_row(eid)["status"] != "checked"
                            or not self.store.evidence_provenance(eid) for eid in cited):
            raise ResearchError("引用する根拠を原資料と照合して手動確認し、必要な再査読を行ってください。")
        self.store.control_study("finalized", reason, actor="human")
        self.store.event("study.finalized", reason, {"draft_id": draft["id"], "review_id": review["id"], "actor": "human",
                         "notice": "人間の研究判断の記録。学会採択を保証しない。"})
        self.store.db.commit()
        return self.summary()

    def import_draft(self, path, reason):
        from pathlib import Path
        from .common import required
        reason = required(reason, "編集理由")
        with Path(path).expanduser().open("rb") as handle:
            payload = handle.read(500001)
        if len(payload) > 500000:
            raise ResearchError("草稿JSONが大きすぎます。")
        try:
            content = json.loads(payload)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ResearchError("草稿をUTF-8のJSONとして読めません。") from exc
        if isinstance(content, dict) and isinstance(content.get("abstract_blocks"), list) and isinstance(content.get("sections"), list):
            candidates = list(content["abstract_blocks"])
            for section in content["sections"]:
                if isinstance(section, dict) and isinstance(section.get("blocks"), list):
                    candidates.extend(section["blocks"])
            for block in candidates:
                if isinstance(block, dict):
                    block.pop("id", None)
        validate(content, roles.DRAFT)
        try:
            self.store.db.execute("BEGIN IMMEDIATE")
            data = self.data_for("outline")
            roles.validate_semantics("draft", data, content)
            self.store.apply_draft(None, "human:" + digest(content), data, content)
            artifact = self.store.latest_artifact("draft")
            self.store.event("draft.imported", reason, {"draft_id": artifact["id"], "actor": "human",
                             "source_file_sha256": hashlib.sha256(payload).hexdigest()})
            self.store.db.commit()
        except Exception:
            self.store.db.rollback()
            raise
        return self.store.show_artifact(artifact["id"])
