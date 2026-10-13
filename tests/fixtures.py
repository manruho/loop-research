"""Explicitly synthetic engine/source fixtures. Never used by the product CLI."""

from loop_research.engine import EngineFailure
from loop_research.literature import fetch_paper


class FixtureEngine:
    config = {"adapter": "synthetic-test-fixture", "model": "fixture", "auth": "not-real"}

    def __init__(self, repair=False, hold=False, withdraw=False, fail=None):
        self.repair, self.hold, self.withdraw, self.fail = repair, hold, withdraw, fail
        self.calls = []
        self.reviews = 0

    def execute(self, kind, data, prompt, schema, on_spawn=None):
        self.calls.append(kind)
        if self.fail == kind:
            raise EngineFailure("synthetic failure", status="timed_out")
        notes = ["Synthetic evidence only; human verification remains necessary."]
        context = data.get("context", {})
        eids = list(context.get("evidence", {}))
        cids = list(context.get("claims", {}))
        if kind == "orientation":
            result = {"interpretation": "架空の評価負担の問い", "domains": ["HCI"],
                      "search_queries": [{"query": "synthetic workload", "provider": "arxiv", "purpose": "仮想資料の探索"}],
                      "key_terms": ["checking workload"], "recommendation": "proceed",
                      "reason": "架空の確認用研究を進める", "uncertainties": notes}
        elif kind == "reading":
            result = {"relevance": "relevant", "findings": [
                {"kind": "observation", "statement": "仮想資料は確認時間の観測を述べる",
                 "excerpt": data["page_text"], "limitations": notes}],
                "strongest_counterargument": "観測から一般的な義務は導けない", "open_questions": notes}
        elif kind == "exploration":
            candidates = []
            for text, claim_kind in (("Report checking workload in consequential AI evaluations.", "normative"),
                                     ("Study detectability before choosing a workload metric.", "proposal")):
                candidates.append({"text": text, "kind": claim_kind, "scope": "Synthetic consequential evaluations",
                                   "audience": "HCI researchers", "assumptions": notes,
                                   "supporting_evidence_ids": eids, "opposing_evidence_ids": [],
                                   "counterarguments": ["A time measure alone misses detectability."],
                                   "novelty": "仮想資料との限定的な差分。新規性は未確認。",
                                   "overlaps": [], "open_questions": notes})
            result = {"candidates": candidates, "reason": "異なる立場を比較する", "limitations": notes}
        elif kind == "comparison":
            decision = "withdraw" if self.withdraw else "hold" if self.hold else "proceed"
            result = {"decision": decision, "selected_claim_id": cids[0] if decision == "proceed" else "",
                      "reason": "架空の比較理由。学術的採用ではない。",
                      "assessments": [{"claim_id": cid, "novelty": "未確認の限定的差分",
                                       "evidence_strength": "仮想の本文抜粋のみ",
                                       "strongest_objection": "射程が広すぎる", "response": "射程を限定する",
                                       "unresolved": notes} for cid in cids],
                      "requested_searches": [], "limitations": notes}
        elif kind == "outline":
            result = {"title": "A synthetic position on checking workload",
                      "central_claim_id": data["claim_id"], "contribution": "A limited normative proposal",
                      "sections": [{"heading": heading, "purpose": "Explain a limited position",
                                    "premises": [{"statement": "Evaluation should disclose checking workload",
                                                  "kind": "normative", "evidence_ids": eids, "assumptions": notes}],
                                    "counterarguments": [{"objection": "Time alone is insufficient",
                                                          "response": "Use it alongside detectability", "evidence_ids": eids}],
                                    "limitations": notes} for heading in ("Introduction", "Argument", "Limits")],
                      "open_questions": notes}
        elif kind == "draft":
            def block(fact=False):
                return {"text": "The supplied synthetic excerpt reports checking time." if fact else context["claims"][data["claim_id"]]["text"],
                        "kind": "fact" if fact else "position", "evidence_ids": eids,
                        "claim_ids": [data["claim_id"]],
                        "qualification": "Only a synthetic excerpt has been inspected; no real empirical study is established."}
            result = {"title": "A synthetic position on checking workload", "abstract_blocks": [block()],
                      "sections": [{"role": role, "heading": role.replace("_", " ").title(), "blocks": [block(role == "related_work")]}
                                   for role in ("introduction", "related_work", "position", "counterarguments",
                                                "implications", "limitations", "conclusion")],
                      "uncertainties": notes}
        elif kind == "verify":
            from loop_research.roles import blocks
            facts, coverage = [], []
            for block in blocks(data["draft"]["content"]):
                factual = block["kind"] == "fact"
                coverage.append({"block_id": block["id"], "status": "complete" if factual else "no_facts", "reason": "Explicit synthetic fixture coverage"})
                if factual:
                    eid = next(iter(data["source_spans"]))
                    facts.append({"id": "F001", "block_id": block["id"], "field": "text", "text": block["text"], "occurrence": 1,
                                  "sources": [{"evidence_id": eid, "quote": data["source_spans"][eid]["quote"], "occurrence": 1}],
                                  "verdict": "supported", "reason": "Synthetic passage only", "qualification": "Synthetic evidence is not a real scientific result."})
            result = {"facts": facts, "coverage": coverage, "limitations": notes}
        elif kind == "review":
            self.reviews += 1
            findings = []
            decision = "ready_for_human"
            if self.repair and self.reviews == 1:
                decision = "revise"
                findings = [{"severity": "major", "category": "scope", "target_type": "claim",
                             "target_id": data["claim_id"], "explanation": "The position needs narrower scope.",
                             "evidence_ids": eids, "suggested_action": "redesign",
                             "remaining_uncertainty": "The adequacy of the new scope remains unverified."}]
            result = {"decision": decision, "synthesis": "Synthetic internal review only.", "findings": findings,
                      "requested_searches": [], "unresolved": notes,
                      "prior_issue_assessments": [{"issue_id": issue["id"], "status": "resolved",
                                                  "reason": "The thesis has substantively narrowed.",
                                                  "remaining_uncertainty": "External validity is unverified."}
                                                 for issue in data["prior_issues"]
                                                 if issue["severity"] in ("critical", "major")
                                                 and issue["status"] not in ("addressed", "rejected", "not_applicable")]}
        elif kind == "redesign":
            result = {"decision": "revise", "text": "For consequential evaluations, report workload alongside detectability.",
                      "kind": "normative", "scope": "Consequential evaluations only", "reason": "射程を限定し反例へ応答",
                      "parent_claim_ids": [data["claim_id"]], "evidence_ids": eids, "assumptions": notes,
                      "counterarguments": ["Detectability remains necessary."], "novelty": "限定的な差分は未確認",
                      "open_questions": notes, "requested_searches": [],
                      "issue_responses": [{"issue_id": issue["id"], "response": "Normative scope has narrowed.",
                                          "remaining_uncertainty": "Must be independently re-reviewed."}
                                         for issue in data["issues"]]}
        elif kind == "decision":
            result = {"choice_id": data["choices"][0]["choice_id"], "reason": "次の未確認事項を減らす", "uncertainty": "仮想例の限界"}
        else:
            raise AssertionError(kind)
        return result, {"synthetic_fixture": True}


def fake_search(store, query, provider, limit):
    sid = store.start_search(provider, query, limit, "https://fixture.invalid/search")
    paper = {"canonical_key": "synthetic:workload", "provider": "synthetic-fixture",
             "external_id": "not-a-real-paper", "title": "Synthetic checking workload",
             "authors": ["Synthetic fixture"], "url": "https://fixture.invalid/paper",
             "source_version": "fixture-v1", "abstract": "A synthetic source, not a real study.",
             "metadata": {"synthetic": True}}
    return store.finish_search(sid, [paper], response={"synthetic": True})


def fake_fetch(store, paper, abstract=False):
    from pathlib import Path
    path = store.path.parent / "synthetic-source.txt"
    path.write_text("In this synthetic fixture, participants checked an answer for two minutes.", encoding="utf-8")
    return fetch_paper(store, paper, file=str(path))
