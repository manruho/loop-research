import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from loop_research import ledger, verification
from loop_research.assertion_spans import scope_loss


class ScopePreservation(unittest.TestCase):
    def normalize(self, text, span, records, checks):
        snapshot = {'records': records, 'sha256': hashlib.sha256(ledger.canonical(records).encode()).hexdigest()}
        raw = {'id': 'G001', 'block_id': 'B001', 'field': 'text', 'text': span,
               'occurrence': 1, 'checks': checks, 'reason': 'Fixture'}
        return ledger.normalize({'ledger_snapshot': snapshot}, [raw], {'B001': {'text': text}})[0]

    def test_saved_model_fragments_do_not_become_supported(self):
        fixture = json.loads(Path('benchmarks/scope-loss-response.json').read_text())
        for assertion in fixture['assertions']:
            span = assertion['text']
            result = self.normalize(fixture['text'], span, {'author_tool_call_count': 0},
                                    [{'path': '/author_tool_call_count', 'operator': 'equals', 'expected_json': '0'}])
            self.assertEqual(result['verdict'], 'undetermined', span)
            if span in ('new searches', 'full-paper reading', 'independent source verification'):
                self.assertTrue(result['scope_check']['omitted_cues'])
            self.assertEqual(fixture['text'][result['start']:result['end']], span)
            self.assertEqual(result['checks'][0]['observed'], 0)

    def test_intact_absence_is_not_proven_by_zero_observed_tools(self):
        text = 'No tools, new searches, full-paper reading, or independent source verification.'
        result = self.normalize(text, text, {'author_tool_call_count': 0},
                                [{'path': '/author_tool_call_count', 'operator': 'equals', 'expected_json': '0'}])
        self.assertFalse(result['scope_check']['omitted_cues'])
        self.assertEqual(result['verdict'], 'undetermined')

    def test_narrow_recorded_count_remains_supported(self):
        text = 'The ledger records no tools.'
        result = self.normalize(text, text, {'author_tool_call_count': 0},
                                [{'path': '/author_tool_call_count', 'operator': 'equals', 'expected_json': '0'}])
        self.assertEqual(result['verdict'], 'supported')

    def test_conditions_modality_and_time_are_preserved(self):
        for text, span in [('If supervised, the system improves accuracy.', 'the system improves accuracy'),
                           ('The system may improve accuracy.', 'improve accuracy'),
                           ('Previously, the trial reported improvement.', 'the trial reported improvement'),
                           ('Only adults were tested.', 'adults were tested')]:
            start = text.index(span)
            self.assertTrue(scope_loss(text, start, start + len(span))['omitted_cues'])
            self.assertFalse(scope_loss(text, 0, len(text))['omitted_cues'])
        text = 'No tools were used. The ledger records one search.'
        start = text.index('The ledger')
        self.assertFalse(scope_loss(text, start, len(text))['omitted_cues'])
        text = 'The ledger records one search. No tools were used.'
        self.assertFalse(scope_loss(text, 0, text.index('.') + 1)['omitted_cues'])

    def test_literature_fragment_blocks_completion_even_if_model_supports_it(self):
        text = 'The treatment did not improve accuracy.'
        block = {'id': 'B001', 'text': text, 'qualification': '', 'kind': 'fact', 'evidence_ids': []}
        snapshot = {'records': {}, 'sha256': hashlib.sha256(ledger.canonical({}).encode()).hexdigest()}
        data = {'draft': {'content': {}}, 'ledger_snapshot': snapshot, 'source_spans': {}}
        raw = {'facts': [{'id': 'F001', 'block_id': 'B001', 'field': 'text', 'text': 'improve accuracy',
                         'occurrence': 1, 'sources': [], 'verdict': 'supported', 'reason': 'Optimistic fixture', 'qualification': ''}],
               'ledger_claims': [], 'coverage': [{'block_id': 'B001', 'status': 'complete', 'reason': 'Fixture'}],
               'ledger_coverage': [{'block_id': 'B001', 'status': 'no_facts', 'reason': 'Fixture'}], 'limitations': ['Fixture']}
        with patch('loop_research.roles.verification_blocks', return_value=[block]):
            result = verification.normalize(data, raw)
        fact = result['facts'][0]
        self.assertEqual(fact['model_verdict'], 'supported')
        self.assertEqual(fact['verdict'], 'undetermined')
        self.assertTrue(verification.blocking(result))
        self.assertIn('not', [c['text'] for c in fact['scope_check']['omitted_cues']])
