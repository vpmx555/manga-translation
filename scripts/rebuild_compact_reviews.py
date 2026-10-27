"""Rebuild graph/analyze/reanalyze presentation files offline, without RunStore recovery."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manga_pipeline.v6.story_review import write_html, compact_markdown


def rebuild(run):
    run = Path(run).resolve()
    stages = {}
    for stage in ('normalize', 'graph', 'analyze', 'reanalyze'):
        paths = list(run.glob('[0-9][0-9]_' + stage + '/result.json'))
        if len(paths) != 1:
            raise ValueError('Expected exactly one result.json for ' + stage)
        stages[stage] = paths[0]
    protected = [run / 'manifest.json', *stages.values()]
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    before = {str(p): digest(p) for p in protected if p.exists()}
    store = SimpleNamespace(directory=run, artifact_path=lambda s: stages[s],
                            read=lambda s: json.loads(stages[s].read_text(encoding='utf-8-sig')))
    outputs = []
    for stage in ('graph', 'analyze', 'reanalyze'):
        data = store.read(stage)
        path = write_html(store, stage, data)
        markdown = stages[stage].parent / 'review.md'
        markdown.write_text(compact_markdown(stage, data), encoding='utf-8')
        outputs.append({'stage': stage, 'html': str(path), 'html_bytes': path.stat().st_size,
                        'markdown_bytes': markdown.stat().st_size, 'sha256': digest(path)})
    if any(digest(Path(p)) != value for p, value in before.items()):
        raise RuntimeError('Source artifact changed during review generation')
    report = {'renderer': 'storylens-v1', 'model_requests': 0,
              'source_hashes': before, 'outputs': outputs}
    (run / 'compact-review-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(rebuild(args.run_dir), ensure_ascii=False))
