import json
from pathlib import Path
import unittest
from loop_research.common import ResearchError
from loop_research.engine import tool_observation
from loop_research.extraction_eval import score


class ClassificationAcceptance(unittest.TestCase):
    def test_developer_cases_are_scored_without_claiming_human_truth(self):
        cases = json.loads(Path('benchmarks/extraction-cases.json').read_text())['cases']
        result = {'classifications': [{'id': c['id'], 'category': c['expected_class']} for c in cases]}
        report = score(cases, result)
        self.assertEqual(report['matches'], 11)
        self.assertIn('not independent', report['notice'])
        result['classifications'][0]['category'] = 'ledger'
        self.assertEqual(score(cases, result)['matches'], 10)

    def test_duplicate_missing_and_unknown_case_ids_are_rejected(self):
        cases = [{'id': 'a', 'expected_class': 'uncertainty'}]
        for rows in ([], [{'id': 'x', 'category': 'uncertainty'}], [{'id': 'a', 'category': 'uncertainty'}] * 2):
            with self.assertRaises(ResearchError):
                score(cases, {'classifications': rows})

    def test_tool_events_are_deduplicated_without_saving_commands(self):
        events = [{'type': kind, 'item': {'id': 'tool1', 'type': 'command_execution', 'command': 'sensitive synthetic string'}}
                  for kind in ('item.started', 'item.completed')]
        observation = tool_observation(events)
        self.assertEqual(observation['known_tool_items'], 1)
        self.assertNotIn('sensitive', str(observation))
        observation = tool_observation([{'type': 'item.completed', 'item': {'type': 'new_unknown_tool'}}])
        self.assertTrue(observation['unknown_item_types'])
        self.assertIn('item_event_inventory_unavailable', tool_observation([])['unknown_item_types'])
