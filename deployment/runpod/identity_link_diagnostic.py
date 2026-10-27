"""Local deterministic identity replay. No provider, profile inference or bank writes."""
import argparse
import copy
import html
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from manga_pipeline.storage.io import atomic_json, file_hash, fingerprint, read_json
from manga_pipeline.v6.identity_from_analyze import register
from manga_pipeline.storage.runs import flow_steps
from manga_pipeline.v6.identity_v2 import confirmed, membership, visual_links, vectors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--bank', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--minimum-mentions', type=int, default=3)
    args = parser.parse_args()
    if args.minimum_mentions < 1 or args.output.exists():
        raise ValueError('Use a positive threshold and a new output directory')
    manifest = read_json(args.source_run / 'manifest.json')
    config = manifest['config']
    artifacts = {}
    for stage in ('extract', 'graph', 'analyze', 'build_identity'):
        number = flow_steps(config).index(stage) + 1
        file = args.source_run / f'{number:02d}_{stage}' / 'result.json'
        if file_hash(file) != manifest['steps'][stage]['artifact_hash']:
            raise ValueError('Source artifact changed: ' + stage)
        artifacts[stage] = read_json(file)
    baseline = read_json(args.source_run / 'snapshots/persons-v6.json')['state']
    metadata = read_json(args.bank)
    before_bank = file_hash(args.bank)
    state, binding = register(artifacts['analyze'], artifacts['graph'], artifacts['extract'],
        metadata, baseline, config['v6']['identity_min_chapter_crops'],
        minimum_events=args.minimum_mentions)
    retained, issues = vectors(args.bank, metadata)
    state, visual, reviews, anchors = visual_links(state, metadata, retained,
        config['v6']['identity_visual_min_crop'])
    members, _ = membership(state)
    identities = {pid: {'primary_name': state['persons'][pid]['primary_name'],
                       'magi_ids': sorted(mids, key=int), 'main_magi_id': anchors.get(pid)}
                  for pid, mids in members.items() if confirmed(state, pid)}
    previous = artifacts['build_identity']
    # Preserve profile bytes as supplied; their provenance remains the old run.
    profiles = copy.deepcopy(previous.get('profiles', {}))
    result = {'policy': 'local-identity-links-only-v1', 'source_run': str(args.source_run.resolve()),
        'source_manifest_sha256': file_hash(args.source_run / 'manifest.json'),
        'minimum_confirmed_mentions': args.minimum_mentions, 'name_binding': binding,
        'person_state': state, 'identities': identities,
        'previous_identities': previous.get('eligible_identities', {}),
        'proposals': [*binding['proposals'], *visual], 'visual_reviews': reviews,
        'crop_issues': issues, 'frozen_main_members': anchors,
        'profiles': profiles, 'profiles_reused_without_inference': True,
        'profile_provenance': str(args.source_run / '07_build_identity/result.json'),
        'profile_context_not_refreshed': True, 'relationship_llm_rerun': False,
        'model_requests': 0, 'source_bank_mutated': False}
    if file_hash(args.bank) != before_bank or fingerprint(profiles) != fingerprint(previous.get('profiles', {})):
        raise ValueError('Read-only replay invariant failed')
    args.output.mkdir(parents=True)
    atomic_json(args.output / 'result.json', result)
    atomic_json(args.output / 'config.json', {**config, 'v6': {
        **config['v6'], 'min_confirmed_call_events': args.minimum_mentions}})
    escape = lambda v: html.escape(str(v))
    rows = ''.join('<tr>' + ''.join('<td>' + escape(v) + '</td>' for v in (
        s['magi_id'], s['canonical_name'], s['support'], s['chapter_crop_count'],
        'Accepted' if s['eligible'] else 'Below threshold')) + '</tr>'
        for s in binding['selections'])
    nodes = ''.join('<li>' + escape(v['primary_name']) + ': MAGI ' +
                    escape('/'.join(v['magi_ids'])) + '</li>' for v in identities.values())
    links = ''.join('<tr>' + ''.join('<td>' + escape(v) + '</td>' for v in (
        p['magi_id'], p.get('main_magi_id', ''), p['decision_basis'], p.get('min_crop', 'name evidence')))
        + '</tr>' for p in result['proposals'])
    (args.output / 'index.html').write_text('<!doctype html><meta charset="utf-8">'
        '<title>Identity — minimum 3 mentions</title><style>body{font:16px system-ui;max-width:1100px;'
        'margin:40px auto;padding:20px}td,th{padding:12px;border:1px solid #bbb}table{border-collapse:collapse}</style>'
        '<h1>Local identity links</h1><p>Minimum confirmed mentions: ' + str(args.minimum_mentions) +
        '. Model requests: 0. Profiles preserved from the source run; dialogue context was not refreshed. '
        'Relationship LLM was not rerun.</p><h2>Named identities</h2><ul>' + nodes +
        '</ul><h2>Name decisions</h2><table><tr><th>MAGI</th><th>Name</th><th>Mentions</th>'
        '<th>Chapter crops</th><th>Decision</th></tr>' + rows +
        '</table><h2>Links after visual matching</h2><table><tr><th>MAGI</th><th>Main MAGI</th>'
        '<th>Basis</th><th>Distance</th></tr>' + links +
        '</table><p><a href="result.json">Full diagnostic JSON</a></p>', encoding='utf-8')
    print({'output': str(args.output), 'identities': identities,
           'visual_links': [{'magi_id': p['magi_id'], 'main_magi_id': p['main_magi_id'],
                             'min_crop': p['min_crop']} for p in visual],
           'model_requests': 0, 'crop_issues': len(issues)})


if __name__ == '__main__':
    main()
