import copy
import json
from pathlib import Path
import tempfile
import unittest
from loop_research.common import ResearchError
from loop_research.verification_eval import score, run


class VerificationRegression(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(Path('benchmarks/verification-cases.json').read_text())['cases']
        self.answer = {'cases': [dict(id=c['id'], assertions=[dict(text=t['text'], kind=t['kind'], verdict=t['verdict'], reason='Fixture expectation') for t in c['expected']]) for c in self.cases]}

    def test_exact_targets_and_semantics_are_separate(self):
        report = score(self.cases, self.answer)
        self.assertEqual(report['correct'], report['required'])
        self.answer['cases'][0]['assertions'] = []
        self.answer['cases'][1]['assertions'][0]['verdict'] = 'supported'
        report = score(self.cases, self.answer)
        self.assertEqual(report['extracted'], report['required'] - 1)
        self.assertEqual(report['correct'], report['required'] - 2)

    def test_whole_paragraph_does_not_mask_comparison_omission(self):
        self.answer['cases'][0]['assertions'][0]['text'] = self.cases[0]['text']
        self.assertEqual(score(self.cases, self.answer)['extracted'], 8)

    def test_terminal_period_is_optional_but_negation_is_required(self):
        for row in self.answer['cases']:
            for assertion in row['assertions']:
                assertion['text'] = assertion['text'].removesuffix('.')
        self.assertEqual(score(self.cases, self.answer)['correct'], 9)
        self.answer['cases'][1]['assertions'][1]['text'] = 'independent source verification'
        self.assertEqual(score(self.cases, self.answer)['extracted'], 8)

    def test_explicit_full_negative_sentence_alternative_is_accepted(self):
        sentence = self.cases[1]['text']
        self.answer['cases'][1]['assertions'] = [dict(text=sentence, kind='ledger', verdict='undetermined', reason='All components unestablished')]
        self.assertEqual(score(self.cases, self.answer)['correct'], 9)

    def test_invalid_ids_and_spans_are_rejected(self):
        variants = [dict(cases=[]), dict(cases=self.answer['cases'] * 2)]
        invalid = copy.deepcopy(self.answer)
        invalid['cases'][0]['assertions'][0]['text'] = 'Invented quotation'
        variants.append(invalid)
        for result in variants:
            with self.assertRaises(ResearchError):
                score(self.cases, result)

    def test_saved_result_preserves_inputs_and_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            anonymous = copy.deepcopy(self.answer)
            for i, row in enumerate(anonymous['cases'], 1):
                row['id'] = f'case{i:03d}'
            (path/'answer.json').write_text(json.dumps(anonymous))
            report = run(path/'out', result_path=path/'answer.json')
            self.assertEqual(report['correct'], 9)
            payload = json.loads((path/'out/input.json').read_text())
            self.assertNotIn('expected', str(payload))
            self.assertNotIn('annotation_reason', str(payload))
            self.assertEqual(json.loads((path/'out/manifest.json').read_text())['status'], 'completed')
            anonymous['cases'] = []
            (path/'answer.json').write_text(json.dumps(anonymous))
            with self.assertRaises(ResearchError):
                run(path/'failed', result_path=path/'answer.json')
            self.assertEqual(json.loads((path/'failed/manifest.json').read_text())['status'], 'failed')
            self.assertTrue((path/'failed/model-result.json').exists())
