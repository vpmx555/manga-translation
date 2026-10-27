import copy
# Historical contract test; requires the matching frozen source.
import tempfile
import unittest
from pathlib import Path

from manga_pipeline.storage.io import fingerprint
from manga_pipeline.v6.semantic_contract import contract, validate_target
from manga_pipeline.v6.translation import known_address_history


def row(mentions=None):
    return {"content_type": "dialogue", "speech_style": "normal", "speaker_person_id": None,
            "speaker_cluster_ref": None, "addressee_person_ids": [], "mentions": mentions or {}, "evidence": "source context"}


def decision():
    return {"confirmed_person_name": False, "mention_type": "unknown", "target_person_id": None,
            "recipient_cluster_ref": None, "visual_support": "unknown", "components": [], "evidence": "not a name"}


class ContractTests(unittest.TestCase):
    def test_locked_speakers_do_not_mutate_other_targets_or_name_references(self):
        value = contract(['a', 'b', 'c'], ['p1', 'p2'], [],
                         {'a': ['ma'], 'b': ['mb'], 'c': []})['properties']['utterances']['properties']
        value['a']['properties']['speaker_person_id']['enum'] = ['p1']
        value['b']['properties']['speaker_person_id']['enum'] = ['p2']
        for key, expected in [('a', ['p1']), ('b', ['p2']), ('c', ['p1', 'p2', None])]:
            self.assertEqual(value[key]['properties']['speaker_person_id']['enum'], expected)
        for key, mid in [('a', 'ma'), ('b', 'mb')]:
            mention = value[key]['properties']['mentions']['properties'][mid]
            self.assertEqual(mention['properties']['target_person_id']['enum'], ['p1', 'p2', None])

    def test_inference_first_merge_preserves_anchor_and_inferred_speaker(self):
        from manga_pipeline.v6.batch_contract import schema
        from manga_pipeline.v6.merge import resolve_speaker
        anchor = {'locked': True, 'person_id': 'p1'}
        wire = schema([{'id': 'a', 'speaker_anchor': anchor}], ['p1', 'p2'], {}, {})
        self.assertEqual(wire['properties']['utterances']['properties']['a']['properties']['speaker_person_id']['enum'], ['p1', 'p2', None])
        value = {'semantic_resolution': 'resolved', 'speaker_anchor': anchor,
                 'speaker_person_id': 'p2', 'speaker_cluster_ref': None, 'speaker_kind': 'person'}
        result = resolve_speaker(value, {'cluster_to_person': {}})
        self.assertEqual(result['speaker_person_id'], 'p2')
        self.assertEqual(result['speaker_anchor'], anchor)
        self.assertEqual(result['speaker_resolution']['selected_source'], 'inference')
        self.assertIn('source_anchor_disagrees', result['speaker_resolution']['conflicts'])

    def test_wire_owns_keys_per_target_and_empty_has_no_placeholder(self):
        schema = contract(["a", "b"], [], [], {"a": ["ma"], "b": []})
        props = schema["properties"]["utterances"]["properties"]
        self.assertEqual(props["a"]["properties"]["mentions"]["required"], ["ma"])
        self.assertEqual(props["b"]["properties"]["mentions"]["properties"], {})
        self.assertNotIn("no-candidates", str(schema))

    def test_cross_target_and_placeholder_are_rejected(self):
        mentions = {"ma": {"utterance_id": "a", "name": "Alice"}}
        for bad in [row({"ma": decision()}), row({"<no-candidates>": decision()})]:
            with self.assertRaisesRegex(ValueError, "coverage changed for b"):
                validate_target(bad, "b", mentions, [], {}, {})

    def test_honorific_is_not_a_surname(self):
        value = decision()
        value.update(confirmed_person_name=True, components=[{"kind": "surname", "value": "san"}])
        with self.assertRaisesRegex(ValueError, "Sentinel/suffix"):
            validate_target(row({"m": value}), "a", {"m": {"utterance_id": "a", "name": "Hiroi-san"}}, [], {}, {})

    def test_unknown_speakers_never_share_address_history(self):
        prior = [{"speaker_person_id": None}, {"speaker_person_id": "p1"},
                 {"speaker_person_id": "p1", "semantic_resolution": "unresolved"}]
        self.assertEqual(known_address_history(prior, [{"speaker_person_id": None}]), [])
        self.assertEqual(known_address_history(prior, [{"speaker_person_id": "p1"}]), [prior[1]])


if __name__ == "__main__":
    unittest.main()
