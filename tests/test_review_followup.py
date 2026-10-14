"""Issue-directed research fixtures; no real publications or quality ground truth."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from loop_research import ledger, roles
from loop_research.common import ResearchError
from loop_research.schema import validate
from loop_research.store import ResearchStore
from loop_research.workflow import ResearchWorkflow
from .fixtures import FixtureEngine, fake_search, fake_fetch


class ResearchFixture(FixtureEngine):
    followup = False
    followup_reviews = 0

    def execute(self, kind, data, prompt, schema, on_spawn=None):
        raw, execution = super().execute(kind, data, prompt, schema, on_spawn)
        if kind == 'review' and self.followup:
            self.followup_reviews += 1
            if self.followup_reviews == 1:
                raw['decision'] = 'revise'
                raw['findings'] = [{'severity': 'major', 'category': 'novelty', 'target_type': 'paper',
                                    'target_id': 'P002', 'explanation': 'Read the synthetic rival framework before deciding the difference.',
                                    'evidence_ids': [], 'suggested_action': 'read', 'remaining_uncertainty': 'Overlap unknown.'}]
        if kind == 'redesign' and not data.get('research_progress', {}).get('readings'):
            raw.update(decision='research', text='', requested_searches=[],
                       reading_requests=[{'paper_id': 'P002', 'issue_ids': data['allowed_issue_ids'],
                                          'purpose': 'Compare the synthetic framework with the proposed scope.'}])
        return raw, execution


class ReviewFollowup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ResearchStore(Path(self.tmp.name) / 'study.sqlite3', initialize=True)
        self.store.init('Synthetic checking workload')
        self.store.configure_study('Bounded offline fixture', settings={'semantic_selection': 0, 'citation_seeds': 0,
                                                                      'pages_per_document': 1, 'draft_checkpoint_readings': 1})
        self.engine = ResearchFixture()
        self.workflow = ResearchWorkflow(self.store, self.engine)
        with patch('loop_research.workflow.search_papers', fake_search), \
             patch('loop_research.workflow.fetch_paper', fake_fetch), contextlib.redirect_stderr(io.StringIO()):
            result = self.workflow.run(100)
        self.assertEqual(result['state']['status'], 'ready_for_human')
        sid = self.store.start_search('fixture', 'synthetic known rival', 1, 'https://fixture.invalid')
        self.store.finish_search(sid, [{'canonical_key': 'synthetic:rival', 'provider': 'fixture', 'external_id': 'rival',
                                     'title': 'Synthetic rival framework', 'abstract': '', 'authors': [],
                                     'url': 'https://fixture.invalid/rival', 'metadata': {'synthetic': True}}], response={'synthetic': True})
        self.engine.followup = True
        with contextlib.redirect_stderr(io.StringIO()):
            for kind in ('outline', 'draft', 'verify', 'review'):
                self.workflow.perform(kind)
        self.first_review = self.workflow.latest_relevant_review()
        self.issue = self.store.list_issues()[0]

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def plan(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.workflow.perform('redesign')
        return self.workflow.research_plan()

    def target_fetch(self, store, paper, abstract=False):
        if paper != 'P002':
            return fake_fetch(store, paper, abstract)
        from loop_research.literature import fetch_paper
        path = store.path.parent / 'synthetic-rival.txt'
        path.write_text('In this synthetic fixture, participants checked an answer for two minutes. '
                        'A synthetic rival framework distinguishes permitted and out-of-scope uses.')
        return fetch_paper(store, paper, file=str(path))

    def test_known_paper_reading_plan_does_not_require_a_redundant_search(self):
        plan = self.plan()
        self.assertEqual(plan['content']['requested_searches'], [])
        validate(plan['content'], roles.REDESIGN)
        with patch.object(self.workflow, 'pending_queries', return_value=[{'query': 'broad search', 'provider': 'arxiv', 'purpose': 'Unrelated remainder'}]):
            choices = self.workflow.choices()
        self.assertEqual([c['kind'] for c in choices], ['fetch'])
        self.assertEqual(choices[0]['parameters']['paper'], 'P002')
        self.assertEqual(self.store.list_issues()[0]['status'], 'investigating')

    def test_plan_rejects_unknown_or_unbound_paper_and_issue_ids(self):
        data = self.workflow.data_for('redesign')
        raw, _ = self.engine.execute('redesign', data, roles.PROMPTS['redesign'], roles.REDESIGN)
        for key, value in [('paper_id', 'P999'), ('issue_ids', ['I999']), ('issue_ids', [])]:
            bad = copy.deepcopy(raw)
            bad['reading_requests'][0][key] = value
            with self.assertRaises(ResearchError):
                roles.validate_semantics('redesign', data, bad)
        bad = copy.deepcopy(raw)
        bad['reading_requests'] *= 2
        with self.assertRaises(ResearchError): roles.validate_semantics('redesign', data, bad)

    def test_new_reading_keeps_historical_criticism_but_invalidates_approval(self):
        plan = self.plan()
        self.target_fetch(self.store, 'P002')
        with contextlib.redirect_stderr(io.StringIO()):
            task = self.workflow.perform('reading', document='D002', page=1)
        self.assertEqual(task['input']['data']['review_followup']['plan_id'], plan['id'])
        self.assertEqual(task['input']['data']['review_followup']['reading_requests'][0]['issue_ids'], [self.issue['id']])
        self.assertIsNone(self.workflow.latest_relevant_review())
        self.assertEqual(self.workflow.followup_review()['id'], self.first_review['id'])
        self.workflow.ground(self.workflow.selected())
        data = self.workflow.data_for('redesign')
        self.assertFalse(data['review_is_current'])
        self.assertEqual(data['research_progress']['readings'][0]['paper_id'], 'P002')
        self.assertEqual(data['allowed_issue_ids'], [self.issue['id']])
        self.assertEqual(self.store.list_issues()[0]['status'], 'investigating')
        with self.assertRaises(ResearchError): self.workflow.finalize('Historical criticism is not approval')

    def test_full_read_revise_reverify_rereview_cycle_preserves_human_gate(self):
        with patch('loop_research.workflow.fetch_paper', self.target_fetch), \
             patch('loop_research.workflow.search_papers', side_effect=AssertionError('No rediscovery of a known paper')), \
             contextlib.redirect_stderr(io.StringIO()):
            result = self.workflow.run(100)
        self.assertEqual(result['state']['status'], 'ready_for_human', result['state']['reason'])
        self.assertEqual(self.store.claim_row(result['state']['selected_claim_id'])['revision'], 2)
        self.assertEqual(self.engine.calls.count('redesign'), 2)
        self.assertEqual(self.engine.followup_reviews, 2)
        self.assertEqual(self.store.list_issues()[0]['status'], 'addressed')
        revision = self.workflow.current_artifact('draft')['input']['revision_plan']
        self.assertEqual(revision['review_id'], self.first_review['id'])
        self.assertFalse(revision['deferred_research'])
        self.assertIsNotNone(self.workflow.current_verification())
        with self.assertRaises(ResearchError): self.workflow.finalize('Unchecked sources still require human confirmation')

    def test_acquisition_budget_is_not_bypassed_for_targeted_papers(self):
        self.plan()
        self.store.configure_study('No additional acquisition slots', settings={'paper_limit': 1})
        self.assertNotIn('fetch', [c['kind'] for c in self.workflow.choices()])
        self.assertNotIn('reading', [c['kind'] for c in self.workflow.choices()])
        self.assertEqual(self.store.list_issues()[0]['status'], 'investigating')

    def test_unread_requests_remain_deferred_when_a_wave_reaches_its_limit(self):
        sid = self.store.start_search('fixture', 'another synthetic rival', 1, 'https://fixture.invalid')
        self.store.finish_search(sid, [{'canonical_key': 'synthetic:rival2', 'provider': 'fixture', 'external_id': 'rival2',
                                     'title': 'Another synthetic rival', 'abstract': '', 'authors': [],
                                     'url': 'https://fixture.invalid/rival2', 'metadata': {'synthetic': True}}], response={'synthetic': True})
        execute = self.engine.execute
        def more_work(kind, data, prompt, schema, on_spawn=None):
            raw, usage = execute(kind, data, prompt, schema, on_spawn)
            if kind == 'redesign' and raw['decision'] == 'research':
                raw['reading_requests'].append({'paper_id': 'P003', 'issue_ids': data['allowed_issue_ids'], 'purpose': 'Another unresolved comparison.'})
            return raw, usage
        with patch.object(self.engine, 'execute', side_effect=more_work), contextlib.redirect_stderr(io.StringIO()):
            self.plan()
            self.target_fetch(self.store, 'P002')
            self.workflow.perform('reading', document='D002', page=1)
            self.assertIsNone(self.workflow.research_choices(self.workflow.research_plan()))
            self.workflow.ground(self.workflow.selected())
            self.workflow.perform('redesign')
            self.workflow.perform('outline')
            self.workflow.perform('draft')
        data = self.workflow.current_artifact('draft')['input']
        self.assertTrue(data['discovery_deferred'])
        self.assertEqual(data['revision_plan']['research_progress']['pending_paper_ids'], ['P003'])
        self.assertEqual(self.store.list_issues()[0]['status'], 'revised_pending_review')

    def test_snapshot_records_deferred_flag_titles_and_only_recorded_scope(self):
        draft = self.workflow.current_artifact('draft')
        data = self.workflow.data_for('verify')
        records = data['ledger_snapshot']['records']
        self.assertEqual(records['discovery_deferred'], draft['input']['discovery_deferred'])
        self.assertEqual(records['paper_titles']['P002'], 'Synthetic rival framework')
        self.assertEqual(records['citation_graph'], [])
        absent = ledger.snapshot(self.store, self.workflow.scope(), self.workflow.context(only_claim=self.workflow.selected()))
        self.assertNotIn('discovery_deferred', absent['records'])

    def test_unchecked_cannot_prove_absence_of_human_verification(self):
        records = {'evidence_status': {'E001': 'unchecked'}}
        data = {'ledger_snapshot': {'records': records, 'sha256': hashlib.sha256(ledger.canonical(records).encode()).hexdigest()}}
        for text, expected in [('The recorded status is unchecked.', 'supported'),
                               ('The evidence is not human-verified.', 'undetermined')]:
            raw = {'id': 'G001', 'block_id': 'B001', 'field': 'text', 'text': text, 'occurrence': 1,
                   'checks': [{'path': '/evidence_status/E001', 'operator': 'equals', 'expected_json': json.dumps('unchecked')}],
                   'reason': 'Synthetic state/activity boundary'}
            result = ledger.normalize(data, [raw], {'B001': {'text': text}})
            self.assertEqual(result[0]['verdict'], expected)

    def test_disabling_checkpoint_also_disables_early_research_wave_handoff(self):
        plan = self.plan()
        pages = ['In this synthetic fixture, participants checked an answer for two minutes.',
                 'A second synthetic passage describes a rival reporting framework.']
        self.store.save_document(None, 'P002', '\n'.join(pages).encode(), 'text', 'https://fixture.invalid/rival', pages, 'synthetic-v1')
        with contextlib.redirect_stderr(io.StringIO()):
            self.workflow.perform('reading', document='D002', page=1)
        self.store.configure_study('Disable early handoff', settings={'draft_checkpoint_readings': 0, 'pages_per_document': 2})
        import time
        self.workflow.run_seconds, self.workflow.deadline = 600, time.monotonic() + 600
        choices = self.workflow.research_choices(plan)
        self.assertIsNotNone(choices)
        self.assertEqual(choices[0]['kind'], 'reading')
        self.assertEqual(choices[0]['parameters']['page'], 2)

    def test_handoff_uses_actual_reserved_tail_not_four_maximum_timeouts(self):
        self.store.configure_study('Two reading wave', settings={'draft_checkpoint_readings': 2})
        self.workflow.run_seconds, self.workflow.deadline = 1200, 1000
        with patch('loop_research.workflow.time.monotonic', return_value=100):
            self.assertIsNone(self.workflow.research_handoff(1))
            self.assertEqual(self.workflow.research_handoff(2), 'reading_checkpoint')
        with patch('loop_research.workflow.time.monotonic', return_value=700):
            self.assertEqual(self.workflow.research_handoff(1), 'time_reserve')

    def test_negative_snippet_triage_does_not_skip_requested_paper_inspection(self):
        plan = self.plan()
        self.target_fetch(self.store, 'P002')
        self.store.configure_study('Semantic screen', settings={'semantic_selection': 1})
        result = {'content': {'judgments': [{'candidate_id': 'D002:1', 'relevance': 'unrelated', 'reason': 'Synthetic triage'}]}}
        with patch.object(self.workflow, 'cached_selection', return_value=result):
            choices = self.workflow.research_choices(plan)
        self.assertEqual([c['kind'] for c in choices], ['reading'])
        self.assertEqual(choices[0]['parameters'], {'document': 'D002', 'page': 1})
        self.assertEqual(self.store.list_issues()[0]['status'], 'investigating')
        self.assertEqual(self.workflow.plan_progress(plan)['readings'], [])

    def test_redesign_input_deduplicates_only_identical_anchor_copies(self):
        before = self.workflow.context()
        rows = self.store.db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]
        after = self.workflow.data_for('redesign')['context']
        self.assertLess(len(json.dumps(after)), len(json.dumps(before)))
        for old, kept in after['omitted_anchor_evidence'].items():
            self.assertEqual(before['evidence'][old]['excerpt_id'], after['evidence'][kept]['excerpt_id'])
            self.assertEqual(before['evidence'][old]['document_source'], after['evidence'][kept]['document_source'])
            self.assertEqual(before['evidence'][old]['status'], after['evidence'][kept]['status'])
            self.assertEqual(after['evidence'][kept]['claim_id'], self.workflow.selected())
            self.assertEqual(after['evidence'][kept]['anchor_interpretations'][old], before['evidence'][old]['interpretation'])
            self.assertIsNotNone(self.store.show_evidence(old))
        self.assertEqual(rows, self.store.db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0])

    def test_distinct_human_status_is_not_deduplicated(self):
        self.store.assess_evidence('E001', 'checked', 'Explicit synthetic fixture confirmation')
        context = self.workflow.context(deduplicate_anchor=True)
        self.assertIn('E001', context['evidence'])
        self.assertEqual(context['evidence']['E001']['status'], 'checked')
