"""Copy a historical inpaint page for documentation; no model execution."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--source-folder',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--page',type=int,default=11)
    a=p.parse_args()
    run,output=a.run_dir.resolve(),a.output.resolve()
    artifact,=run.glob('[0-9][0-9]_inpaint/result.json')
    result=json.loads(artifact.read_text(encoding='utf-8-sig'))
    page=next(x for x in result['pages'] if x['page']==a.page)
    output.mkdir(parents=True,exist_ok=True)
    source=a.source_folder/PurePosixPath(page['source']['path'].replace('\\','/')).name
    if sha(source)!=page['source']['sha256']:
        raise ValueError('Source page changed')
    shutil.copyfile(source,output/('input'+source.suffix))
    for relative,expected in page['files'].items():
        path=(run/relative).resolve()
        if not path.is_relative_to(run) or sha(path)!=expected:
            raise ValueError('Saved inpaint asset changed')
        shutil.copyfile(path,output/path.name)
    (output/'page.json').write_text(json.dumps(page,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    manifest={'kind':'historical-inpaint-example','source_artifact':str(artifact),
        'artifact_sha256':sha(artifact),'page':a.page,'model':result['model'],'revision':result['revision'],
        'generator_sha256':sha(__file__),'model_requests':0,
        'assets':{x.name:sha(x) for x in sorted(output.iterdir()) if x.is_file() and x.name!='manifest.json'}}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(output),'page':a.page,'model_requests':0}))
