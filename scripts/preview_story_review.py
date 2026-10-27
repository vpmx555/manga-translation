"""Build a portable, offline chapter-review website from a verified local run."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manga_pipeline.review.story_graph import build_review, digest


def build(run, output, source_folder=None):
    run, output = Path(run).resolve(), Path(output).resolve()
    artifacts = {}
    for stage in ('graph', 'analyze', 'reanalyze'):
        matches = list(run.glob('[0-9][0-9]_'+stage+'/result.json'))
        if len(matches) == 1:
            artifacts[stage] = matches[0]
    if 'graph' not in artifacts:
        raise ValueError('A graph result is required for the review website')
    protected = [run/'manifest.json', *artifacts.values()]
    hashes = {str(p): digest(p) for p in protected if p.is_file()}
    outputs = []
    for stage, artifact in artifacts.items():
        destination = output if stage == 'graph' else output/stage
        links = {name: ('index.html' if name == 'graph' else name+'/index.html')
                 for name in artifacts}
        if stage != 'graph':
            links = {name: '../'+link for name, link in links.items()}
        report = build_review(artifact, destination, source_folder=source_folder if stage == 'graph' else None,
                              stage_links=links)
        outputs.append({'stage': stage, 'page': report['review'], 'stats': report['stats'],
                        'warnings': report['warnings']})
    if any(digest(Path(p)) != value for p, value in hashes.items()):
        raise RuntimeError('Frozen source artifact changed while building review')
    report = {'renderer': 'storylens-v1', 'model_requests': 0, 'source_hashes': hashes,
              'entry': str(output/'index.html'), 'pages': outputs}
    (output/'preview-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-folder', type=Path)
    args = parser.parse_args()
    report = build(args.run_dir, args.output, args.source_folder)
    print(json.dumps({'entry': report['entry'], 'pages': len(report['pages']), 'model_requests': 0}))
