"""Small, OCR-only control for batch and patch-budget effects on saved crops."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--comparison", type=Path, required=True)
    args = parser.parse_args()
    import torch
    from PIL import Image
    from manga_pipeline.extraction.core_ocr import MangaOcrEngine
    from manga_pipeline.extraction.hayai import PROCESSOR, PROCESSOR_REVISION

    torch.set_num_threads(4)
    source = args.comparison / "result.json"
    raw = source.read_bytes()
    result = json.loads(raw)
    config = result["config"]
    keys = {"main_magi_gray-03-5", "reference_jpeg_rgb-03-7",
            "main_magi_gray-11-1", "reference_jpeg_rgb-11-2"}
    tasks = [task for task in result["tasks"] if task["key"] in keys]
    if len(tasks) != len(keys):
        raise ValueError("Expected all four selected control crops")
    engine = MangaOcrEngine(
        model_name=config["model"], revision=config["revision"],
        processor_name=PROCESSOR, processor_revision=PROCESSOR_REVISION,
        device="cpu", max_num_patches=384,
        max_new_tokens=config["max_new_tokens"],
    )
    engine.load()
    records = []
    started = time.monotonic()
    for patches in (384, 256):
        engine.max_num_patches = patches
        for task in tasks:
            crop = args.comparison / task["crop"]
            with Image.open(crop) as image:
                text = engine.predict_batch([image.convert("RGB")])[0]
            records.append({
                "key": task["key"], "max_num_patches": patches,
                "batch_size": 1, "crop": task["crop"],
                "crop_sha256": hashlib.sha256(crop.read_bytes()).hexdigest(),
                "reference_or_old": task["baseline"],
                "batch16_384_text": task["new_text"], "new_text": text,
            })
            print(f"Control {len(records)}/8", flush=True)
    output = {"status": "completed", "config": config,
              "comparison_sha256": hashlib.sha256(raw).hexdigest(),
              "elapsed_seconds": time.monotonic() - started,
              "records": records}
    (args.comparison / "patch-budget-control.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
