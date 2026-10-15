"""Comparison protocol, failure-inclusive accounting and human-score provenance."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from loop_research import evaluation as e
from loop_research.budget import WORKING_DRAFT_WORDS, COMPACT
from loop_research.store import ResearchStore


class EvaluationIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self, runs, cases=1):
        case = json.loads(Path('benchmarks/benchmark-reporting-pilot.json').read_text())['cases'][0]
        return {'cases': {'cases': [dict(case, id=f'case-{i}') for i in range(cases)]},
                'methods': ['baseline', 'harness'], 'runs': runs, 'human_evaluation': 'pending',
                'limitations': ['Synthetic comparison'], 'evaluation_contract': 2}

    def test_same_word_target_and_withdrawal_are_recorded(self):
        case = {'question': 'Synthetic?', 'seed': {'title': 'Synthetic', 'arxiv': '1234.12345v1'}}
        process = MagicMock(returncode=0)
        def communicate(prompt, timeout):
            (self.root / 'draft.md').write_text('NO_PAPER: withdraw\nUnjustified thesis.')
            return json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 2}}), ''
        process.communicate.side_effect = communicate
        with patch.object(e, 'codex_status', return_value={'chatgpt_authenticated': True}), patch.object(e.subprocess, 'Popen', return_value=process):
            result = e.baseline(case, self.root, 'writer', 600)
        self.assertEqual(WORKING_DRAFT_WORDS, (600, 900))
        self.assertIn('600-900 words', (self.root / 'prompt.txt').read_text())
        self.assertIn('600-900 word', COMPACT['draft'])
        self.assertEqual(result['status'], 'withdrawn')
        self.assertFalse(e.manuscript_available(self.root))

    def test_failed_draft_is_still_in_blind_packet_and_denominator(self):
        runs = [{'case_id': 'case-0', 'method': method, 'status': status, 'path': method}
                for method, status in [('baseline', 'failed'), ('harness', 'interrupted')]]
        manifest = self.manifest(runs)
        for run in runs:
            folder = self.root / run['path']; folder.mkdir()
            if run['method'] == 'baseline': (folder / 'draft.md').write_text('The experiment tested adults.')
        e.save(self.root / 'manifest.json', manifest)
        engine = MagicMock()
        engine.execute.return_value = ({'facts': ['The experiment tested adults.'], 'limitations': ['Synthetic']}, {})
        with patch.object(e, 'CodexEngine', return_value=engine): e.packet(self.root, 'reviewer')
        samples = e.read(self.root / 'blind-packet/scores.json')['samples']
        self.assertEqual(sum(s['manuscript_available'] for s in samples), 1)
        self.assertEqual(engine.execute.call_count, 1)
        e.report(self.root)
        report = e.read(self.root / 'report.json')
        self.assertEqual(report['method_summary']['baseline']['milestones']['draft']['reached'], 1)
        self.assertEqual(report['method_summary']['baseline']['milestones']['verify']['unknown'], 1)
        self.assertEqual(report['method_summary']['harness']['status_counts'], {'interrupted': 1})
        self.assertIsNone(report['human_fact_support']['supported_fraction_all_factual'])

    def test_failed_database_retains_durations_usage_and_accepted_milestones_readonly(self):
        folder = self.root / 'harness';folder.mkdir()
        store = ResearchStore(folder / 'study.sqlite3', initialize=True)
        store.init('Synthetic')
        with store.db:
            for kind, status, end, usage in [('draft', 'completed', '2026-10-15T00:00:04+00:00', {'input_tokens': 4}),
                                             ('verify', 'failed', '2026-10-15T00:00:03+00:00', None),
                                             ('review', 'interrupted', None, None)]:
                store.db.execute('INSERT INTO tasks(kind,semantic_key,status,input_json,prompt,schema_json,config_json,execution_json,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                                 (kind,kind,status,'{}','','{}','{}',json.dumps({'usage': usage}),'2026-10-15T00:00:00+00:00',end))
            store.db.execute("INSERT INTO artifacts(kind,semantic_key,content_json,input_json,created_at) VALUES('draft','x','{}','{}','2026-10-15T00:00:04+00:00')")
        store.close()
        database = folder / 'study.sqlite3'
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        case = self.manifest([])['cases']['cases'][0]
        metrics = e.metrics(self.root, {'path': 'harness'}, case)
        self.assertEqual(metrics['milestones'], {'draft': True, 'verify': False, 'review': False})
        phases = metrics['phase_metrics']
        self.assertEqual(phases['phases']['verify']['failed'], 1)
        self.assertEqual(phases['phases']['review']['duration_unavailable_calls'], 1)
        self.assertEqual(phases['reported_usage'], {'input_tokens': 4})
        self.assertEqual(phases['usage_unavailable_calls'], 2)
        self.assertEqual(before, hashlib.sha256(database.read_bytes()).hexdigest())

    def test_unrecorded_attempts_and_uninstrumented_stages_are_unknown(self):
        manifest = self.manifest([], cases=3)
        summary = e.method_summary(manifest, [])
        self.assertEqual(summary['harness']['milestones']['review']['unknown'], 3)
        self.assertEqual(summary['harness']['declared_runs'], 3)

    def test_human_support_denominators_do_not_drop_uncertainty(self):
        result = e.human_fact_support([{'facts': [{'verdict': v} for v in
                                  ['supported', 'partial', 'contradicted', 'undetermined', 'not_factual']]}])
        self.assertEqual(result['sampled_factual_assertions'], 4)
        self.assertEqual(result['supported_fraction_all_factual'], .25)
        self.assertAlmostEqual(result['supported_fraction_assessable'], 1/3)

    def test_protocol_records_conditions_and_freezes_ablation_on_resume(self):
        def fake(case, folder, *args): return {'status': 'held'}
        with patch.object(e, 'baseline', side_effect=fake), patch.object(e, 'harness', side_effect=fake), contextlib.redirect_stdout(io.StringIO()):
            e.run_evaluation(self.root/'eval', 'benchmarks/benchmark-reporting-pilot.json', 'writer', 'reviewer', 600)
        m = e.read(self.root/'eval/manifest.json')
        self.assertEqual(m['conditions']['manuscript_words'], [600, 900])
        self.assertEqual(m['evaluation_contract'], 2)
        self.assertIn('Different search tools', m['conditions']['remaining_confounds'][0])

    def test_human_evaluator_provenance_is_preserved_without_certifying_independence(self):
        runs = [{'case_id': 'case-0', 'method': method, 'status': 'failed', 'path': method}
                for method in ('baseline', 'harness')]
        manifest = self.manifest(runs)
        samples = []
        mapping = {}
        for index, run in enumerate(runs, 1):
            folder = self.root / run['path']; folder.mkdir()
            code = f'Sample-{index:02d}'
            available = index == 1
            if available: (folder/'draft.md').write_text('The trial tested adults.')
            samples.append({'sample_id': code, 'manuscript_available': available,
                            'scores': {k: 3 if available else None for k in e.RUBRIC},
                            'facts': [{'id': 'Fact-01', 'text': 'The trial tested adults.',
                                       'verdict': 'supported', 'source_locator': 'Synthetic primary source p.1',
                                       'reason': 'Synthetic human-score fixture'}] if available else []})
            mapping[code] = {'case_id': run['case_id'], 'method': run['method']}
        e.save(self.root/'manifest.json', manifest)
        e.save(self.root/'private-score-template.json', {'samples': samples})
        e.save(self.root/'private-sample-map.json', mapping)
        e.save(self.root/'submitted.json', {'samples': samples, 'evaluator': {
            'id': 'fixture-reviewer', 'relationship_to_project': 'independent', 'assistance': 'none (synthetic fixture)'}})
        e.import_scores(self.root, self.root/'submitted.json')
        report = e.read(self.root/'report.json')
        self.assertEqual(report['evaluator']['relationship_to_project'], 'independent')
        self.assertIn('not independently verified', report['evaluator_independence'])
        self.assertEqual(report['human_fact_support_by_method']['baseline']['supported_fraction_all_factual'], 1)
        self.assertIsNone(report['human_fact_support_by_method']['harness']['supported_fraction_all_factual'])

    def test_setup_consumes_common_time_budget_without_starting_a_model(self):
        case = {'question': 'Synthetic?', 'seed': {'title': 'Synthetic', 'arxiv': '1234.12345v1'}}
        with patch.object(e, 'codex_status', return_value={'chatgpt_authenticated': True}), \
                patch.object(e.time, 'monotonic', side_effect=[0, 2]), patch.object(e.subprocess, 'Popen') as popen:
            with self.assertRaises(e.subprocess.TimeoutExpired): e.baseline(case, self.root, 'writer', 1)
            popen.assert_not_called()
        folder = self.root/'harness';folder.mkdir()
        with patch.object(e.time, 'monotonic', side_effect=[0, 2, 3]), \
                patch('loop_research.workflow.ResearchWorkflow.run') as run:
            result = e.harness(case, folder, 'writer', 'reviewer', 1)
        run.assert_not_called()
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(result['phase_metrics']['phases'], {})

    def test_later_failure_preserves_accepted_draft_and_original_exception(self):
        case = {'question': 'Synthetic?', 'seed': {'title': 'Synthetic', 'arxiv': '1234.12345v1'}}
        content = {'title': 'Synthetic trial', 'abstract_blocks': [
            {'id': 'B001', 'text': 'The trial tested adults.', 'evidence_ids': [], 'claim_ids': [], 'qualification': '', 'kind': 'fact'}],
            'sections': [], 'uncertainties': ['Synthetic only']}
        def fail_after_draft(workflow, *args, **kwargs):
            with workflow.store.db:
                workflow.store._artifact('draft', 'synthetic', None, {}, content)
            raise OSError('Synthetic failure after accepted draft')
        with patch('loop_research.workflow.ResearchWorkflow.run', new=fail_after_draft):
            with self.assertRaisesRegex(OSError, 'Synthetic failure after accepted draft'):
                e.harness(case, self.root, 'writer', 'reviewer', 60)
        self.assertTrue(e.manuscript_available(self.root))
        self.assertIn('Historical/outdated', (self.root/'draft.md').read_text())
        self.assertFalse(e.read(self.root/'manuscript-provenance.json')['current'])

    def test_resume_retains_abrupt_attempt_and_recovers_saved_text_without_rerun(self):
        output = self.root/'eval'
        folder = output/'benchmark-reporting-harness';folder.mkdir(parents=True)
        store = ResearchStore(folder/'study.sqlite3', initialize=True)
        store.init('Synthetic interrupted research')
        content = {'title': 'Synthetic', 'abstract_blocks': [
            {'id': 'B001', 'text': 'A provisional argument.', 'kind': 'position',
             'evidence_ids': [], 'claim_ids': [], 'qualification': ''}], 'sections': [], 'uncertainties': ['Synthetic']}
        with store.db: store._artifact('draft', 'synthetic', None, {}, content)
        store.close()
        with patch.object(e, 'harness') as harness, contextlib.redirect_stdout(io.StringIO()):
            e.run_evaluation(output, 'benchmarks/benchmark-reporting-pilot.json', 'writer', 'reviewer', 60,
                             resume=True, methods=['harness'])
        harness.assert_not_called()
        run = e.read(output/'manifest.json')['runs'][0]
        self.assertEqual(run['status'], 'interrupted')
        self.assertTrue(e.manuscript_available(folder))
        self.assertFalse(e.read(folder/'manuscript-provenance.json')['current'])

    def test_phase_accounting_reads_each_task_once(self):
        store = MagicMock()
        store.list_tasks.return_value = [{'id': 'T001'}, {'id': 'T002'}]
        store.show_task.return_value = {'kind': 'reading', 'status': 'completed',
            'started_at': '2026-10-15T00:00:00+00:00', 'finished_at': '2026-10-15T00:00:01+00:00', 'execution': None}
        result = e.phase_metrics(store)
        self.assertEqual(store.show_task.call_count, 2)
        self.assertEqual(store.list_tasks.call_count, 1)
        self.assertEqual(result['phases']['reading']['calls'], 2)
        self.assertEqual(result['usage_unavailable_calls'], 2)
