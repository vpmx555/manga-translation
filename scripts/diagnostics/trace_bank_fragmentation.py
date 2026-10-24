"""Trace creation of MAGI 5/14 using cached embeddings; render actual bank previews."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    import numpy as np
    import torch
    from PIL import Image, ImageDraw
    from manga_pipeline.bank.identity import CharacterBank
    from manga_pipeline.bank.assignment import DynamicCharacterAssigner, _aggregate_distance
    from manga_pipeline.extraction.magi import MODEL_FINGERPRINT
    base = ROOT / 'outputs'
    folder = base / 'diagnostics/iris-zero-ch1-bank-crops5-10-gap1-20261022'
    raw = json.loads((base / 'runpod-iris-zero-ch1-v6-l40s-inference-first-20261021-r3/live/outputs/iris-zero/chapter-001/20261021T141629Z-34e4e3d3/02_extract/result.json').read_text(encoding='utf-8'))
    vectors = [np.load(next((base / 'diagnostics/iris-zero-ch1-bank-distance-040-20261022/embeddings').glob(f'{i:03d}-*.npy')), allow_pickle=False) for i in range(1, len(raw['pages'])+1)]
    pages = raw['pages']
    results = {}

    class Model:
        _character_assignment_panels = [p['panels'] for p in pages]

        def predict_crop_embeddings(self, images, boxes):
            return [torch.from_numpy(v) for v in vectors]

    for distance in (0.4, 0.45):
        records = []

        class Bank(CharacterBank):
            def create_character(self, embeddings, crop_paths):
                identity = super().create_character(embeddings, crop_paths)
                records[-1]['created_id'] = identity
                return identity

        class Trace(DynamicCharacterAssigner):
            def _save_crops(self, crops, prefix):
                return []

            def _resolve_existing(self, observation, blocked):
                scores = self._candidate_scores(observation, blocked)
                record = {'page': observation.page_index+1,
                    'detections': [i+1 for i in observation.detection_indices],
                    'global_indices': observation.global_indices,
                    'panels': sorted(observation.panel_ids, key=str),
                    'blocked': sorted(blocked), 'candidates': scores[:5],
                    'id5_score_even_if_blocked': None, 'id5_prototypes': 0}
                prototypes = self.bank.get_character_embeddings(5)
                if prototypes.size:
                    record['id5_score_even_if_blocked'] = _aggregate_distance(observation.representative, prototypes)
                    record['id5_prototypes'] = len(prototypes)
                records.append(record)
                chosen = super()._resolve_existing(observation, blocked)
                record['accepted_existing'] = chosen
                return chosen

        with tempfile.TemporaryDirectory(prefix='fragment-trace-') as temp:
            with Bank(Path(temp), raw['story_id'], model_fingerprint=MODEL_FINGERPRINT) as bank:
                bank.config.update(max_distance=distance, min_margin=0, pending_max_page_gap=1)
                assigner = Trace(bank, [f'p{i}' for i in range(len(pages))], panel_assignment='overlap')
                assigner.assign_names_to_characters(Model(), [np.zeros((1, 1, 3), dtype=np.uint8) for p in pages],
                    [p['characters'] for p in pages], {}, [p['character_cluster_labels'] for p in pages])
                expected = json.loads((folder / 'result.json').read_text(encoding='utf-8'))['assignments'][f'distance-{distance:.2f}']
                if assigner.last_character_ids != expected:
                    raise ValueError('Replay differs from diagnostic')
                for record in records:
                    record['final_id'] = assigner.last_character_ids[record.pop('global_indices')[0]]
        results[str(distance)] = [r for r in records if r['final_id'] in (5, 14) or r.get('created_id') in (5, 14)]
        source = folder / f'distance-{distance:.2f}/banks/iris-zero'
        meta = json.loads((source / 'metadata.json').read_text(encoding='utf-8'))
        sheet = Image.new('RGB', (1200, 1040), 'white')
        draw = ImageDraw.Draw(sheet)
        for row, identity in enumerate((5, 14)):
            for i, relative in enumerate(meta['characters'][str(identity)]['crop_paths']):
                with Image.open(source / relative) as image:
                    image = image.convert('RGB')
                    image.thumbnail((230, 225))
                    x, y = (i % 5)*240, row*520+(i//5)*260
                    draw.text((x+5, y+3), f'Distance {distance}: MAGI {identity} crop {i+1}', fill='black')
                    sheet.paste(image, (x+(240-image.width)//2, y+25))
        sheet.save(folder / f'char5-char14-{distance:.2f}.jpg', quality=95)
    (folder / 'char5-char14-trace.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps({k: [r for r in rows if r.get('created_id') in (5, 14)] for k, rows in results.items()}))


if __name__ == '__main__':
    main()
