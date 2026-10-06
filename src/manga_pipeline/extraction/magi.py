"""MAGI is imported only inside the extraction worker."""
import json
import os
import sys
from pathlib import Path

from .document import build_raw_document
from .pages import choose_device
from .progress import emit, magi_progress
from ..storage.io import atomic_bytes, atomic_json, file_hash, read_json

MODEL = "ragavsachdeva/magiv2"
REVISION = "fbc890fec52977142e8ee00bfe26e9458b65517c"
MODEL_FINGERPRINT = MODEL + "@" + REVISION


def extract(manifest_path):
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoModel
    from ..bank.assignment import DynamicCharacterAssigner, install_detection_patch, reset_assignment_context
    from ..bank.identity import CharacterBank

    manifest_path = Path(manifest_path)
    manifest = read_json(manifest_path)
    config = manifest["config"]
    progress_path = os.environ.get("MANGA_PIPELINE_PROGRESS_PATH")
    device = choose_device(manifest.get("extraction_device", config["device"]), torch.cuda.is_available())
    paths = [Path(p["path"]) for p in manifest["source"]["images"]]
    emit(progress_path, "Loading pages", 0, len(paths), "page")
    for p, item in zip(paths, manifest["source"]["images"]):
        if file_hash(p) != item["sha256"]:
            raise ValueError(f"Source image changed: {p}")
    images = []
    for index, path in enumerate(paths, 1):
        with Image.open(path) as image:
            images.append(np.asarray(image.convert("L").convert("RGB")))
        emit(progress_path, "Loading pages", index, len(paths), "page")
    bank_path = Path(manifest["bank_path"])
    folder = manifest_path.parent / "01_extract"
    folder.mkdir(parents=True, exist_ok=True)
    emit(progress_path, "Loading MAGI")
    model = AutoModel.from_pretrained(MODEL, revision=REVISION, trust_remote_code=True).eval().to(device)
    with CharacterBank(bank_path.parent.parent, manifest["source"]["story"],
                       manifest["source"].get("story_title", manifest["source"]["story"]), model_fingerprint=MODEL_FINGERPRINT) as bank:
        assigner = DynamicCharacterAssigner(bank, [f"{manifest['source']['chapter']}/{p.name}" for p in paths])
        assigner.bind(model)
        install_detection_patch(model, config["text_detection_threshold"],
                                config.get("character_character_matching_threshold", 0.65))
        reset_assignment_context(model)
        with torch.no_grad(), magi_progress(progress_path):
            results = model.do_chapter_wide_prediction(images, {"images": [], "names": []},
                                                       use_tqdm=True, do_ocr=True)
        identity_pages, offset = [], 0
        for result in results:
            count = len(result["characters"])
            identity_pages.append(assigner.last_character_ids[offset:offset + count])
            offset += count
        raw = build_raw_document(images, paths, results, story_id=bank.story_key,
                                 chapter_id=manifest["source"]["chapter"],
                                 model_fingerprint=MODEL_FINGERPRINT, character_ids=identity_pages)
        raw["device"] = device
        transcript = []
        emit(progress_path, "Saving page outputs", 0, len(results), "page")
        for page, result in enumerate(results, 1):
            transcript.append(f"--- Page {page} ---")
            for row in raw["texts"]:
                if row["page"] == page and row["is_essential_text"]:
                    label = row["speaker_id"] if row["speaker_id"] is not None else "Other"
                    transcript.append(f"<{label}>: {row['text']}")
            if config["visualizations"]:
                target = folder / "visualizations" / f"page_{page}.png"
                target.parent.mkdir(exist_ok=True)
                model.visualise_single_image_prediction(images[page - 1], result, str(target))
            emit(progress_path, "Saving page outputs", page, len(results), "page")
        emit(progress_path, "Saving bank")
        bank.save()
        atomic_json(manifest_path.parent / "snapshots" / "after_extract_bank.json", bank.metadata)
        atomic_bytes(folder / "transcript.txt", ("\n".join(transcript) + "\n").encode())
        # The worker receipt permits recovery without a second MAGI call.
        from ..storage.io import fingerprint
        atomic_json(folder / "worker_commit.json", {"output": raw, "hash": fingerprint(raw),
                                                    "transcript_hash": file_hash(folder / "transcript.txt")})
        emit(progress_path, "Committed", 1, 1, "step")
    return raw


if __name__ == "__main__":
    try:
        extract(sys.argv[1])
    except Exception as exc:
        atomic_json(Path(sys.argv[1]).parent / "01_extract" / "worker_error.json", {"error": str(exc)})
        raise
