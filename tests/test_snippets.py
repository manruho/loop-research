import json
from pathlib import Path
import tempfile
import unittest
from loop_research import selection
from loop_research.snippet_eval import run
from loop_research.common import ResearchError


class BoundedSnippets(unittest.TestCase):
    def test_windows_are_bounded_exact_and_nonoverlapping(self):
        text = 'Background. '*150 + '\nLimitations\nNo significant effect was found.\n' + 'Background. '*150
        result = selection.snippet(text, {'effect'})
        spans = result['snippet_spans']
        self.assertEqual(result['snippet'], '\n[...]\n'.join(text[s['start']:s['end']] for s in spans))
        self.assertLessEqual(sum(s['end']-s['start'] for s in spans), 1200)
        self.assertTrue(all(a['end'] <= b['start'] for a,b in zip(spans, spans[1:])))
        self.assertIn('No significant effect was found.', result['snippet'])

    def test_short_text_is_sent_once_and_legacy_ablation_is_exact(self):
        text = 'a' * 240
        self.assertEqual(selection.snippet(text, set())['snippet'], text)
        self.assertEqual(selection.snippet(text, set(), False)['snippet'], text + '\n[...]\n' + text)

    def test_page_sample_preserves_bounds_and_determinism(self):
        pages = [{'page':i, 'text': 'Limitations' if i==42 else 'Background'} for i in range(1,101)]
        for limit in (1,2,3,16,60):
            observed = selection.page_sample(pages, limit, set())
            self.assertEqual(len(observed), limit)
            self.assertEqual(observed, selection.page_sample(pages, limit, set()))
            if limit > 1: self.assertEqual([observed[0]['page'], observed[-1]['page']], [1,100])
        self.assertIn(42, [p['page'] for p in selection.page_sample(pages, 16, set())])

    def test_offline_ablation_keeps_all_targets_and_bounded_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'output'
            report = run(root)
            self.assertGreater(report['totals']['structured']['targets_exposed'], report['totals']['edges']['targets_exposed'])
            self.assertLess(report['totals']['structured']['targets_exposed'], report['targets'])
            self.assertEqual(json.loads((root/'manifest.json').read_text())['model_calls'], 0)
            with self.assertRaises(ResearchError): run(root)
