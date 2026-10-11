"""Prepare only the pinned local OCR model and processor assets."""
import json
import sys
from pathlib import Path
PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT/"src"))
from huggingface_hub import snapshot_download
from manga_pipeline.extraction.hayai import MODEL, REVISION, PROCESSOR, PROCESSOR_REVISION
from manga_pipeline.storage.io import atomic_json, file_hash


def main():
    assets = []
    for model,revision,patterns in ((MODEL,REVISION,["*.json","*.safetensors","*.py","*.txt","*.model"]),
                                    (PROCESSOR,PROCESSOR_REVISION,["*.json","*.txt","*.model"])):
        directory = PROJECT / "models/ocr" / model.split("/")[-1] / revision
        directory = Path(snapshot_download(model,revision=revision,allow_patterns=patterns,local_dir=str(directory)))
        hashes = {p.relative_to(directory).as_posix():file_hash(p) for p in directory.rglob("*")
                  if p.is_file() and ".cache" not in p.parts and p.name != "assets.json"}
        atomic_json(directory / "assets.json", {"model":model,"revision":revision,"sha256":hashes})
        assets.append({"model":model,"revision":revision,"directory":str(directory),
            "files":hashes})
        print(json.dumps({"model":model,"revision":revision,"files":len(assets[-1]["files"])}),flush=True)
    atomic_json(PROJECT/"outputs/diagnostics/v6-ocr-assets.json",{"assets":assets,"execution":"local"})


if __name__ == "__main__":
    main()
