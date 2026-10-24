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
    "engine": "core", "batch_size": 16, "max_num_patches": 384, "max_new_tokens": 128, "python": None,
}


def validate_config(config):
    if config.get('engine', 'core') != 'core':
        raise ValueError('OCR engine must be core')
    if config["backend"] not in {"hayai", "magi", "glm-ocr"}:
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
    glm = config["backend"] == "glm-ocr"
    label = "GLM-OCR" if glm else "Hayai"
    interpreter = config.get("python") or os.environ.get("MANGA_GLM_OCR_PYTHON" if glm else "MANGA_HAYAI_PYTHON")
    if not interpreter:
        project = Path(__file__).resolve().parents[3]
        for relative in ((".venv-glm-ocr/Scripts/python.exe", ".venv-glm-ocr/bin/python") if glm else
                         (".venv-hayai-local/Scripts/python.exe", ".venv-hayai-local/bin/python", "venv-hayai/bin/python")):
            candidate = project / relative
            if candidate.is_file():
                interpreter = str(candidate)
                break
    if not interpreter:
        raise RuntimeError(label+" requires its own environment: set ocr.python for the selected backend")
    with tempfile.TemporaryDirectory(prefix="manga-hayai-") as folder:
        request, response = Path(folder) / "request.json", Path(folder) / "response.json"
        request.write_text(json.dumps({"paths": [str(p) for p in paths], "results": results,
            "config": config, "device": device, "progress_path": progress_path}), encoding="utf-8")
        subprocess.run([interpreter, str(Path(__file__).resolve()), str(request), str(response)], check=True)
        output = json.loads(response.read_text(encoding="utf-8"))
        details = output.get("details") if isinstance(output, dict) else None
        output = output["ocr"] if isinstance(output, dict) else output
        if len(output) != len(results):
            raise ValueError("Hayai OCR page alignment mismatch")
        for result, texts in zip(results, output):
            if len(texts) != len(result["texts"]) or any(not isinstance(t, str) for t in texts):
                raise ValueError("Hayai OCR text alignment mismatch")
            result["ocr"] = texts
        if details is not None:
            if len(details) != len(results) or any(len(d) != len(r["texts"]) for d, r in zip(details, results)):
                raise ValueError("OCR detail alignment mismatch")
            for result, items in zip(results, details):
                result["ocr_details"] = items


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
    # This module also runs directly in the isolated OCR environment.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from manga_pipeline.extraction.progress import emit
    from manga_pipeline.runtime.resources import ResourceExhausted, event, is_oom
    from manga_pipeline.extraction.core_ocr import MangaOcrEngine

    if len(images) != len(results):
        raise ValueError("OCR page count mismatch")
    locations = [(page, index) for page, result in enumerate(results)
                 for index in range(len(result["texts"]))]
    for result in results:
        result["ocr"] = [""] * len(result["texts"])
    if not locations:
        return
    glm = config["backend"] == "glm-ocr"
    label = "GLM-OCR" if glm else "Hayai OCR"
    emit(progress_path, "Loading "+label)
    if glm:
        from manga_pipeline.extraction.glm_ocr import GlmOcrEngine
        engine = GlmOcrEngine(model_name=config['model'], revision=config['revision'],
                              device=device, max_new_tokens=config['max_new_tokens'])
        for result in results:
            result["ocr_details"] = [None] * len(result["texts"])
    else:
        engine = MangaOcrEngine(model_name=config['model'], revision=config['revision'],
            processor_name=PROCESSOR, processor_revision=PROCESSOR_REVISION, device=device,
            max_num_patches=config['max_num_patches'], max_new_tokens=config['max_new_tokens'])
    engine.load()
    size = config["batch_size"]
    recovery_limit = int(os.environ.get("MANGA_GPU_RECOVERY_ATTEMPTS", "0"))
    with torch.inference_mode():
        start, recovery_count = 0, 0
        while start < len(locations):
            batch = locations[start:start + size]
            crops = [text_crop(images[p], results[p]["texts"][i]) for p, i in batch]
            try:
                texts = engine.predict_batch(crops)
            except Exception as exc:
                if not is_oom(exc):
                    raise
                if not str(device).startswith("cuda") or len(batch) <= 1 or recovery_count >= recovery_limit:
                    raise ResourceExhausted(label+" resource recovery exhausted: " + str(exc)) from exc
                recovery_count += 1
                size = max(1, len(batch)//2)
                torch.cuda.empty_cache()
                event("ocr_batch_split", start=start, targets=len(batch), next_size=size, attempt=recovery_count)
                continue
            if len(texts) != len(batch) or any(not isinstance(t, str) for t in texts):
                raise ValueError("Hayai OCR returned invalid text alignment")
            for position, ((page, index), text) in enumerate(zip(batch, texts)):
                results[page]["ocr"][index] = text
                if glm:
                    results[page]["ocr_details"][index] = engine.last_details[position]
            emit(progress_path, label, start + len(batch), len(locations), "text")
            start += len(batch)
            recovery_count = 0


if __name__ == "__main__":
    import numpy as np
    from PIL import Image
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    images = []
    for path in data["paths"]:
        with Image.open(path) as image:
            images.append(np.asarray(image.convert("L").convert("RGB")))
    recognize(images, data["results"], data["config"], data["device"], data["progress_path"])
    Path(sys.argv[2]).write_text(json.dumps({"ocr": [r["ocr"] for r in data["results"]],
        "details": [r.get("ocr_details", [None]*len(r["ocr"])) for r in data["results"]]}), encoding="utf-8")
