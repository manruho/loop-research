import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loop_research.common import ResearchError
from loop_research.export import export_study
from loop_research.store import ResearchStore
from loop_research.workflow import ResearchWorkflow, study_lock
from loop_research import roles
from .fixtures import FixtureEngine, fake_fetch, fake_search


class HarnessAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="loop-research-acceptance-")
        self.root = Path(self.temporary.name)
        self.store = ResearchStore(self.root / "study.sqlite3", initialize=True)
        self.store.init("Synthetic checking workload question")

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def run_study(self, engine=None, steps=40, reason=None):
        engine = engine or FixtureEngine()
        self.store.configure_study("Offline fixture has no citation provider", settings={"citation_seeds": 0})
        workflow = ResearchWorkflow(self.store, engine)
        with patch("loop_research.workflow.search_papers", fake_search), patch("loop_research.workflow.fetch_paper", fake_fetch), contextlib.redirect_stderr(io.StringIO()):
            result = workflow.run(steps, resume_reason=reason)
        return workflow, engine, result

    def test_topic_to_grounded_manuscript_review_and_export(self):
        workflow, engine, result = self.run_study()
        self.assertEqual(result["state"]["status"], "ready_for_human", result["state"]["reason"])
        for role in ("orientation", "reading", "exploration", "comparison", "outline", "draft", "review"):
            self.assertIn(role, engine.calls)
        self.assertEqual(len(workflow.candidates()), 2)
        draft = workflow.current_artifact("draft")
        for block in roles.blocks(draft["content"]):
            self.assertTrue(block["claim_ids"])
            for eid in block["evidence_ids"]:
                evidence = self.store.show_evidence(eid)
                self.assertEqual(evidence["status"], "unchecked")
                source = evidence["document_source"]
                self.assertIn(evidence["quote"], self.store.document_page(source["document_id"], source["page"]))
        report = self.root / "report"
        export_study(self.store, report, include_sources=True)
        manifest = json.loads((report / "manifest.json").read_text())
        required = {"research-map.md", "claims.json", "evidence.json", "literature.json", "draft.md",
                    "reviews.json", "decisions.json", "uncertainties.md", "tasks.json", "references.bib"}
        self.assertTrue(required <= set(manifest["files"]))
        for name, checksum in manifest["files"].items():
            self.assertEqual(hashlib.sha256((report / name).read_bytes()).hexdigest(), checksum)
        self.assertIn("[^1]", (report / "draft.md").read_text())
        with self.assertRaises(ResearchError):
            export_study(self.store, report)
        with self.assertRaises(ResearchError):
            workflow.finalize("Not human-verified")

    def test_review_returns_to_thesis_design_and_reassesses_old_issue(self):
        workflow, engine, result = self.run_study(FixtureEngine(repair=True))
        self.assertEqual(result["state"]["status"], "ready_for_human", result["state"]["reason"])
        self.assertIn("redesign", engine.calls)
        self.assertEqual(len(self.store.list_artifacts("draft")), 2)
        selected = self.store.claim_row(result["state"]["selected_claim_id"])
        self.assertEqual(selected["revision"], 2)
        self.assertTrue(any(edge["kind"] == "revise" for edge in self.store.claim_graph()))
        issue = self.store.list_issues()[0]
        self.assertEqual(issue["status"], "addressed")
        self.assertIn("内部査読", issue["response"])
        self.assertTrue(issue["remaining_uncertainty"])
        self.assertEqual(workflow.current_artifact("draft")["claim_revision"], 2)

    def test_budget_stop_is_not_completion_and_resumes_without_repeating(self):
        workflow, engine, first = self.run_study(steps=2)
        self.assertEqual(first["state"]["status"], "budget_exhausted")
        self.assertIsNone(first["current_draft"])
        calls = engine.calls.count("orientation")
        self.store.close()
        self.store = ResearchStore(self.root / "study.sqlite3")
        workflow, _, result = self.run_study(engine, reason="Explicit continuation")
        self.assertEqual(result["state"]["status"], "ready_for_human", result["state"]["reason"])
        self.assertEqual(engine.calls.count("orientation"), calls)

    def test_hold_and_withdraw_are_valid_no_paper_outcomes(self):
        workflow, _, result = self.run_study(FixtureEngine(hold=True))
        self.assertEqual(result["state"]["status"], "held")
        self.assertIsNone(result["current_draft"])
        _, _, unchanged = self.run_study(FixtureEngine())
        self.assertEqual(unchanged["state"]["status"], "held")
        report = self.root / "held-report"
        export_study(self.store, report)
        self.assertIn("No manuscript", (report / "draft.md").read_text())

    def test_withdrawal_is_a_recorded_no_paper_outcome(self):
        _, _, result = self.run_study(FixtureEngine(withdraw=True))
        self.assertEqual(result["state"]["status"], "withdrawn")
        self.assertIsNone(result["current_draft"])
        self.assertTrue(self.store.latest_artifact("comparison")["content"]["reason"])

    def test_human_checked_sources_can_finalize_after_fresh_review(self):
        workflow, engine, result = self.run_study()
        for evidence in self.store.show_claim(result["state"]["selected_claim_id"])["evidence"]:
            self.store.assess_evidence(evidence["id"], "checked", "Human checked the synthetic excerpt and interpretation")
        with self.assertRaises(ResearchError):
            workflow.finalize("Old manuscript must not be finalized")
        workflow, _, result = self.run_study(engine)
        self.assertEqual(result["state"]["status"], "ready_for_human", result["state"]["reason"])
        finalized = workflow.finalize("Human accepts a limited synthetic position for software validation")
        self.assertEqual(finalized["state"]["status"], "finalized")
        self.assertIsNotNone(finalized["current_draft"])

    def test_repeated_execution_settings_do_not_invalidate_current_manuscript(self):
        workflow, _, result = self.run_study()
        before = workflow.basis(result["state"]["selected_claim_id"])
        self.store.configure_study("Same settings", settings={"model": None, "timeout": 180})
        self.assertEqual(workflow.basis(result["state"]["selected_claim_id"]), before)
        self.assertIsNotNone(workflow.current_artifact("draft"))

    def test_human_draft_import_preserves_old_version_and_requires_new_review(self):
        workflow, engine, result = self.run_study()
        original = workflow.current_artifact("draft")
        editable = json.loads(json.dumps(original["content"]))
        editable["sections"][0]["blocks"][0]["text"] += " This is an explicitly limited discussion proposal."
        path = self.root / "edited.json"
        path.write_text(json.dumps(editable), encoding="utf-8")
        imported = workflow.import_draft(path, "Human clarified the scope")
        self.assertNotEqual(imported["id"], original["id"])
        self.assertIsNone(imported["task_id"])
        with self.assertRaises(ResearchError):
            workflow.finalize("No review of the new manuscript")
        with contextlib.redirect_stderr(io.StringIO()):
            workflow.perform("verify")
            workflow.perform("review")
        self.assertEqual(workflow.latest_relevant_review()["input"]["draft"]["id"], imported["id"])
        self.assertEqual(len(self.store.list_artifacts("draft")), 2)

    def test_human_guidance_makes_late_results_stale(self):
        class GuidingEngine(FixtureEngine):
            def execute(engine, kind, data, prompt, schema, on_spawn=None):
                self.store.event("study.guidance", "Human changes the value premise", {"actor": "human"})
                self.store.db.commit()
                return super().execute(kind, data, prompt, schema, on_spawn)
        workflow = ResearchWorkflow(self.store, GuidingEngine())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(ResearchError):
            workflow.perform("orientation")
        self.assertEqual(self.store.list_tasks()[-1]["status"], "stale")
        self.assertEqual(self.store.list_artifacts("orientation"), [])

    def test_changed_question_does_not_mix_old_positions_with_new_study(self):
        workflow, _, result = self.run_study()
        self.store.configure_study("A genuinely different problem", question="A new synthetic question")
        self.assertEqual(workflow.candidates(), {})
        self.assertIsNone(self.store.study_state()["selected_claim_id"])

    def test_prior_major_issue_cannot_be_hidden_by_rewriting(self):
        workflow, engine, result = self.run_study(FixtureEngine(repair=True))
        data = workflow.data_for("review")
        old = self.store.list_issues()[0]
        old["status"] = "revised_pending_review"
        data["prior_issues"] = [old]
        review = {"decision": "ready_for_human", "synthesis": "Looks polished", "findings": [],
                  "requested_searches": [], "unresolved": ["Original issue remains uncertain."],
                  "prior_issue_assessments": []}
        with self.assertRaises(ResearchError):
            roles.validate_semantics("review", data, review)
        review["prior_issue_assessments"] = [{"issue_id": old["id"], "status": "persists",
                                             "reason": "Only wording changed", "remaining_uncertainty": "Still unsupported"}]
        with self.assertRaises(ResearchError):
            roles.validate_semantics("review", data, review)

    def test_late_result_cannot_overwrite_human_question_change(self):
        class ChangingEngine(FixtureEngine):
            def execute(engine, kind, data, prompt, schema, on_spawn=None):
                self.store.configure_study("Human changed question", question="A different research question")
                return super().execute(kind, data, prompt, schema, on_spawn)
        workflow = ResearchWorkflow(self.store, ChangingEngine())
        with self.assertRaises(ResearchError):
            workflow.perform("orientation")
        self.assertEqual(self.store.list_tasks()[-1]["status"], "stale")
        self.assertEqual(self.store.list_artifacts("orientation"), [])
        self.assertEqual(self.store.study_state()["question"], "A different research question")

    def test_evidence_problem_propagates_across_branches_and_invalidates_ready_draft(self):
        workflow, _, result = self.run_study()
        anchor = self.store.study_state()["anchor_claim_id"]
        source = self.store.show_claim(anchor)["evidence"][0]["id"]
        selected = result["state"]["selected_claim_id"]
        self.store.assess_evidence(source, "disputed", "Source was misinterpreted")
        self.assertEqual(self.store.study_state()["status"], "active")
        self.assertIsNone(workflow.current_artifact("draft"))
        for evidence in self.store.show_claim(selected)["evidence"]:
            self.assertEqual(evidence["status"], "disputed")

    def test_failed_inference_is_recorded_without_manuscript_or_false_success(self):
        _, _, result = self.run_study(FixtureEngine(fail="orientation"))
        self.assertEqual(result["state"]["status"], "needs_human")
        self.assertEqual(result["tasks"][-1]["status"], "timed_out")
        self.assertEqual(result["artifacts"], [])

    def test_fork_merge_preserves_source_confirmation_and_graph(self):
        workflow, _, result = self.run_study()
        claims = list(workflow.candidates())
        branch = self.store.fork_claim([claims[0]], "A human alternative", "Explicit fork")
        merged = self.store.fork_claim([claims[0], branch["id"]], "A human merge", "Explicit merge", kind="merge")
        self.assertEqual(len([edge for edge in merged["edges"] if edge["child_id"] == merged["id"]]), 2)
        self.assertTrue(all(e["status"] == "unchecked" for e in merged["evidence"]))

    def test_source_owner_and_study_lock_prevent_false_recovery_and_duplicate_execution(self):
        sid = self.store.start_search("arxiv", "independent live search", 1, "https://fixture.invalid")
        workflow = ResearchWorkflow(self.store, FixtureEngine())
        workflow.recover()
        self.assertEqual(self.store.show_search(f"S{sid:03d}")["status"], "running")
        with study_lock(self.store):
            with self.assertRaises(ResearchError):
                with study_lock(self.store):
                    pass


if __name__ == "__main__":
    unittest.main()
