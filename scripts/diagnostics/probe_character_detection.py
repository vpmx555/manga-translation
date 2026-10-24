"""Rerun MAGI character detection against verified, committed Koharu panels."""
import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


def plain(value):
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value.tolist() if hasattr(value, 'tolist') else value


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--threshold', type=float, default=.3)
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1:
        parser.error('Threshold must be 0..1')
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoModel
    from manga_pipeline.extraction.magi import MODEL, REVISION, MODEL_FINGERPRINT
    from manga_pipeline.visual.magi_panels import koharu_panels
    from manga_pipeline.storage.io import file_hash, fingerprint, atomic_json
    torch.set_num_threads(4)
    manifest = json.loads((args.run / 'manifest.json').read_text(encoding='utf-8-sig'))
    originals = {}
    for step in ('layout', 'extract'):
        artifact = next(args.run.glob(f'[0-9][0-9]_{step}/result.json'))
        if file_hash(artifact) != manifest['steps'][step]['artifact_hash']:
            raise ValueError('Committed artifact checksum mismatch: ' + step)
        originals[step] = json.loads(artifact.read_text(encoding='utf-8-sig'))
    if originals['extract']['model_fingerprint'] != MODEL_FINGERPRINT:
        raise ValueError('MAGI model revision mismatch')
    thresholds = {k: manifest['config'][k] for k in ('text_detection_threshold',
        'character_character_matching_threshold', 'text_character_matching_threshold', 'text_classification_threshold')}
    thresholds['character_detection_threshold'] = args.threshold
    args.output.mkdir(parents=True, exist_ok=True)
    cache = args.output / 'embeddings'
    cache.mkdir(exist_ok=True)
    raw = copy.deepcopy(originals['extract'])
    raw.update(kind='diagnostic_characters', texts=[], pages=[])
    report = {'status': 'running', 'source_run': str(args.run.resolve()),
        'source_extract_hash': manifest['steps']['extract']['artifact_hash'],
        'source_layout_hash': manifest['steps']['layout']['artifact_hash'],
        'thresholds': thresholds, 'device': 'cpu', 'ocr': False, 'raw': raw, 'page_comparison': []}
    model = None
    for index, (saved, layout, old_page) in enumerate(zip(manifest['source']['images'], originals['layout']['pages'], originals['extract']['pages']), 1):
        path = args.source / saved['path'].replace('\\', '/').rsplit('/', 1)[-1]
        if file_hash(path) != saved['sha256']:
            raise ValueError('Source image checksum mismatch: ' + str(path))
        contract = fingerprint([MODEL_FINGERPRINT, saved['sha256'], layout, thresholds])
        page_file = args.output / f'page-{index:03d}.json'
        with Image.open(path) as image:
            image = np.asarray(image.convert('L').convert('RGB'))
        if page_file.exists():
            stored = json.loads(page_file.read_text(encoding='utf-8'))
            if stored['contract'] != contract:
                raise ValueError('Diagnostic inputs changed; choose new output')
            result = stored['result']
        else:
            if model is None:
                print('Loading cached MAGI for fresh detection on CPU', flush=True)
                model = AutoModel.from_pretrained(MODEL, revision=REVISION, trust_remote_code=True,
                    local_files_only=True).eval().to('cpu')
            with torch.inference_mode(), koharu_panels(model, [layout]):
                result = plain(model.predict_detections_and_associations([image], **thresholds)[0])
            atomic_json(page_file, {'contract': contract, 'result': result})
        page = copy.deepcopy(old_page)
        page.update(characters=result['characters'], character_cluster_labels=result['character_cluster_labels'],
            panels=result['panels'], character_ids=[None]*len(result['characters']),
            character_display_labels=['Other']*len(result['characters']),
            text_character_associations=[], text_tail_associations=[])
        vector_file = cache / f'{index:03d}-{fingerprint([MODEL_FINGERPRINT, saved["sha256"], page["characters"]])}.npy'
        if not vector_file.exists():
            if model is None:
                model = AutoModel.from_pretrained(MODEL, revision=REVISION, trust_remote_code=True,
                    local_files_only=True).eval().to('cpu')
            if page['characters']:
                with torch.inference_mode():
                    vectors = model.predict_crop_embeddings([image], [page['characters']])[0].detach().cpu().numpy()
            else:
                vectors = np.empty((0, 768), dtype=np.float32)
            np.save(vector_file, vectors)
        raw['pages'].append(page)
        report['page_comparison'].append({'page': index, 'previous_detections': len(old_page['characters']),
            'current_detections': len(page['characters'])})
        atomic_json(args.output / 'result.json', report)
        print(f'Page {index}/{len(manifest["source"]["images"])}: {len(old_page["characters"])} -> {len(page["characters"])} characters', flush=True)
    if len(raw['pages']) != len(manifest['source']['images']):
        raise ValueError('Detection result is incomplete')
    report['status'] = 'completed'
    report['total_previous_detections'] = sum(p['previous_detections'] for p in report['page_comparison'])
    report['total_current_detections'] = sum(p['current_detections'] for p in report['page_comparison'])
    atomic_json(args.output / 'result.json', report)
    print(json.dumps({k: report[k] for k in ('status', 'total_previous_detections', 'total_current_detections')}), flush=True)


if __name__ == '__main__':
    main()
