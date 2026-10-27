"""Selector recovery and semantic alias coverage without model inference."""
# Historical contract test; requires the matching frozen source.
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manga_pipeline.v6 import name_normalization as names, scene


class SelectorTests(unittest.TestCase):
    def run_normalize(self, rows, extracted, patches=(), groups=()):
        mentions = scene.validate_names({'names': extracted}, rows)
        return names.process({'patches': list(patches), 'groups': list(groups), 'suspicions': []}, rows, mentions, {})

    def test_real_off_by_one_patch_resolves_detected_name_only(self):
        rows = [{'id': 'a', 'text': "IT'S SASA- MORI- SAN!"}]
        p = {'utterance_id': 'a', 'start': 4, 'end': 19, 'original': 'SASA- MORI- SAN',
             'replacement': 'SASAMORI-SAN', 'status': 'confirmed',
             'evidence': 'Identical letters, spacing repair.', 'evidence_refs': ['a']}
        result = self.run_normalize(rows, [{'utterance_id': 'a', 'name': p['original']}], [p])
        self.assertEqual((result['patches'][0]['start'], result['patches'][0]['end']), (5, 20))
        self.assertEqual(names.apply({'utterances': rows}, result)['utterances'][0]['text'], "IT'S SASAMORI-SAN!")
        self.assertTrue(result['repairs'])

    def test_repeated_surface_requires_selector_index(self):
        rows = [{'id': 'a', 'text': '😀 SASA- MORI, SASA- MORI!'}]
        extracted = [{'utterance_id': 'a', 'name': 'SASA- MORI'}]
        base = {'utterance_id': 'a', 'original': 'SASA- MORI', 'replacement': 'SASAMORI',
                'status': 'confirmed', 'evidence': 'Spacing only.', 'evidence_refs': ['a']}
        ambiguous = self.run_normalize(rows, extracted, [base])
        self.assertTrue(ambiguous['rejected'])
        result = self.run_normalize(rows, extracted, [{**base, 'occurrence_index': 1}])
        self.assertEqual(names.apply({'utterances': rows}, result)['utterances'][0]['text'], '😀 SASA- MORI, SASAMORI!')

    def test_names_group_by_source_selectors_without_offsets(self):
        rows = [{'id': 'a', 'text': 'SASAMORI SAN'}, {'id': 'b', 'text': 'KOYUKI'},
                {'id': 'c', 'text': 'SASAMORI KOYUKI'}]
        extracted = [{'utterance_id': r['id'], 'name': r['text']} for r in rows]
        group = {'group_id': 'person-name-1', 'canonical_name': 'SASAMORI KOYUKI',
                 'members': [{**v, 'occurrence_index': 0} for v in extracted],
                 'evidence': 'Source introduction connects surname and given name.', 'evidence_refs': ['c']}
        result = self.run_normalize(rows, extracted, groups=[group])
        self.assertEqual(len(result['groups']), 1)
        self.assertTrue(result['grouping_coverage']['complete'])
        self.assertEqual(result['groups'][0]['members'][0]['start'], 0)
        self.assertEqual(result['groups'][0]['status'], 'proposed')

    def test_honorific_separator_repair_does_not_change_letters(self):
        rows = [{'id': 'a', 'text': 'SASAMORI SAN'}]
        p = {'utterance_id': 'a', 'occurrence_index': 0, 'original': rows[0]['text'],
             'replacement': 'SASAMORI-SAN', 'status': 'confirmed', 'evidence': 'Separator only.', 'evidence_refs': ['a']}
        result = self.run_normalize(rows, [{'utterance_id': 'a', 'name': rows[0]['text']}], [p])
        self.assertEqual(result['patches'][0]['status'], 'applied')
        self.assertEqual(result['rejected'], [])

    def test_joint_scan_feedback_rejects_silently_empty_alias_groups(self):
        rows = [{'id': 'source-row', 'text': 'Sasamori'}]
        class Calls:
            count = 0
            prompts = []
            def call(self, key, task, prompt, request, schema, **kwargs):
                self.count += 1
                self.prompts.append(prompt)
                groups = [] if self.count == 1 else [{'group_id': 'g', 'canonical_name': 'Sasamori',
                    'members': [{'utterance_id': 'u0', 'name': 'Sasamori', 'occurrence_index': 0}],
                    'evidence': 'One explicit source name.', 'evidence_refs': ['u0']}]
                return {'scenes': [{'start_id': 'u0', 'summary': 'A name is spoken.'}],
                        'names': [{'utterance_id': 'u0', 'name': 'Sasamori'}],
                        'name_normalization': {'patches': [], 'groups': groups, 'suspicions': []}}
        calls = Calls()
        _, _, result = scene.plan(rows, calls, rows, {})
        self.assertEqual(calls.count, 2)
        self.assertIn('Alias grouping incomplete', calls.prompts[1])
        self.assertTrue(result['grouping_coverage']['complete'])


if __name__ == '__main__':
    unittest.main()
