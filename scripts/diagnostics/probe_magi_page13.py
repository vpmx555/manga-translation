"""Compare MAGI page-local clusters on exactly Bocchi Chapter 1/13.png."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main():
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoModel
    from manga_pipeline.extraction.magi import MODEL, REVISION
    from manga_pipeline.storage.io import atomic_json, file_hash

    source = Path("D:/download/Bocchi-en/Chapter 1/13.png")
    output = ROOT / "outputs/diagnostics/bocchi-page13-matching-085"
    output.mkdir(parents=True, exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"SOURCE={source.resolve()} SHA256={file_hash(source)} DEVICE={device}", flush=True)
    with Image.open(source) as image:
        pixels = np.asarray(image.convert("L").convert("RGB"))
    model = AutoModel.from_pretrained(MODEL, revision=REVISION, trust_remote_code=True,
                                     local_files_only=True).eval().to(device)
    # Reuse the exact same predicted affinity matrix for both thresholds.
    captured = {}
    original = model._get_character_character_affinity_matrices

    def capture(*args, **kwargs):
        matrices = original(*args, **kwargs)
        captured["scores"] = matrices[0].detach().cpu().numpy()
        return matrices

    model._get_character_character_affinity_matrices = capture
    with torch.no_grad():
        result = model.predict_detections_and_associations(
            [pixels], text_detection_threshold=0.15,
            character_character_matching_threshold=0.85)[0]

    def plain(value):
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [plain(v) for v in value]
        if hasattr(value, "tolist"):
            return value.tolist()
        return value

    scores = captured["scores"]

    def labels(threshold):
        parent = list(range(len(scores)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(scores)):
            for j in range(i):
                if scores[i, j] > threshold:
                    parent[find(i)] = find(j)
        roots = {}
        return [roots.setdefault(find(i), len(roots)) for i in range(len(scores))]

    comparisons = {}
    for threshold in (0.65, 0.75, 0.85):
        view = dict(result)
        view["character_cluster_labels"] = labels(threshold)
        view["character_names"] = [f"cluster-{label}" for label in view["character_cluster_labels"]]
        model.visualise_single_image_prediction(pixels, view, str(output / f"clusters-{threshold:.2f}.png"))
        comparisons[str(threshold)] = {
            "labels": view["character_cluster_labels"],
            "clusters": len(set(view["character_cluster_labels"])),
        }
    atomic_json(output / "result.json", {
        "source": str(source.resolve()), "source_sha256": file_hash(source),
        "device": device, "model": MODEL, "revision": REVISION,
        "result_085": plain(result), "matching_scores": scores.tolist(),
        "comparison": comparisons,
        "scope": "Single-image detection/clustering only; no OCR or persistent bank writes.",
    })
    print(json.dumps({"output": str(output), "comparison": comparisons}), flush=True)


if __name__ == "__main__":
    main()
