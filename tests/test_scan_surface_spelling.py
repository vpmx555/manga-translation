"""Spelling-only OCR repair; no model, network or existing output mutation."""
import unittest
from types import SimpleNamespace

from manga_pipeline.v6 import scan_v3 as scan, semantic
from manga_pipeline.v6.name_normalization import apply
from manga_pipeline.v6.scene_wire import mapping


class SurfaceSpellingTests(unittest.TestCase):
    def parse(self, text, edits, canonical='Toru Mizushima'):
        document = {'utterances': [{'id': 'row', 'text': text, 'is_essential_text': True}]}
        value = {'scenes': [], 'groups': [{'group_id': 'g', 'canonical_name': canonical}],
                 'names': [{'text_id': 'u0', 'old_name': old, 'new_name': new, 'group_id': 'g'}
                           for old, new in edits]}
        result = scan.parse(value, document, document['utterances'], mapping(document['utterances'], []), {})
        self.assertEqual(result['policy'], 'canonical-scan-v4')
        return result, apply(document, result['name_normalization'])['utterances'][0]['text']

    def test_given_name_stays_short_full_name_and_group_stay_full(self):
        result, text = self.parse('TORU! Toru Mizushima.', [('TORU', 'Toru')])
        self.assertEqual(text, 'Toru! Toru Mizushima.')
        self.assertEqual(result['name_normalization']['groups'][0]['canonical_name'], 'Toru Mizushima')
        self.assertEqual(result['name_normalization']['patches'][0]['count'], 1)

    def test_model_full_name_expansion_is_not_applied_but_group_candidate_remains(self):
        result, text = self.parse('TORU! Toru Mizushima.', [('TORU', 'Toru Mizushima')])
        self.assertEqual(text, 'TORU! Toru Mizushima.')
        self.assertFalse(result['name_normalization']['replacement_rules'])
        issue = next(w for w in result['needs_review'] if w['reason'] == 'ocr_name_scope_change_not_applied')
        self.assertEqual(issue['proposed_new_name'], 'Toru Mizushima')
        self.assertEqual(issue['kind'], 'name_expansion')
        self.assertTrue(all(c['group_id'] == 'g' for c in result['candidates']))
        self.assertEqual(result['automatic_retries'], 0)

    def test_prefix_correction_only_matches_complete_typo(self):
        _, text = self.parse('Koyuk / Koyuki / Koyukii / _Koyuk / Koyuk2',
                             [('Koyuk', 'Koyuki')], 'Koyuki Sasamori')
        self.assertEqual(text, 'Koyuki / Koyuki / Koyukii / _Koyuk / Koyuk2')

    def test_suffix_alias_does_not_match_inside_given_name(self):
        _, text = self.parse('yuki-chan! Koyuki-chan!', [('yuki-chan', 'Koyuki-chan')], 'Koyuki Sasamori')
        self.assertEqual(text, 'Koyuki-chan! Koyuki-chan!')

    def test_truncated_honorific_does_not_match_complete_honorific(self):
        _, text = self.parse('MIZUSHIMA-SA / MIZUSHIMA-SAN', [('MIZU- SHIMA- SA', 'Mizushima-san')])
        self.assertEqual(text, 'Mizushima-san / MIZUSHIMA-SAN')
        self.assertNotIn('sann', text)

    def test_spacing_hyphen_repairs_preserve_existing_honorific(self):
        _, text = self.parse('sasa- mori - chan, SASA MORI CHAN!',
                             [('sasa- mori - chan', 'Sasamori-chan')], 'Koyuki Sasamori')
        self.assertEqual(text, 'Sasamori-chan, Sasamori-chan!')

    def test_adding_or_removing_name_parts_and_honorific_is_withheld(self):
        for old, new in [('Toru-kun', 'Toru Mizushima-kun'), ('Toru', 'Toru-kun'),
                         ('Toru-kun', 'Toru'), ('Toru Mizushima', 'Toru')]:
            with self.subTest(old=old, new=new):
                result, text = self.parse(old, [(old, new)])
                self.assertEqual(text, old)
                self.assertTrue(any(w['reason'] == 'ocr_name_scope_change_not_applied' for w in result['needs_review']))

    def test_name_order_is_preserved_while_spelling_can_be_repaired(self):
        _, text = self.parse('Mizushlma Toru', [('Mizushlma Toru', 'Mizushima Toru')])
        self.assertEqual(text, 'Mizushima Toru')
        result, text = self.parse('Mizushima Toru', [('Mizushima Toru', 'Toru Mizushima')])
        self.assertEqual(text, 'Mizushima Toru')
        self.assertEqual(result['needs_review'][0]['kind'], 'name_reordering')

    def test_correct_full_surface_is_not_partially_rewritten(self):
        _, text = self.parse('TORU / Toru Mizushima', [('TORU', 'Tooru')])
        self.assertEqual(text, 'Tooru / Toru Mizushima')

    def test_valid_replacements_still_cascade_in_returned_order(self):
        _, text = self.parse('A B', [('A', 'B'), ('B', 'C')], 'Other Person')
        self.assertEqual(text, 'C C')

    def test_word_boundaries_preserve_unicode_and_casefold_units(self):
        self.assertEqual(scan.find_all('STRASSE Straße', 'straße'), [(0, 7), (8, 14)])
        self.assertEqual(scan.find_all('ß', 's'), [])
        self.assertEqual(scan.find_all('Koyuk\u0301 Koyuk', 'Koyuk'), [(7, 12)])
        self.assertEqual(scan.find_all('Toru\u203fToru', 'Toru'), [])

    def test_model_contract_explicitly_separates_group_name_and_ocr_surface(self):
        self.assertIn('TORU -> Toru, NEVER Toru Mizushima', scan.PROMPT)
        self.assertIn('Do not copy canonical_name into every new_name', scan.PROMPT)
        field = scan.schema()['properties']['names']['items']['properties']['new_name']
        self.assertIn('Spelling-only', field['description'])

    def test_historical_v3_can_be_read_but_is_not_rerun_under_new_source(self):
        store = SimpleNamespace(manifest={'config': {'v6': {'scan_policy': 'canonical-scan-v3'}}})
        with self.assertRaisesRegex(ValueError, 'frozen source'):
            semantic.scan(store, {}, {}, None)
        document = {'utterances': [{'id': 'row', 'text': 'TORU', 'is_essential_text': True}]}
        result, _ = self.parse('TORU', [('TORU', 'Toru')])
        normalization = result['name_normalization']
        normalization['policy'] = 'canonical-scan-v3'
        self.assertEqual(apply(document, normalization)['utterances'][0]['text'], 'Toru')


if __name__ == '__main__':
    unittest.main()
