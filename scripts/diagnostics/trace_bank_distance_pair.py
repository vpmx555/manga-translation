"""Replay cached assignment and record exact decisions for P31 C26 / P32 C6."""
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
    from manga_pipeline.bank.assignment import DynamicCharacterAssigner, _normalize_rows, _aggregate_distance
    from manga_pipeline.extraction.magi import MODEL_FINGERPRINT
    base = ROOT / 'outputs'
    raw = json.loads((base / 'runpod-iris-zero-ch1-v6-l40s-inference-first-20261021-r3/live/outputs/iris-zero/chapter-001/20261021T141629Z-34e4e3d3/02_extract/result.json').read_text(encoding='utf-8'))
    diagnostic = base / 'diagnostics/iris-zero-ch1-bank-distance-040-20261022'
    pages = raw['pages']
    vectors = [np.load(next((diagnostic / 'embeddings').glob(f'{i:03d}-*.npy')), allow_pickle=False) for i in range(1, len(pages)+1)]
    traces, representatives = [], {}

    class Cached:
        _character_assignment_panels = [p['panels'] for p in pages]

        def predict_crop_embeddings(self, images, boxes):
            return [torch.from_numpy(v) for v in vectors]

    class Trace(DynamicCharacterAssigner):
        def _save_crops(self, crops, prefix):
            return []

        def _resolve_existing(self, observation, blocked):
            chosen = super()._resolve_existing(observation, blocked)
            if observation.page_index in (30, 31):
                wanted = 25 if observation.page_index == 30 else 5
                if wanted in observation.detection_indices:
                    prototypes = self.bank.get_character_embeddings(14)
                    normalized = _normalize_rows(prototypes)
                    distances = np.linalg.norm(normalized - observation.representative[None, :], axis=1)
                    scores = self._candidate_scores(observation, blocked)
                    record = {'page': observation.page_index+1, 'detections': [i+1 for i in observation.detection_indices],
                        'panels': sorted(observation.panel_ids, key=str), 'blocked_ids': sorted(blocked),
                        'accepted_existing': chosen, 'best_candidates': scores[:5],
                        'id14_distance': _aggregate_distance(observation.representative, prototypes) if prototypes.size else None,
                        'id14_prototype_distances': distances.tolist(),
                        'id14_prototypes_at_decision': len(prototypes)}
                    traces.append(record)
                    representatives[observation.page_index+1] = observation.representative
            return chosen

    with tempfile.TemporaryDirectory(prefix='bank-pair-trace-') as temporary:
        with CharacterBank(Path(temporary), raw['story_id'], model_fingerprint=MODEL_FINGERPRINT) as bank:
            bank.config['max_distance'] = 0.4
            keys = [f'chapter-001/page-{i}' for i in range(len(pages))]
            assigner = Trace(bank, keys, panel_assignment='overlap')
            assigner.assign_names_to_characters(Cached(), [np.zeros((1, 1, 3), dtype=np.uint8) for p in pages],
                [p['characters'] for p in pages], {}, [p['character_cluster_labels'] for p in pages])
            offset = 0
            for i, page in enumerate(pages):
                if i in (30, 31):
                    next(r for r in traces if r['page'] == i+1)['final_id'] = assigner.last_character_ids[offset + (25 if i == 30 else 5)]
                offset += len(page['characters'])
    pair = _normalize_rows(np.vstack([vectors[30][25], vectors[31][5]]))
    report = {'decisions': traces, 'individual_crop_distance': float(np.linalg.norm(pair[0]-pair[1])),
        'cluster_representative_distance': float(np.linalg.norm(representatives[31]-representatives[32])),
        'note': 'Assignment compares cluster mean to historical bank prototypes, not these two crops directly.'}
    output = base / 'diagnostics/iris-zero-ch1-bank-distance-040-20261022-review/p31-c26-p32-c6-trace.json'
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
