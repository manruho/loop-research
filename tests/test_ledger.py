"""Offline ledger checks and repeated-review handoff acceptance cases."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from loop_research import ledger, roles, verification
from loop_research.common import ResearchError
from loop_research.store import ResearchStore
from loop_research.workflow import ResearchWorkflow
from loop_research.export import export_study
from tests.fixtures import FixtureEngine, fake_search, fake_fetch


class PersistentReviewer(FixtureEngine):
    def execute(self, kind, data, prompt, schema, on_spawn=None):
        raw, execution = super().execute(kind, data, prompt, schema, on_spawn)
        if kind == 'review':
            raw['decision'] = 'revise'
            raw['findings'] = [{'severity': 'major', 'category': 'citation', 'target_type': 'block',
                                'target_id': 'B001', 'explanation': 'Synthetic persistent issue',
                                'evidence_ids': [], 'suggested_action': 'revise', 'remaining_uncertainty': 'Synthetic unresolved'}]
            for item in raw['prior_issue_assessments']:
                item['status'] = 'persists'
        if kind == 'revise_draft':
            raw['abstract_blocks'][0]['qualification'] += f' Repair attempt {self.reviews}.'
        return raw, execution


class LedgerAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ResearchStore(self.root / 'study.sqlite3', initialize=True)
        self.store.init('Synthetic ledger question')
        self.store.configure_study('Offline', settings={'citation_seeds': 0})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def run_with(self, engine):
        workflow = ResearchWorkflow(self.store, engine)
        with patch('loop_research.workflow.search_papers', fake_search), patch('loop_research.workflow.fetch_paper', fake_fetch), contextlib.redirect_stderr(io.StringIO()):
            result = workflow.run(60)
        return workflow, result

    def case(self):
        workflow, result = self.run_with(FixtureEngine())
        self.assertEqual(result['state']['status'], 'ready_for_human')
        data = workflow.data_for('verify')
        raw, _ = FixtureEngine().execute('verify', data, '', {})
        block = roles.blocks(data['draft']['content'])[0]
        block['text'] = 'The ledger records one search.'
        claim = {'id': 'G001', 'block_id': block['id'], 'field': 'text', 'text': block['text'], 'occurrence': 1,
                 'checks': [{'path': '/search_count', 'operator': 'equals', 'expected_json': '1'}],
                 'reason': 'Extracted count assertion'}
        raw['ledger_claims'] = [claim]
        next(c for c in raw['ledger_coverage'] if c['block_id'] == block['id'])['status'] = 'complete'
        return workflow, data, raw

    def test_host_checks_count_and_retains_snapshot_and_exact_span(self):
        workflow, data, raw = self.case()
        result = verification.normalize(data, raw)
        fact = result['ledger_claims'][0]
        self.assertEqual(fact['verdict'], 'supported')
        self.assertEqual(fact['checks'][0]['observed'], 1)
        self.assertEqual(fact['snapshot_sha256'], data['ledger_snapshot']['sha256'])
        block = roles.blocks(data['draft']['content'])[0]
        self.assertEqual(block['text'][fact['start']:fact['end']], fact['text'])
        self.assertFalse(verification.blocking(result))

    def test_ledger_fact_is_traceable_without_a_literature_citation(self):
        workflow, data, raw = self.case()
        block = roles.blocks(data['draft']['content'])[0]
        block.update(kind='ledger_fact', evidence_ids=[])
        result = verification.normalize(data, raw)
        self.assertEqual(result['ledger_claims'][0]['verdict'], 'supported')
        self.assertFalse(verification.blocking(result))
        draft_data = workflow.data_for('draft')
        draft = copy.deepcopy(workflow.current_artifact('draft')['content'])
        target = roles.blocks(draft)[0]
        target.update(kind='ledger_fact', evidence_ids=[], text='The ledger records one search.')
        roles.validate_semantics('draft', draft_data, draft)
        for bad in ('missing_snapshot', 'literature_citation', 'uncited_literature_fact'):
            d, r = copy.deepcopy(draft_data), copy.deepcopy(draft)
            b = roles.blocks(r)[0]
            if bad == 'missing_snapshot': d.pop('ledger_snapshot')
            elif bad == 'literature_citation': b['evidence_ids'] = list(d['context']['evidence'])[:1]
            else: b['kind'] = 'fact'
            with self.subTest(bad=bad), self.assertRaises(ResearchError):
                roles.validate_semantics('draft', d, r)
        path = self.root / 'ledger-draft.json'
        path.write_text(json.dumps(draft))
        imported = workflow.import_draft(path, 'Explicit record-based statement')
        self.assertEqual(roles.blocks(imported['content'])[0]['kind'], 'ledger_fact')
        self.assertIsNone(workflow.current_verification())

    def test_ledger_fact_cannot_skip_extraction(self):
        _, data, raw = self.case()
        roles.blocks(data['draft']['content'])[0]['kind'] = 'ledger_fact'
        raw['ledger_claims'] = []
        for item in raw['ledger_coverage']:
            item.update(status='no_facts')
        result = verification.normalize(data, raw)
        self.assertTrue(verification.blocking(result))
        self.assertEqual(result['ledger_coverage'][0]['status'], 'incomplete')

    def test_wrong_unknown_and_type_mismatched_records_block_completion(self):
        _, data, raw = self.case()
        for path, expected, verdict in [('/search_count', '2', 'contradicted'), ('/search_count', 'true', 'contradicted'), ('/not_recorded', '1', 'undetermined')]:
            changed = copy.deepcopy(raw)
            changed['ledger_claims'][0]['checks'][0].update(path=path, expected_json=expected)
            result = verification.normalize(data, changed)
            self.assertEqual(result['ledger_claims'][0]['verdict'], verdict)
            self.assertTrue(any(f.get('ledger') for f in verification.blocking(result)))

    def test_invalid_span_snapshot_and_duplicate_ids_rejected(self):
        _, data, raw = self.case()
        for corrupt in ('span', 'hash', 'duplicate', 'coverage'):
            d, r = copy.deepcopy(data), copy.deepcopy(raw)
            if corrupt == 'span': r['ledger_claims'][0]['text'] = 'Invented text'
            if corrupt == 'hash': d['ledger_snapshot']['records']['search_count'] += 1
            if corrupt == 'duplicate': r['ledger_claims'].append(copy.deepcopy(r['ledger_claims'][0]))
            if corrupt == 'coverage': r['ledger_coverage'].pop()
            with self.subTest(corrupt=corrupt), self.assertRaises(ResearchError):
                verification.normalize(d, r)

    def test_membership_and_empty_universal_do_not_fake_support(self):
        _, data, raw = self.case()
        raw['ledger_claims'][0]['checks'] = [{'path': '/search_providers', 'operator': 'contains', 'expected_json': '"arxiv"'}]
        self.assertEqual(verification.normalize(data, raw)['ledger_claims'][0]['verdict'], 'supported')
        raw['ledger_claims'][0]['checks'] = [{'path': '/search_providers/0', 'operator': 'equals', 'expected_json': '"arxiv"'}]
        self.assertEqual(verification.normalize(data, raw)['ledger_claims'][0]['verdict'], 'supported')
        raw['ledger_claims'][0]['checks'] = [{'path': '/search_count', 'operator': 'all_equal', 'expected_json': '1'}]
        self.assertEqual(verification.normalize(data, raw)['ledger_claims'][0]['verdict'], 'undetermined')

    def test_bad_expected_value_is_undetermined_without_discarding_valid_assertions(self):
        _, data, raw = self.case()
        second = copy.deepcopy(raw['ledger_claims'][0])
        second['id'] = 'G002'
        second['checks'][0]['expected_json'] = '"P005"},{'
        raw['ledger_claims'].append(second)
        result = verification.normalize(data, raw)
        self.assertEqual(result['ledger_claims'][0]['verdict'], 'supported')
        self.assertEqual(result['ledger_claims'][1]['verdict'], 'undetermined')
        self.assertTrue(verification.blocking(result))
        second['checks'][0]['expected_json'] = 'NaN'
        self.assertEqual(verification.normalize(data, raw)['ledger_claims'][1]['verdict'], 'undetermined')

    def test_record_envelope_prefix_and_dictionary_membership(self):
        _, data, raw = self.case()
        raw['ledger_claims'][0]['checks'][0]['path'] = '/records/search_count'
        self.assertEqual(verification.normalize(data, raw)['ledger_claims'][0]['verdict'], 'supported')
        raw['ledger_claims'][0]['checks'] = [{'path': '/records/searches', 'operator': 'contains', 'expected_json': '"S001"'}]
        self.assertEqual(verification.normalize(data, raw)['ledger_claims'][0]['verdict'], 'supported')

    def test_web_search_configuration_cannot_prove_no_tool_use(self):
        _, data, raw = self.case()
        import hashlib
        block = roles.blocks(data['draft']['content'])[0]
        block['text'] = 'The author worked without tools.'
        raw['ledger_claims'][0]['text'] = block['text']
        data['ledger_snapshot']['records']['author_web_search_config'] = 'disabled'
        data['ledger_snapshot']['sha256'] = hashlib.sha256(ledger.canonical(data['ledger_snapshot']['records']).encode()).hexdigest()
        raw['ledger_claims'][0]['checks'] = [{'path': '/author_web_search_config', 'operator': 'equals', 'expected_json': '"disabled"'}]
        self.assertEqual(verification.normalize(data, raw)['ledger_claims'][0]['verdict'], 'undetermined')

    def test_author_input_snapshot_attests_omission_without_inventing_old_tool_counts(self):
        workflow, _, _ = self.case()
        data = workflow.data_for('verify')
        records = data['ledger_snapshot']['records']
        self.assertFalse(records['raw_search_responses_supplied'])
        self.assertNotIn('author_tool_call_count', records)
        self.assertEqual(records['author_web_search_config'], 'unrecorded')

    def test_disputed_evidence_remains_visible_in_record_snapshot(self):
        workflow, _, _ = self.case()
        context = workflow.context(only_claim=workflow.selected())
        eid = next(iter(context['evidence']))
        from loop_research.common import identifier
        with self.store.db:
            self.store.db.execute("UPDATE evidence SET status='disputed' WHERE id=?", (identifier(eid, 'E'),))
        context = workflow.context(only_claim=workflow.selected())
        self.assertNotIn(eid, context['evidence'])
        snap = ledger.snapshot(self.store, workflow.scope(), context)
        self.assertEqual(snap['records']['evidence_status'][eid], 'disputed')

    def test_empty_universal_check_is_undetermined(self):
        _, data, raw = self.case()
        import hashlib
        data['ledger_snapshot']['records']['empty'] = {}
        data['ledger_snapshot']['sha256'] = hashlib.sha256(ledger.canonical(data['ledger_snapshot']['records']).encode()).hexdigest()
        raw['ledger_claims'][0]['checks'] = [{'path': '/empty', 'operator': 'all_equal', 'expected_json': 'true'}]
        self.assertEqual(verification.normalize(data, raw)['ledger_claims'][0]['verdict'], 'undetermined')

    def test_snapshot_counts_failures_and_retains_document_reading_identity(self):
        workflow, _, _ = self.case()
        sid = self.store.start_search('openalex', 'failed', 1, 'https://fixture.invalid')
        self.store.finish_search(sid, error='Synthetic unavailable')
        snap = ledger.snapshot(self.store, workflow.scope(), workflow.context(only_claim=workflow.selected()))
        self.assertEqual(snap['records']['search_count'], 2)
        self.assertEqual(snap['records']['completed_search_count'], 1)
        self.assertTrue(snap['records']['readings'])
        self.assertTrue(snap['records']['documents']['D001']['sha256'])

    def test_repeated_issue_stops_after_two_distinct_manuscript_repairs(self):
        engine = PersistentReviewer()
        workflow, result = self.run_with(engine)
        self.assertEqual(result['state']['status'], 'needs_human')
        self.assertEqual(engine.calls.count('revise_draft'), 2)
        self.assertEqual(engine.calls.count('review'), 3)
        handoff = next(e for e in reversed(self.store.history()) if e['action'] == 'study.handoff')
        self.assertIn('I001', handoff['payload']['issue_ids'])
        self.assertEqual(handoff['payload']['draft_id'], result['current_draft'])
        self.assertTrue(workflow.repeated_review_blockers(workflow.latest_relevant_review()))
        with self.assertRaises(ResearchError):
            workflow.finalize('Cannot finalize unresolved work')
        output = self.root / 'export'
        export_study(self.store, output)
        packet = json.loads((output / 'ledger-verification.json').read_text())
        self.assertTrue(packet[0]['snapshot']['sha256'])

    def test_optimistic_review_cannot_hide_contradicted_ledger_claim(self):
        workflow, _ = self.run_with(FixtureEngine())
        original = workflow.engine.execute
        def with_record(kind, data, prompt, schema, on_spawn=None):
            raw, execution = original(kind, data, prompt, schema, on_spawn)
            if kind == 'draft':
                raw['abstract_blocks'][0]['text'] += ' The ledger records two searches.'
            if kind == 'verify':
                raw['ledger_claims'] = [{'id': 'G001', 'block_id': 'B001', 'field': 'text',
                                         'text': 'The ledger records two searches.', 'occurrence': 1,
                                         'checks': [{'path': '/search_count', 'operator': 'equals', 'expected_json': '2'}],
                                         'reason': 'Synthetic count assertion'}]
                raw['ledger_coverage'][0]['status'] = 'complete'
            return raw, execution
        with patch.object(workflow.engine, 'execute', side_effect=with_record):
            workflow.perform('draft', force=True)
            workflow.perform('verify', force=True)
            workflow.perform('review', force=True)
        review = workflow.latest_relevant_review()
        self.assertEqual(review['content']['decision'], 'revise')
        self.assertTrue(any(f['target_type'] == 'ledger_fact' and f['target_id'] == 'G001' and f['severity'] == 'major'
                            for f in review['content']['findings']))
        with self.assertRaises(ResearchError):
            workflow.finalize('Cannot finalize contradicted ledger')
        issue = next(i for i in self.store.list_issues() if i['content']['target_type'] == 'ledger_fact')
        content = dict(issue['content'], category='scope')
        from loop_research.common import identifier
        with self.store.db:
            self.store.db.execute('UPDATE issues SET content_json=? WHERE id=?', (json.dumps(content), identifier(issue['id'], 'I')))
        self.assertTrue(any(c['kind'] == 'revise_draft' for c in workflow.choices()))
        self.assertFalse(any(c['kind'] == 'redesign' for c in workflow.choices()))

    def test_substantive_guidance_breaks_old_handoff_basis(self):
        workflow, _ = self.run_with(PersistentReviewer())
        self.store.event('study.guidance', 'Reconsider the scope with a new methodological premise',
                         {'actor': 'human', 'question': self.store.study_state()['question']})
        self.store.db.commit()
        self.assertIsNone(workflow.current_verification())
        self.assertFalse(any(c['kind'] == 'consult' and c['parameters'].get('issue_ids') for c in workflow.choices()))

    def test_same_draft_rereview_does_not_count_as_repair(self):
        workflow, _ = self.run_with(FixtureEngine())
        workflow.perform('review', force=True)
        workflow.perform('review', force=True)
        self.assertEqual(workflow.repeated_review_blockers(workflow.latest_relevant_review()), [])
