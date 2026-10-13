import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from loop_research.common import ResearchError
from loop_research.store import ResearchStore
from loop_research.workflow import ResearchWorkflow
from loop_research.discovery import parse_work, search_openalex, related_papers
from loop_research import roles, verification
from loop_research.evaluation import anonymize, import_scores, save
from tests.fixtures import FixtureEngine, fake_search, fake_fetch


def work(wid, doi=None, refs=None):
    return {"id": "https://openalex.org/" + wid, "title": "Synthetic " + wid, "doi": "https://doi.org/" + doi if doi else None,
            "referenced_works": refs or [], "abstract_inverted_index": {"Synthetic": [0], "text": [1]},
            "best_oa_location": {"landing_page_url": "https://fixture.invalid/" + wid}}


class QualityAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ResearchStore(self.root / "study.sqlite3", initialize=True)
        self.store.init("Original question")
        self.store.configure_study("Offline", settings={"citation_seeds": 0})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def full(self, engine=None):
        workflow = ResearchWorkflow(self.store, engine or FixtureEngine())
        with patch("loop_research.workflow.search_papers", fake_search), patch("loop_research.workflow.fetch_paper", fake_fetch), contextlib.redirect_stderr(io.StringIO()):
            result = workflow.run(50)
        self.assertEqual(result["state"]["status"], "ready_for_human", result["state"]["reason"])
        return workflow

    def test_manual_revision_retains_question_but_not_old_assessments(self):
        self.store.configure_study("New topic", question="Changed question")
        claim = self.store.add_claim("Position on changed question")
        profile = self.store.profile(claim["id"])
        profile["supporting_evidence_ids"] = ["E999"]
        with self.store.db:
            self.store.db.execute("UPDATE profiles SET content_json=? WHERE claim_id=1", (json.dumps(profile),))
        revised = self.store.revise_claim(claim["id"], "Revised position", "Human revision")
        self.assertEqual(revised["profile"]["_question"], "Changed question")
        self.assertNotIn("supporting_evidence_ids", revised["profile"])
        self.assertIn(claim["id"], ResearchWorkflow(self.store, FixtureEngine()).candidates())
        self.assertEqual(self.store.profile(claim["id"], 1)["supporting_evidence_ids"], ["E999"])

    def test_selected_revision_invalidates_ready_and_finalized(self):
        claim = self.store.add_claim("A position")
        self.store.select_claim(claim["id"], "Human selected")
        for status in ("ready_for_human", "finalized"):
            self.store.control_study(status, "Previous judgment")
            before = self.store.claim_row(claim["id"])
            self.store.revise_claim(claim["id"], before["text"] + " narrower", "New scope")
            state = self.store.study_state()
            self.assertEqual(state["status"], "active")
            self.assertEqual(state["selected_revision"], before["revision"] + 1)
        self.assertTrue(any(e["action"] == "study.revision_followed" for e in self.store.history()))

    def test_manual_revision_preserves_explicit_pause(self):
        claim = self.store.add_claim("A position")
        self.store.select_claim(claim["id"], "Selected")
        self.store.control_study("held", "Do not run")
        self.store.revise_claim(claim["id"], "A narrower position", "Revise while paused")
        self.assertEqual(self.store.study_state()["status"], "held")

    def test_openalex_search_provenance_and_exact_alias(self):
        with patch("loop_research.discovery.request", return_value={"results": [work("W1", "10.1/test")], "meta": {"count": 1}}):
            result = search_openalex(self.store, "Synthetic", 5)
            search_openalex(self.store, "Competing synthetic", 5)
        self.assertEqual(len(self.store.list_papers()), 1)
        self.assertEqual(len(self.store.show_paper("P001")["discoveries"]), 2)
        self.assertEqual(result["response"]["meta"]["count"], 1)
        self.assertEqual(self.store.paper_row("P001")["abstract"], "Synthetic text")

    def test_reference_graph_cycle_budget_and_direction(self):
        self.store.add_paper(parse_work(work("W1", refs=["W2", "W3"])))
        self.store.add_paper(parse_work(work("W2")))
        seed = work("W1", refs=["W2", "W3"])
        with patch("loop_research.discovery.request", side_effect=[seed, {"results": [work("W2"), work("W3")]}, seed, {"results": [work("W1"), work("W2")], "meta": {"count": 30}}]):
            related_papers(self.store, "P001", "both", limit=2, remaining=0)
        self.assertEqual(len(self.store.list_papers()), 2)
        edges = self.store.citation_graph()
        self.assertEqual([(e["citing_id"], e["cited_id"]) for e in edges], [("P001", "P002"), ("P002", "P001")])
        self.assertEqual(self.store.show_search("S001")["response"]["_coverage"]["budget_omitted"], 1)
        self.assertTrue(self.store.show_search("S002")["response"]["_coverage"]["truncated"])

    def test_failed_graph_is_recorded_and_not_retried_by_loop(self):
        self.store.add_paper(parse_work(work("W1")))
        with patch("loop_research.discovery.request", side_effect=ResearchError("Unavailable")):
            with self.assertRaises(ResearchError):
                related_papers(self.store, "P001", "references")
        self.assertEqual(self.store.show_search("S001")["status"], "failed")
        self.assertEqual(self.store.citation_graph(), [])

    def test_verification_snapshots_and_exact_page_offsets(self):
        workflow = self.full()
        verified = workflow.current_verification()
        facts = self.store.verification_rows(verified["id"])
        self.assertEqual(len(facts), 1)
        source = facts[0]["sources"][0]
        text = self.store.document_page("D001", source["page"])
        self.assertEqual(text[source["start"]:source["end"]], source["quote"])
        self.assertEqual(facts[0]["verdict"], "supported")
        self.assertTrue(all(e["status"] == "unchecked" for e in self.store.show_claim(self.store.study_state()["selected_claim_id"])["evidence"]))

    def test_offsets_omissions_and_source_mismatch_are_rejected(self):
        workflow = self.full()
        verified = workflow.current_verification()
        task = self.store.show_task(verified["task_id"])
        data = task["input"]["data"]
        # Convert normalized storage result back to the model contract.
        raw, _ = FixtureEngine().execute("verify", data, "", {})
        for corrupt in ("quote", "fact", "coverage", "source", "occurrence"):
            with self.subTest(corrupt=corrupt):
                result = copy.deepcopy(raw)
                if corrupt == "quote": result["facts"][0]["sources"][0]["quote"] = "Invented"
                if corrupt == "fact": result["facts"][0]["text"] = "Not in manuscript"
                if corrupt == "coverage": result["coverage"].pop()
                if corrupt == "source": result["facts"][0]["sources"][0]["evidence_id"] = "E999"
                if corrupt == "occurrence": result["facts"][0]["occurrence"] = 900
                with self.assertRaises(ResearchError): verification.normalize(data, result)

    def test_unsupported_fact_cannot_be_hidden_by_optimistic_review(self):
        class Partial(FixtureEngine):
            def execute(engine, kind, data, prompt, schema, on_spawn=None):
                result, execution = super(Partial, engine).execute(kind, data, prompt, schema, on_spawn)
                if kind == "verify":
                    result["facts"][0].update(verdict="partial", qualification="Population mismatch")
                return result, execution
        workflow = ResearchWorkflow(self.store, Partial())
        with patch("loop_research.workflow.search_papers", fake_search), patch("loop_research.workflow.fetch_paper", fake_fetch), contextlib.redirect_stderr(io.StringIO()):
            workflow.run(10)
            workflow.perform("review")
        self.assertNotEqual(self.store.study_state()["status"], "ready_for_human")
        self.assertTrue(any(i["severity"] == "major" for i in self.store.list_issues()))
        with self.assertRaises(ResearchError): workflow.finalize("No override")

    def test_explicit_models_required_and_review_routing(self):
        workflow = ResearchWorkflow(self.store)
        with self.assertRaises(ResearchError): workflow.engine_for("orientation")
        with self.assertRaises(ResearchError): self.store.configure_study("Bad", settings={"model": "same", "review_model": "same"})
        self.store.configure_study("Explicit", settings={"model": "writer", "review_model": "critic"})
        self.assertEqual(workflow.engine_for("draft").model, "writer")
        self.assertEqual(workflow.engine_for("verify").model, "critic")
        self.assertEqual(workflow.engine_for("review").model, "critic")

    def test_prompt_contracts_are_complete(self):
        self.assertIn("No search proves novelty.", roles.PROMPTS["comparison"])
        self.assertNotIn("No search A position", roles.PROMPTS["comparison"])
        self.assertNotIn("Distinguish absent evidence Check", roles.PROMPTS["review"])
        self.assertIn("Distinguish lack of evidence from evidence against the claim.", roles.PROMPTS["review"])
        self.assertEqual(set(roles.PROMPTS), set(roles.PROMPT_VERSIONS))

    def test_anonymization_removes_host_record_identifiers(self):
        result = anonymize('Text E001 and C002.\n<!-- block:B001 claims:C002 -->\n')
        self.assertNotIn("E001", result)
        self.assertNotIn("<!--", result)
        self.assertIn("[record]", result)

    def test_citation_discovery_enters_acquisition_and_draft(self):
        self.store.configure_study("Exercise graph acquisition", settings={"citation_seeds": 1, "citation_limit": 1, "citation_budget": 1, "paper_limit": 1})
        seed = work("W1", refs=["https://openalex.org/W2"])
        neighbor = work("W2")
        neighbor["title"] = "Checking workload in AI collaboration"
        def search(store, query, provider, limit):
            sid = store.start_search(provider, query, limit, "https://fixture.invalid/search")
            return store.finish_search(sid, [parse_work(seed)], response={"synthetic": True})
        def requests(url):
            if "/W1" in url: return seed
            if "cites" in url: return {"results": [], "meta": {"count": 0}}
            return {"results": [neighbor]}
        workflow = ResearchWorkflow(self.store, FixtureEngine())
        with patch("loop_research.workflow.search_papers", search), patch("loop_research.workflow.fetch_paper", fake_fetch), patch("loop_research.discovery.request", requests), contextlib.redirect_stderr(io.StringIO()):
            result = workflow.run(50)
        self.assertEqual(result["state"]["status"], "ready_for_human", result["state"]["reason"])
        self.assertTrue(self.store.show_paper("P002")["documents"])
        self.assertTrue(self.store.citation_graph())
        draft = workflow.current_artifact("draft")
        papers = {self.store.evidence_provenance(eid)["paper_id"] for block in roles.blocks(draft["content"]) for eid in block["evidence_ids"]}
        self.assertIn("P002", papers)

    def test_review_model_change_invalidates_verification_without_self_invalidation(self):
        workflow = self.full()
        before = workflow.current_verification()
        self.assertIsNotNone(before)
        # Simulate a new explicitly selected reviewer without changing the scientific input.
        workflow.engine.config = dict(workflow.engine.config, model="another-reviewer")
        self.assertIsNone(workflow.current_verification())
        with contextlib.redirect_stderr(io.StringIO()):
            workflow.perform("verify")
        self.assertNotEqual(workflow.current_verification()["id"], before["id"])
        self.assertIsNone(workflow.latest_relevant_review())

    def test_http_retry_is_bounded_and_secrets_are_not_recorded(self):
        import urllib.error
        import urllib.request
        from loop_research.literature import get_bytes, SafeRedirect
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b"{}"
            def geturl(self): return "https://api.openalex.org/works"
        opener = unittest.mock.Mock()
        opener.open.side_effect = [urllib.error.HTTPError("https://api.openalex.org/works", 429, "rate", {"Retry-After": "0"}, None), Response()]
        with patch("loop_research.literature.urllib.request.build_opener", return_value=opener), patch("loop_research.literature.time.sleep"):
            payload, url = get_bytes("https://api.openalex.org/works", 100, headers={"Authorization": "Bearer secret"}, retries=2)
        self.assertEqual(payload, b"{}")
        self.assertEqual(opener.open.call_count, 2)
        self.assertNotIn("secret", url)
        original = urllib.request.Request("https://api.openalex.org/works", headers={"Authorization": "Bearer secret"})
        redirected = SafeRedirect().redirect_request(original, None, 302, "", {}, "https://other.invalid/works")
        self.assertFalse(redirected.has_header("Authorization"))
        opener.open.side_effect = urllib.error.HTTPError(url, 429, "rate", {}, None)
        with patch("loop_research.literature.urllib.request.build_opener", return_value=opener), patch("loop_research.literature.time.sleep"):
            with self.assertRaises(ResearchError): get_bytes(url, 100, retries=2)
        self.assertEqual(opener.open.call_count, 5)

    def test_human_scores_cannot_be_fabricated_or_change_fact_text(self):
        scores = {"samples": [{"sample_id": "Sample-01", "manuscript_available": True,
                              "scores": {k: None for k in ("prior_work", "argument", "counterarguments", "qualification", "traceability")},
                              "facts": [{"id": "Fact-01", "text": "A factual assertion", "verdict": None, "source_locator": "", "reason": ""}]}]}
        (self.root / "blind-packet").mkdir()
        save(self.root / "blind-packet" / "scores.json", scores)
        save(self.root / "private-sample-map.json", {"Sample-01": {"case_id": "case", "method": "baseline"}})
        save(self.root / "submitted.json", scores)
        with self.assertRaises(ResearchError): import_scores(self.root, self.root / "submitted.json")
        scores["samples"][0]["facts"][0]["text"] = "Altered assertion"
        save(self.root / "submitted.json", scores)
        with self.assertRaises(ResearchError): import_scores(self.root, self.root / "submitted.json")
