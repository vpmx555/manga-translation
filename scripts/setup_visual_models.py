"""Download the pinned visual checkpoints and record their hashes."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from manga_pipeline.storage.io import atomic_json, file_hash
from manga_pipeline.visual.config import INPAINT_CONFIG, LAYOUT_CONFIG, asset_dir, checked_assets


def prepare(config):
    from huggingface_hub import hf_hub_download

    directory = asset_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    path = Path(hf_hub_download(repo_id=config["model"], filename="model.safetensors",
                                revision=config["revision"], local_dir=directory))
    if path.resolve() != (directory / "model.safetensors").resolve():
        raise ValueError("Downloaded checkpoint is outside the expected model directory")
    receipt = {"model": config["model"], "revision": config["revision"],
               "sha256": {"model.safetensors": file_hash(path)}}
    atomic_json(directory / "assets.json", receipt)
    checked_assets(config)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=("layout", "inpaint"))
    parser.add_argument("--model-root", type=Path)
    args = parser.parse_args(argv)
    for name, defaults in (("layout", LAYOUT_CONFIG), ("inpaint", INPAINT_CONFIG)):
        if args.only and args.only != name:
            continue
        config = {**defaults, **({"model_root": str(args.model_root)} if args.model_root else {})}
        print(f"{name}: {prepare(config)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
