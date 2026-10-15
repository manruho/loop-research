import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from loop_research import roles, selection, verification
from loop_research.common import ResearchError
from loop_research.evaluation import phase_metrics, selection_metrics, VARIANTS, run_evaluation, packet
from loop_research.store import ResearchStore
from loop_research.workflow import ResearchWorkflow
from loop_research.latex import render
from .fixtures import FixtureEngine, fake_search, fake_fetch


class ResearchImprovements(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ResearchStore(self.root / 'study.sqlite3', initialize=True)
        self.store.init('Synthetic checking workload')
        self.store.configure_study('Offline', settings={'citation_seeds': 0})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def full(self):
        w = ResearchWorkflow(self.store, FixtureEngine())
        with patch('loop_research.workflow.search_papers', fake_search), patch('loop_research.workflow.fetch_paper', fake_fetch), contextlib.redirect_stderr(io.StringIO()):
            result = w.run(80)
        self.assertEqual(result['state']['status'], 'ready_for_human')
        return w

    def test_semantic_selection_is_cached_without_becoming_a_reading(self):
        fake_search(self.store, 'synthetic', 'arxiv', 1)
        w = ResearchWorkflow(self.store, FixtureEngine())
        with contextlib.redirect_stderr(io.StringIO()): w.perform('selection')
        self.assertIsNotNone(w.cached_selection())
        with patch.dict(roles.PROMPTS, {'selection': roles.PROMPTS['selection'] + ' Changed contract.'}):
            self.assertIsNone(w.cached_selection())
        self.assertEqual(w.scope()['readings'], [])
        self.assertEqual(self.store.list_documents(), [])
        self.store.event('study.guidance', 'Human changes the research relevance premise', {'actor': 'human'})
        self.store.db.commit()
        self.assertIsNone(w.cached_selection())

    def test_structured_snippet_ablation_changes_cache_without_reading(self):
        fake_search(self.store, 'synthetic workload', 'arxiv', 1)
        text = 'Background. ' * 150 + '\nLimitations\nNo significant effect.\n' + 'Background. ' * 150
        self.store.save_document(None, 'P001', text.encode(), 'text', 'https://fixture.invalid/a', [text], 'synthetic')
        w = ResearchWorkflow(self.store, FixtureEngine())
        with contextlib.redirect_stderr(io.StringIO()): w.perform('selection', document='D001')
        self.assertIsNotNone(w.cached_selection('D001'))
        payload = w.data_for('selection', document='D001')
        self.assertIn('snippet_spans', payload['candidates'][0])
        self.assertEqual(self.store.list_artifacts('reading'), [])
        self.store.configure_study('Ablate snippets', settings={'structured_snippets': 0})
        self.assertIsNone(w.cached_selection('D001'))
        self.assertNotIn('No significant effect.', w.data_for('selection', document='D001')['candidates'][0]['snippet'])

    def test_selection_rejects_missing_duplicate_unknown_ids_and_keeps_counterwork(self):
        data = {'candidates': [{'id': 'P001'}, {'id': 'P002'}, {'id': 'P003'}]}
        raw = {'judgments': [{'candidate_id': cid, 'relevance': kind, 'reason': 'Synthetic'}
                             for cid, kind in [('P001', 'core'), ('P002', 'counter'), ('P003', 'unrelated')]],
               'limitations': ['Synthetic']}
        selection.validate(data, raw)
        self.assertEqual(selection.order(raw), ['P001', 'P002'])
        for ids in (['P001'], ['P001', 'P001', 'P003'], ['P001', 'P002', 'P999']):
            bad = copy.deepcopy(raw)
            bad['judgments'] = [dict(raw['judgments'][0], candidate_id=cid) for cid in ids]
            with self.assertRaises(ResearchError): selection.validate(data, bad)

    def test_duplicate_extractions_only_select_pages_from_newest_snapshot(self):
        fake_search(self.store, 'synthetic workload', 'arxiv', 1)
        self.store.save_document(None, 'P001', b'synthetic', 'text', 'https://fixture.invalid/a', ['Synthetic checking workload'], 'extractor-1')
        self.store.save_document(None, 'P001', b'synthetic', 'text', 'https://fixture.invalid/a', ['Synthetic checking workload'], 'extractor-2')
        w = ResearchWorkflow(self.store, FixtureEngine())
        with contextlib.redirect_stderr(io.StringIO()):
            w.perform('orientation')
            w.perform('selection')
        self.assertEqual(w.choices()[0]['parameters'], {'document': 'D002'})

    def test_semantic_selection_can_recover_different_terminology_and_counter_pages(self):
        self.store.configure_study('Domain', question='Language model benchmark reporting')
        self.store.add_paper({'canonical_key': 'fixture:envelope', 'provider': 'fixture', 'external_id': 'envelope',
                              'title': 'Operational envelopes for text generators', 'abstract': '',
                              'url': 'https://fixture.invalid/envelope', 'metadata': {}})
        w = ResearchWorkflow(self.store, FixtureEngine())
        self.assertEqual(w.lexical_papers(), [])
        with contextlib.redirect_stderr(io.StringIO()): w.perform('selection')
        self.assertEqual([p['id'] for p in w.ranked_papers()], ['P001'])
        pages = ['Language model benchmark ' * 20, 'A strong rival explanation.', 'Operational limits under shifted conditions.']
        self.store.save_document(None, 'P001', '\n'.join(pages).encode(), 'text', 'https://fixture.invalid/envelope', pages, 'synthetic')
        original = w.engine.execute
        def select(kind, data, prompt, schema, on_spawn=None):
            raw, execution = original(kind, data, prompt, schema, on_spawn)
            for item in raw['judgments']:
                item['relevance'] = {'D001:1': 'unrelated', 'D001:2': 'counter', 'D001:3': 'core'}[item['candidate_id']]
            return raw, execution
        with patch.object(w.engine, 'execute', side_effect=select), contextlib.redirect_stderr(io.StringIO()):
            w.perform('selection', document='D001')
        self.assertEqual(w.ranked_pages('D001'), [3, 2])
        self.assertEqual(w.scope()['readings'], [])

    def test_uncertainties_title_and_headings_cannot_be_omitted_from_verification(self):
        w = self.full()
        data = w.data_for('verify')
        raw, _ = FixtureEngine().execute('verify', data, '', {})
        targets = {b['id'] for b in data['verification_targets']}
        self.assertTrue({'MT001', 'MU001', 'MH001'} <= targets)
        for section in ('coverage', 'ledger_coverage'):
            bad = copy.deepcopy(raw)
            bad[section] = [b for b in bad[section] if b['block_id'] != 'MU001']
            with self.assertRaises(ResearchError): verification.normalize(data, bad)
        draft = w.current_artifact('draft')
        text, trace, _ = render(self.store, draft, 'ready_for_human', True)
        for row in trace['metadata_targets']:
            self.assertIn(row['block_id'], targets)
            from loop_research.latex import escape
            self.assertIn(escape(row['text']), '\n'.join(text.splitlines()[row['tex_first_line'] - 1:row['tex_last_line']]))

    def test_normalized_assertions_retain_context_and_unknown_usage(self):
        w = self.full()
        v = w.current_verification()['content']
        self.assertIn(v['facts'][0]['text'], v['assertion_contexts'][v['facts'][0]['context_id']])
        metrics = phase_metrics(self.store)
        self.assertGreater(metrics['calls_to_first_draft'], 0)
        self.assertGreater(metrics['usage_unavailable_calls'], 0)
        self.assertEqual(metrics['reported_usage'], {})
        data = w.data_for('verify')
        raw, _ = FixtureEngine().execute('verify', data, '', {})
        raw['facts'].append(dict(copy.deepcopy(raw['facts'][0]), id='F002'))
        normalized = verification.normalize(data, raw)
        self.assertEqual(len(normalized['assertion_contexts']), 1)
        self.assertEqual(normalized['facts'][0]['context_id'], normalized['facts'][1]['context_id'])

    def test_selection_recall_keeps_unknown_and_counter_recall_distinct(self):
        cases = [{'id': 'a', 'candidates': [{'id': 'x', 'label': 'core'}, {'id': 'y', 'label': 'counter'}, {'id': 'z', 'label': 'unrelated'}]}]
        row = selection_metrics(cases, {'a': ['x', 'z']}, 2)['cases'][0]
        self.assertEqual(row['recall_at_k'], .5)
        self.assertEqual(row['counter_recall_at_k'], 0)
        for ranked in ({'a': ['x', 'x']}, {'a': ['unknown']}, {}):
            with self.assertRaises(ResearchError): selection_metrics(cases, ranked, 2)

    def test_saved_ranking_evaluation_is_reproducible_and_does_not_overwrite(self):
        from loop_research.retrieval_eval import run
        cases = json.loads(Path('benchmarks/selection-cases.json').read_text())['cases']
        ranking = {c['id']: [p['id'] for p in c['candidates'] if p['label'] in ('core', 'counter')] for c in cases}
        path = self.root / 'ranking.json'
        path.write_text(json.dumps(ranking))
        dest = self.root / 'selection-eval'
        result = run(dest, 'benchmarks/selection-cases.json', ranking_path=path)
        self.assertTrue(all(c['recall_at_k'] == 1 for c in result['selected']['cases']))
        self.assertEqual(result['observations'], [])
        self.assertEqual(json.loads((dest / 'manifest.json').read_text())['status'], 'completed')
        with self.assertRaises(ResearchError): run(dest, 'benchmarks/selection-cases.json', ranking_path=path)

    def test_initial_search_wave_defers_without_consuming_remaining_queries(self):
        self.store.configure_study('Legacy selector isolates scheduling', settings={'semantic_selection': 0})
        class Queries(FixtureEngine):
            def execute(engine, kind, data, prompt, schema, on_spawn=None):
                raw, meta = super().execute(kind, data, prompt, schema, on_spawn)
                if kind == 'orientation':
                    raw['search_queries'] = [{'query': f'workload {i}', 'provider': 'arxiv', 'purpose': 'Synthetic'} for i in range(3)]
                return raw, meta
        w = ResearchWorkflow(self.store, Queries())
        with contextlib.redirect_stderr(io.StringIO()): w.perform('orientation')
        fake_search(self.store, 'workload 0', 'arxiv', 1)
        fake_search(self.store, 'workload 1', 'arxiv', 1)
        self.assertEqual([q['query'] for q in w.pending_queries()], ['workload 2'])
        choices = w.choices()
        self.assertTrue(any(c['kind'] == 'fetch' for c in choices))
        self.assertFalse(any(c['kind'] == 'search' for c in choices))
        self.assertEqual(len(self.store.list_searches()), 2)

    def test_safe_ablation_and_frozen_resume_conditions(self):
        methods = ['baseline', 'harness', 'harness-lexical']
        output = self.root / 'eval'
        def fake(case, root, *args):
            return {'status': 'held', 'elapsed_seconds': 1, 'usage': []}
        with patch('loop_research.evaluation.baseline', fake), patch('loop_research.evaluation.harness', fake), contextlib.redirect_stdout(io.StringIO()):
            run_evaluation(output, 'benchmarks/position-papers.json', 'writer', 'reviewer', 1, methods=methods)
            run_evaluation(output, 'benchmarks/position-papers.json', 'writer', 'reviewer', 1, resume=True, methods=methods)

        manifest = json.loads((output / 'manifest.json').read_text())
        self.assertEqual(len(manifest['runs']), 9)
        self.assertFalse(any('verify' in k or 'review' in k for v in VARIANTS.values() for k in v))
        packet(output, 'reviewer')
        self.assertEqual(len(json.loads((output / 'blind-packet/scores.json').read_text())['samples']), 9)
        manifest['source_sha256'] = {'changed': 'implementation'}
        (output / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ResearchError):
            run_evaluation(output, 'benchmarks/position-papers.json', 'writer', 'reviewer', 1, resume=True, methods=methods)

    def test_single_case_pilot_is_supported_without_duplicating_the_question(self):
        from loop_research.evaluation import load_cases
        spec = json.loads(Path('benchmarks/benchmark-reporting-pilot.json').read_text())
        self.assertEqual(len(load_cases('benchmarks/benchmark-reporting-pilot.json')['cases']), 1)
        for cases in ([], spec['cases'] * 2, [dict(spec['cases'][0], seed={})]):
            path = self.root / 'bad-cases.json'
            path.write_text(json.dumps({'cases': cases}))
            with self.assertRaises(ResearchError): load_cases(path)

    def test_checkpoint_reaches_draft_earlier_and_then_resumes_reading(self):
        outcomes = {}
        for checkpoint in (0, 2):
            store = ResearchStore(self.root / f'checkpoint-{checkpoint}.sqlite3', initialize=True)
            try:
                store.init('Synthetic checking workload')
                store.configure_study('Controlled ablation', settings={'semantic_selection': 0, 'citation_seeds': 0,
                                                                     'draft_checkpoint_readings': checkpoint, 'paper_limit': 4})
                def search(store, query, provider, limit):
                    sid = store.start_search(provider, query, limit, 'https://fixture.invalid/search')
                    papers = [{'canonical_key': f'synthetic:{i}', 'provider': 'fixture', 'external_id': str(i),
                               'title': f'Synthetic checking workload {i}', 'abstract': '', 'authors': [], 'metadata': {}}
                              for i in range(4)]
                    for paper in papers: paper['url'] = 'https://fixture.invalid/' + paper['external_id']
                    return store.finish_search(sid, papers, response={'synthetic': True})
                w = ResearchWorkflow(store, FixtureEngine())
                with patch('loop_research.workflow.search_papers', search), patch('loop_research.workflow.fetch_paper', fake_fetch), contextlib.redirect_stderr(io.StringIO()):
                    result = w.run(100)
                self.assertEqual(result['state']['status'], 'ready_for_human')
                tasks = store.list_tasks()
                first = next(i for i, t in enumerate(tasks) if t['kind'] == 'draft')
                outcomes[checkpoint] = sum(t['kind'] == 'reading' for t in tasks[:first])
                self.assertEqual(len(w.scope()['readings']), 4)
                self.assertIsNotNone(w.current_verification())
            finally: store.close()
        self.assertEqual(outcomes, {0: 4, 2: 2})
