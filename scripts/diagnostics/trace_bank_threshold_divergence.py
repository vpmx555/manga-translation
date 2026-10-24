"""Trace Iris Zero P34 threshold divergence without model inference."""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    import numpy as np
    import torch
    from manga_pipeline.bank.identity import CharacterBank
    from manga_pipeline.bank.assignment import DynamicCharacterAssigner, _aggregate_distance
    from manga_pipeline.extraction.magi import MODEL_FINGERPRINT
    base = ROOT / 'outputs'
    folder = base / 'diagnostics/iris-zero-ch1-consensus-distance050-055-20261022'
    raw = json.loads((base / 'runpod-iris-zero-ch1-v6-l40s-inference-first-20261021-r3/live/outputs/iris-zero/chapter-001/20261021T141629Z-34e4e3d3/02_extract/result.json').read_text(encoding='utf-8'))
    pages = raw['pages']
    vectors = [np.load(next((base / 'diagnostics/iris-zero-ch1-bank-distance-040-20261022/embeddings').glob(f'{i:03d}-*.npy')), allow_pickle=False) for i in range(1, len(pages)+1)]
    results = {}

    class Model:
        _character_assignment_panels = [p['panels'] for p in pages]

        def predict_crop_embeddings(self, images, boxes):
            return [torch.from_numpy(v) for v in vectors]

    for threshold in (0.5, 0.55):
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
                prototypes = self.bank.get_character_embeddings(5)
                record = {'page': observation.page_index+1, 'cluster': observation.cluster_label,
                    'detections': [i+1 for i in observation.detection_indices],
                    'global_indices': observation.global_indices,
                    'panels': sorted(observation.panel_ids, key=str), 'blocked': sorted(blocked),
                    'candidates': self._candidate_scores(observation, blocked)[:5],
                    'id5_score_even_if_blocked': _aggregate_distance(observation.representative, prototypes) if prototypes.size else None,
                    'id5_vector_hash': hashlib.sha256(prototypes.tobytes()).hexdigest(),
                    'query': observation.representative.tolist()}
                records.append(record)
                chosen = super()._resolve_existing(observation, blocked)
                record['accepted_existing'] = chosen
                return chosen

        with tempfile.TemporaryDirectory(prefix='threshold-trace-') as temp:
            with Bank(Path(temp), raw['story_id'], model_fingerprint=MODEL_FINGERPRINT) as bank:
                bank.config.update(max_distance=threshold, min_margin=0, pending_max_page_gap=1)
                assigner = Trace(bank, [f'p{i}' for i in range(len(pages))], panel_assignment='overlap')
                assigner.assign_names_to_characters(Model(), [np.zeros((1, 1, 3), dtype=np.uint8) for p in pages],
                    [p['characters'] for p in pages], {}, [p['character_cluster_labels'] for p in pages])
                expected = json.loads((folder / 'result.json').read_text(encoding='utf-8'))['assignments'][f'distance-{threshold:.2f}']
                if assigner.last_character_ids != expected:
                    raise ValueError('Replay differs from diagnostic')
                for record in records:
                    record['final_id'] = assigner.last_character_ids[record.pop('global_indices')[0]]
        results[str(threshold)] = records
    (folder / 'p34-threshold-trace.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    first = next(({'page': a['page'], 'detections': a['detections'], '0.5': a['accepted_existing'], '0.55': b['accepted_existing']} for a, b in zip(results['0.5'], results['0.55']) if a['accepted_existing'] != b['accepted_existing']), None)
    print(json.dumps({'first_routing_difference': first, 'p34': {k: [{a:b for a,b in r.items() if a not in ('query', 'id5_vector_hash')} for r in rows if r['page'] == 34 and (1 in r['detections'] or 3 in r['detections'])] for k, rows in results.items()}}))


if __name__ == '__main__':
    main()
