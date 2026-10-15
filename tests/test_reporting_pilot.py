"""Regressions from the bounded real-literature pilot and earlier ledger replay."""
import contextlib
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loop_research import ledger, selection
from loop_research.evaluation import identifiers
from loop_research.schema import validate
from loop_research.store import ResearchStore
from loop_research.workflow import ResearchWorkflow
from .fixtures import FixtureEngine, fake_search


class ReportingPilot(unittest.TestCase):
    def test_available_material_is_acquired_before_citation_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResearchStore(Path(directory) / 'study.sqlite3', initialize=True)
            try:
                store.init('Synthetic checking workload')
                store.configure_study('Isolate scheduling', settings={'semantic_selection': 0, 'citation_seeds': 1})
                w = ResearchWorkflow(store, FixtureEngine())
                with contextlib.redirect_stderr(io.StringIO()): w.perform('orientation')
                fake_search(store, 'synthetic workload', 'arxiv', 1)
                observed = []
                def execute(choice):
                    observed.append(choice['kind'])
                    store.control_study('held', 'Stop fixture after chosen action')
                with patch.object(w, 'execute_choice', side_effect=execute), contextlib.redirect_stderr(io.StringIO()):
                    w.run(1)
                self.assertEqual(observed, ['fetch'])
            finally: store.close()

    def test_long_document_selection_is_bounded_and_includes_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResearchStore(Path(directory) / 'study.sqlite3', initialize=True)
            try:
                store.init('Synthetic checking workload')
                fake_search(store, 'synthetic workload', 'arxiv', 1)
                pages = [f'Synthetic page {i}' for i in range(1, 101)]
                store.save_document(None, 'P001', '\n'.join(pages).encode(), 'text', 'https://fixture.invalid', pages, 'synthetic')
                data = selection.data(store, [], 'D001')
                self.assertLessEqual(len(data['candidates']), 16)
                self.assertEqual(data['candidates'][0]['page'], 1)
                self.assertEqual(data['candidates'][-1]['page'], 100)
                self.assertEqual(store.list_artifacts('reading'), [])
            finally: store.close()

    def test_historical_comparison_has_an_expressible_negative_check(self):
        text = 'The recorded current count differs from five.'
        raw = {'id': 'G001', 'block_id': 'B001', 'field': 'text', 'text': text, 'occurrence': 1,
               'checks': [{'path': '/search_count', 'operator': 'not_equals', 'expected_json': '5'}],
               'reason': 'Synthetic regression of an explicitly negated count, not an activity absence claim'}
        validate(raw, ledger.CLAIM)
        for value, expected in ((12, 'supported'), (5, 'contradicted'), (None, 'undetermined'), (True, 'undetermined')):
            records = {'search_count': value}
            data = {'ledger_snapshot': {'records': records, 'sha256': hashlib.sha256(ledger.canonical(records).encode()).hexdigest()}}
            result = ledger.normalize(data, [raw], {'B001': {'text': text}})
            self.assertEqual(result[0]['verdict'], expected)

    def test_html_and_acl_citations_are_not_silently_omitted(self):
        observed = identifiers('https://arxiv.org/html/2211.09110v2 https://aclanthology.org/2020.emnlp-main.393/')
        self.assertEqual(observed, {'arxiv:2211.09110', 'acl:2020.emnlp-main.393'})

    def test_negative_check_does_not_verify_a_positive_fragment_or_missing_record(self):
        for text, records, expected in [('Five search attempts', {'search_count': 12}, 'undetermined'),
                                        ('The current count differs from five.', {}, 'undetermined'),
                                        ('The current count differs from five.', {'search_count': 5.0}, 'undetermined')]:
            data = {'ledger_snapshot': {'records': records, 'sha256': hashlib.sha256(ledger.canonical(records).encode()).hexdigest()}}
            raw = {'id': 'G001', 'block_id': 'B001', 'field': 'text', 'text': text, 'occurrence': 1,
                   'checks': [{'path': '/search_count', 'operator': 'not_equals', 'expected_json': '5'}], 'reason': 'Synthetic comparison'}
            result = ledger.normalize(data, [raw], {'B001': {'text': text}})
            self.assertEqual(result[0]['verdict'], expected)
