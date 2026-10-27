"""Export page examples for extract/geometry/normalize docs from saved artifacts."""
import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil

from PIL import Image, ImageDraw


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--source-folder', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--page', type=int, default=11)
    a = p.parse_args()
    run, output = a.run_dir.resolve(), a.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipts = []

    def load(stage):
        path, = run.glob('[0-9][0-9]_'+stage+'/result.json')
        receipts.append({'file': str(path), 'sha256': sha(path)})
        return json.loads(path.read_text(encoding='utf-8-sig'))

    raw, geo, normalized = load('extract'), load('bubble_geometry'), load('normalize')
    page = next(x for x in raw['pages'] if x['page'] == a.page)
    geometry = next(x for x in geo['pages'] if x['page'] == a.page)
    manifest = json.loads((run/'manifest.json').read_text(encoding='utf-8-sig'))
    receipt = manifest['source']['images'][a.page-1]
    source = a.source_folder / PurePosixPath(receipt['path'].replace('\\', '/')).name
    if sha(source) != receipt['sha256']:
        raise ValueError('Source image changed')
    receipts.append({'file': str(source), 'sha256': sha(source)})
    with Image.open(source) as original:
        image = original.convert('RGB')
    shutil.copyfile(source, output / ('input'+source.suffix))
    texts = [x for x in raw['texts'] if x['page'] == a.page]
    mapped = [x for x in geo['texts'] if x['page'] == a.page]
    utterance = next(x for x in normalized['utterances'] if x['page'] == a.page)
    group = next(x for x in geometry['bubble_groups'] if x['id'] == utterance['group_id'])
    (output/'extract-page.json').write_text(json.dumps({'page': page, 'texts': texts}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (output/'geometry-group.json').write_text(json.dumps({'group': group,
        'texts': [t for t in mapped if t.get('group_id') == group['id']]}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (output/'normalized-utterance.json').write_text(json.dumps(utterance, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    def zoom(picture, bbox, filename):
        x1,y1,x2,y2 = bbox
        crop = picture.crop((max(0,math.floor(x1)-16),max(0,math.floor(y1)-16),
            min(picture.width,math.ceil(x2)+16),min(picture.height,math.ceil(y2)+16)))
        crop.resize((crop.width*2,crop.height*2),Image.Resampling.NEAREST).save(output/filename)

    overview = image.copy()
    draw = ImageDraw.Draw(overview)
    for i, box in enumerate(page['characters']):
        draw.rectangle(box,outline='#00843d',width=3)
        draw.text((box[0]+3,box[1]+3),'MAGI '+str(page['character_ids'][i]),fill='#00843d',stroke_width=1,stroke_fill='white')
    for text in texts:
        draw.rectangle(text['bbox'],outline='#d02020',width=2)
    overview.save(output/'extract-overview.png')
    index = texts[0].get('character_detection_index')
    if index is not None:
        zoom(overview,page['characters'][index],'character-zoom.png')
    text_picture = image.copy()
    d = ImageDraw.Draw(text_picture)
    for text in utterance['source_boxes']:
        d.rectangle(text['bbox'],outline='#d02020',width=2)
        d.text(tuple(text['bbox'][:2]),text['id'].split(':')[-1],fill='#d02020')
    d.rectangle(utterance['bbox'],outline='#165dcc',width=3)
    zoom(text_picture,utterance['bbox'],'normalize-zoom.png')
    for key in ('refined_mask','bridge_mask','observed_mask'):
        record = group.get(key)
        if not isinstance(record,dict) or not record.get('mask_path'):
            continue
        path = (run/record['mask_path']).resolve()
        if not path.is_relative_to(run) or sha(path) != record['mask_sha256']:
            raise ValueError('Geometry mask checksum mismatch')
        receipts.append({'file':str(path),'sha256':sha(path)})
        with Image.open(path) as mask:
            zoom(mask,group['bbox'],key+'.png')
    overlay = run/next((x for x in geometry['files'] if '/overlays/' in x), '')
    if overlay.is_file():
        shutil.copyfile(overlay,output/'geometry-overview.png')
        receipts.append({'file':str(overlay),'sha256':sha(overlay)})
    record = {'page':a.page,'source_artifacts':receipts,'generator_sha256':sha(__file__),
        'model_requests':0,'assets':{x.name:sha(x) for x in sorted(output.iterdir()) if x.is_file() and x.name!='manifest.json'}}
    (output/'manifest.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(output),'page':a.page,'files':len(record['assets']),'model_requests':0}))


if __name__ == '__main__':
    main()
