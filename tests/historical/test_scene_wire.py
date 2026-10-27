"""Model-free compact wire regressions; no operational receipts or models."""
# Historical contract test; requires the matching frozen source.
import copy
import unittest
from manga_pipeline.v6 import scene_wire


class SceneWireTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'id': 'chapter:page:group:original', 'text': 'SASA- MORI - CHAN!'},
                     {'id': 'chapter:page:group:second', 'text': 'Hello.'}]
        self.request = {'targets': self.rows[:1], 'name_targets': self.rows,
                        'name_registry': [{'person_id': 'person:original', 'primary_name': 'Sasamori', 'aliases': []}]}
        self.schema = {'type': 'object', 'properties': {'utterance_id': {
            'type': 'string', 'enum': [r['id'] for r in self.rows]}}}

    def test_text_is_sent_once_and_source_enum_is_removed(self):
        request, schema, ledger = scene_wire.encode(self.request, self.schema, self.rows)
        self.assertNotIn('name_targets', request)
        self.assertEqual([r['text'] for r in request['targets']], [r['text'] for r in self.rows])
        self.assertEqual([r['scene_target'] for r in request['targets']], [True, False])
        self.assertNotIn('enum', schema['properties']['utterance_id'])
        self.assertEqual(ledger['rows']['u0'], self.rows[0]['id'])

    def test_only_references_are_decoded(self):
        _, _, ledger = scene_wire.encode(self.request, self.schema, self.rows)
        value = {'patches': [{'utterance_id': 'u0', 'original': 'u0', 'replacement': 'p0',
                             'start': 0, 'end': 19, 'evidence_refs': ['u1', 'p0']}]}
        source = copy.deepcopy(value)
        decoded = scene_wire.decode(value, ledger)['patches'][0]
        self.assertEqual(decoded['original'], 'u0')
        self.assertEqual(decoded['replacement'], 'p0')
        self.assertEqual(decoded['evidence_refs'], [self.rows[1]['id'], 'person:original'])
        self.assertEqual(value, source)
        for ref in ('u999', 'person:original', 'p0'):
            with self.assertRaises(ValueError):
                scene_wire.decode({'names': [{'utterance_id': ref}]}, ledger)

    def test_source_changes_invalidate_mapping(self):
        old = scene_wire.mapping(self.rows, [])
        changed = copy.deepcopy(self.rows)
        changed[0]['text'] += 'é'
        self.assertNotEqual(old['source_hash'], scene_wire.mapping(changed, [])['source_hash'])

    def test_invalid_name_retry_receives_wire_feedback_and_keeps_source(self):
        from manga_pipeline.v6 import scene
        rows = [{'id': 'source:row', 'text': 'SASA- MORI!'}]
        prompts = []
        class Calls:
            def call(self, key, task, prompt, request, schema, **kwargs):
                prompts.append(prompt)
                self_res = {'scenes': [{'start_id': 'u0', 'summary': 'A name is called.'}],
                            'names': [{'utterance_id': 'u0', 'name': 'TORU' if len(prompts) == 1 else 'SASA- MORI'}]}
                return self_res
        _, names = scene.plan(rows, Calls(), name_rows=rows)
        self.assertIn("'TORU' at u0", prompts[1])
        self.assertNotIn('source:row', prompts[1])
        self.assertEqual(names['source:row'][0]['name'], 'SASA- MORI')
        self.assertEqual(rows[0]['text'], 'SASA- MORI!')


if __name__ == '__main__':
    unittest.main()
