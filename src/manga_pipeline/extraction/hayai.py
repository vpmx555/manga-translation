"""Pinned Hayai OCR for MAGI's ordered text detections."""
import math
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MODEL = "JustANormalTinkerer/hayai-ocr-v2.5-nova"
REVISION = "2803d0dc005fa4396ab1e7c5a472d2c450a247f7"
PROCESSOR = "google/siglip2-base-patch16-naflex"
PROCESSOR_REVISION = "b53b807d3a2d5e2b3911292f2d69e5341cdc064c"
DEFAULT_CONFIG = {
    "backend": "hayai", "model": MODEL, "revision": REVISION,
    "batch_size": 8, "max_num_patches": 384, "max_new_tokens": 128, "python": None,
}


def validate_config(config):
    if config["backend"] not in {"hayai", "magi"}:
        raise ValueError("Unknown OCR backend")
    for key in ("batch_size", "max_num_patches", "max_new_tokens"):
        if type(config[key]) is not int or config[key] <= 0:
            raise ValueError(f"OCR {key} must be a positive integer")
    if not isinstance(config["model"], str) or not config["model"] or not isinstance(config["revision"], str) or not config["revision"]:
        raise ValueError("OCR model and pinned revision are required")
    if config["python"] is not None and not isinstance(config["python"], str):
        raise ValueError("OCR python must be an interpreter path")


def recognize_isolated(paths, results, config, device, progress_path=None):
    """Keep Hayai's modern Transformers separate from pinned MAGI dependencies."""
    interpreter = config.get("python") or os.environ.get("MANGA_HAYAI_PYTHON")
    if not interpreter:
        project = Path(__file__).resolve().parents[3]
        for relative in (".venv-hayai-local/Scripts/python.exe", ".venv-hayai-local/bin/python", "venv-hayai/bin/python"):
            candidate = project / relative
            if candidate.is_file():
                interpreter = str(candidate)
                break
    if not interpreter:
        raise RuntimeError("Hayai OCR requires its own environment: set ocr.python or MANGA_HAYAI_PYTHON (requirements-hayai.txt)")
    with tempfile.TemporaryDirectory(prefix="manga-hayai-") as folder:
        request, response = Path(folder) / "request.json", Path(folder) / "response.json"
        request.write_text(json.dumps({"paths": [str(p) for p in paths], "results": results,
            "config": config, "device": device, "progress_path": progress_path}), encoding="utf-8")
        subprocess.run([interpreter, str(Path(__file__).resolve()), str(request), str(response)], check=True)
        output = json.loads(response.read_text(encoding="utf-8"))
        if len(output) != len(results):
            raise ValueError("Hayai OCR page alignment mismatch")
        for result, texts in zip(results, output):
            if len(texts) != len(result["texts"]) or any(not isinstance(t, str) for t in texts):
                raise ValueError("Hayai OCR text alignment mismatch")
            result["ocr"] = texts


def text_crop(image, box):
    from PIL import Image
    if len(box) != 4 or not all(math.isfinite(float(v)) for v in box):
        raise ValueError("Invalid OCR text box")
    height, width = image.shape[:2]
    x1, y1, x2, y2 = map(float, box)
    bounds = (max(0, math.floor(x1)), max(0, math.floor(y1)),
              min(width, math.ceil(x2)), min(height, math.ceil(y2)))
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise ValueError("OCR text box is empty or outside the page")
    return Image.fromarray(image).convert("RGB").crop(bounds)


def local_assets(model, revision):
    from manga_pipeline.storage.io import read_json, file_hash
    directory = Path(__file__).resolve().parents[3] / "models/ocr" / model.split("/")[-1] / revision
    receipt = directory / "assets.json"
    if not receipt.exists():
        return model
    saved = read_json(receipt)
    if saved.get("model") != model or saved.get("revision") != revision or not saved.get("sha256"):
        raise ValueError("Local OCR asset model/revision mismatch")
    for relative,digest in saved["sha256"].items():
        target = (directory/relative).resolve()
        if not target.is_relative_to(directory.resolve()) or not target.is_file() or file_hash(target) != digest:
            raise ValueError("Local OCR asset changed/corrupt")
    return str(directory)


def recognize(images, results, config, device, progress_path=None):
    import torch
    from transformers import AutoModel, AutoProcessor, PreTrainedTokenizerFast
    # This module also runs directly in the isolated OCR environment.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from manga_pipeline.extraction.progress import emit

    if len(images) != len(results):
        raise ValueError("OCR page count mismatch")
    locations = [(page, index) for page, result in enumerate(results)
                 for index in range(len(result["texts"]))]
    for result in results:
        result["ocr"] = [""] * len(result["texts"])
    if not locations:
        return
    emit(progress_path, "Loading Hayai OCR")
    model = AutoModel.from_pretrained(local_assets(config["model"],config["revision"]), revision=config["revision"],
                                      trust_remote_code=True).to(device).eval()
    tokenizer = PreTrainedTokenizerFast.from_pretrained(local_assets(config["model"],config["revision"]), revision=config["revision"])
    processor = AutoProcessor.from_pretrained(local_assets(PROCESSOR,PROCESSOR_REVISION), revision=PROCESSOR_REVISION)
    size = config["batch_size"]
    with torch.inference_mode():
        for start in range(0, len(locations), size):
            batch = locations[start:start + size]
            crops = [text_crop(images[p], results[p]["texts"][i]) for p, i in batch]
            inputs = processor(images=crops, max_num_patches=config["max_num_patches"],
                               return_tensors="pt").to(device)
            texts = model.generate(pixel_values=inputs["pixel_values"],
                pixel_attention_mask=inputs["pixel_attention_mask"],
                spatial_shapes=inputs["spatial_shapes"], tokenizer=tokenizer,
                max_new_tokens=config["max_new_tokens"], repetition_penalty=1.0)
            if len(texts) != len(batch) or any(not isinstance(t, str) for t in texts):
                raise ValueError("Hayai OCR returned invalid text alignment")
            for (page, index), text in zip(batch, texts):
                results[page]["ocr"][index] = text.strip()
            emit(progress_path, "Hayai OCR", start + len(batch), len(locations), "text")


if __name__ == "__main__":
    import numpy as np
    from PIL import Image
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    images = []
    for path in data["paths"]:
        with Image.open(path) as image:
            images.append(np.asarray(image.convert("L").convert("RGB")))
    recognize(images, data["results"], data["config"], data["device"], data["progress_path"])
    Path(sys.argv[2]).write_text(json.dumps([r["ocr"] for r in data["results"]]), encoding="utf-8")
