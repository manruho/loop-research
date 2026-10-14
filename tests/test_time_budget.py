"""Budgeting must protect validation without weakening its acceptance criteria."""
import contextlib
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from loop_research import budget, roles
from loop_research.common import ResearchError
from loop_research.store import ResearchStore
from loop_research.workflow import ResearchWorkflow
from .fixtures import FixtureEngine, fake_search, fake_fetch


class TimeBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ResearchStore(Path(self.tmp.name) / 'study.sqlite3', initialize=True)
        self.store.init('Synthetic checking workload')
        self.store.configure_study('Isolate wall time', settings={'semantic_selection': 0, 'citation_seeds': 0,
                                                                'draft_checkpoint_readings': 1, 'paper_limit': 1})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_envelopes_preserve_verification_and_review_and_configured_cap(self):
        self.assertEqual(budget.allocation('outline', 600, 400, 240)['timeout_seconds'], 130)
        self.assertEqual(budget.allocation('verify', 600, 310, 240)['timeout_seconds'], 220)
        self.assertEqual(budget.allocation('review', 600, 80, 240)['timeout_seconds'], 80)
        self.assertEqual(budget.allocation('draft', 600, 200, 240)['timeout_seconds'], 0)
        self.assertEqual(budget.allocation('reading', 7200, 7200, 180)['timeout_seconds'], 180)

    def test_real_engine_enforces_envelope_and_retains_independent_model(self):
        self.store.configure_study('Explicit test models', settings={'model': 'writer', 'review_model': 'critic', 'timeout': 240})
        w = ResearchWorkflow(self.store)
        w.deadline, w.run_seconds = 500, 600
        with patch('loop_research.workflow.time.monotonic', return_value=100):
            self.assertEqual(w.engine_for('outline').timeout, 130)
            verifier = w.engine_for('verify')
            self.assertEqual(verifier.timeout, 240)
            self.assertEqual(verifier.model, 'critic')
            self.assertEqual(w.engine_for('review').model, 'critic')

    def test_elapsed_clock_is_not_part_of_semantic_cache_key(self):
        w = ResearchWorkflow(self.store, FixtureEngine())
        normal_key = w.prepare('orientation')[1]
        w.deadline, w.run_seconds = 1000, 600
        with patch('loop_research.workflow.time.monotonic', return_value=10):
            first = w.prepare('orientation')[1]
        with patch('loop_research.workflow.time.monotonic', return_value=20):
            second = w.prepare('orientation')[1]
        self.assertEqual(first, second)
        self.assertNotEqual(first, normal_key)
        self.assertEqual(w.prompt_for('verify'), roles.PROMPTS['verify'])
        self.assertEqual(w.prompt_for('review'), roles.PROMPTS['review'])

    def test_reserved_tail_stops_unfinished_writing_and_resume_keeps_history(self):
        clock = [0.0]
        class SlowFixture(FixtureEngine):
            def execute(self, kind, *args, **kwargs):
                result = super().execute(kind, *args, **kwargs)
                clock[0] += 20 if kind == 'outline' else 10
                return result
        engine = SlowFixture()
        w = ResearchWorkflow(self.store, engine)
        with patch('loop_research.workflow.time.monotonic', side_effect=lambda: clock[0]), \
             patch('loop_research.workflow.search_papers', fake_search), \
             patch('loop_research.workflow.fetch_paper', fake_fetch), contextlib.redirect_stderr(io.StringIO()):
            result = w.run(100, max_seconds=100)
            self.assertEqual(result['state']['status'], 'budget_exhausted')
            self.assertIsNone(result['current_draft'])
            self.assertNotIn('verify', engine.calls)
            self.assertLess(clock[0], 100)
            self.assertIsNone(w.deadline)
            self.assertIsNone(w.run_seconds)
            task = self.store.show_task(self.store.list_tasks()[0]['id'])
            self.assertEqual(task['config']['run_budget']['reserved_seconds'], 45)
            self.assertIn('TIME-BOUNDED WORKING PROFILE', task['prompt'])
            resumed = w.run(100, resume_reason='Fresh continuation budget')
        self.assertEqual(resumed['state']['status'], 'ready_for_human')
        self.assertEqual(engine.calls.count('orientation'), 1)
        self.assertIsNotNone(w.current_verification())
        self.assertIsNotNone(w.latest_relevant_review())

    def test_existing_draft_is_audited_before_more_discovery(self):
        w = ResearchWorkflow(self.store, FixtureEngine())
        with patch('loop_research.workflow.search_papers', fake_search), \
             patch('loop_research.workflow.fetch_paper', fake_fetch), contextlib.redirect_stderr(io.StringIO()):
            for _ in range(20):
                result = w.run(1, resume_reason='Synthetic step')
                if result['current_draft']:
                    break
            self.assertIsNotNone(result['current_draft'])
            w.run_seconds, w.deadline = 600, time.monotonic() + 600
            with patch.object(w, 'pending_queries', return_value=[{'query': 'more evidence', 'provider': 'arxiv', 'purpose': 'A gap'}]) as pending:
                self.assertEqual([c['kind'] for c in w.choices()], ['verify'])
                w.perform('verify')
                self.assertEqual([c['kind'] for c in w.choices()], ['review'])
                pending.assert_not_called()
            self.assertEqual(self.store.study_state()['status'], 'budget_exhausted')

    def test_invalid_run_also_clears_transient_envelope(self):
        w = ResearchWorkflow(self.store, FixtureEngine())
        with self.assertRaises(ResearchError):
            w.run(0, max_seconds=600)
        self.assertIsNone(w.run_seconds)
        self.assertIsNone(w.deadline)

    def test_phase_timeout_does_not_claim_completion_or_skip_to_verification(self):
        engine = FixtureEngine(fail='outline')
        w = ResearchWorkflow(self.store, engine)
        with patch('loop_research.workflow.search_papers', fake_search), \
             patch('loop_research.workflow.fetch_paper', fake_fetch), contextlib.redirect_stderr(io.StringIO()):
            result = w.run(100, max_seconds=600)
        self.assertEqual(result['state']['status'], 'budget_exhausted')
        self.assertIn('工程上限', result['state']['reason'])
        self.assertIsNone(result['current_draft'])
        self.assertNotIn('verify', engine.calls)
        self.assertNotIn('review', engine.calls)
        self.assertEqual(self.store.list_tasks()[-1]['status'], 'timed_out')
