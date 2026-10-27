"""Copy one completed analyze event into docs, without invoking inference."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--page', type=int, default=36)
    a = p.parse_args()
    run, output = a.run_dir.resolve(), a.output.resolve()
    directory, = run.glob('[0-9][0-9]_analyze')
    for target in sorted((directory/'targets').glob('*.json')):
        checkpoint = json.loads(target.read_text(encoding='utf-8-sig'))
        rows = checkpoint.get('result', {}).get('rows', [])
        decision = next((r for r in rows if f':p{a.page}:' in r['utterance_id']), None)
        if checkpoint.get('status') == 'completed' and decision:
            break
    else:
        raise ValueError('No completed checkpoint for requested page')
    packet_path = directory/'packets'/target.name
    event_path = directory/'events'/target.name
    packet = json.loads(packet_path.read_text(encoding='utf-8-sig'))
    event = json.loads(event_path.read_text(encoding='utf-8-sig'))
    output.mkdir(parents=True, exist_ok=True)
    for name, record in [('decision.json',decision), ('event.json',event),
            ('packet-summary.json',{k:packet[k] for k in ['event_id','scene_id','targets','context','images']})]:
        (output/name).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    receipts = [{'file':str(x),'sha256':sha(x)} for x in [target,packet_path,event_path]]
    for index, image in enumerate(packet['images']):
        path = (run/image['file']).resolve()
        if not path.is_relative_to(run) or sha(path)!=image['retained_sha256']:
            raise ValueError('Event image changed')
        shutil.copyfile(path,output/f'event-{index+1}.jpg')
        receipts.append({'file':str(path),'sha256':sha(path)})
    manifest={'event_id':event['event_id'],'page':a.page,'source_files':receipts,
        'generator_sha256':sha(__file__),'model_requests':0,
        'assets':{x.name:sha(x) for x in sorted(output.iterdir()) if x.is_file() and x.name!='manifest.json'}}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(output),'event_id':event['event_id'],'model_requests':0}))
