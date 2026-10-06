"""Page-local MAGI clustering for an entire chapter, without bank writes or OCR."""
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

    source = Path("D:/download/Bocchi-en/Chapter 1")
    output = ROOT / "outputs/diagnostics/bocchi-chapter001-matching-085"
    output.mkdir(parents=True, exist_ok=True)
    images_dir = output / "visualizations"
    images_dir.mkdir(exist_ok=True)
    paths = sorted(source.glob("*.png"))
    assert [p.name for p in paths] == [f"{i:02d}.png" for i in range(1, 32)], "Unexpected chapter images"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"SOURCE={source} IMAGES={len(paths)} THRESHOLD=0.85 DEVICE={device}", flush=True)
    model = AutoModel.from_pretrained(MODEL, revision=REVISION, trust_remote_code=True,
                                     local_files_only=True).eval().to(device)
    captured = {}
    original = model._get_character_character_affinity_matrices

    def capture(*args, **kwargs):
        matrices = original(*args, **kwargs)
        captured["scores"] = matrices[0].detach().cpu().numpy()
        return matrices

    model._get_character_character_affinity_matrices = capture

    def plain(value):
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [plain(v) for v in value]
        return value.tolist() if hasattr(value, "tolist") else value

    def labels(scores, threshold):
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

    pages = []
    for path in paths:
        page_output = output / f"page-{path.stem}.json"
        source_hash = file_hash(path)
        if page_output.exists() and (images_dir / path.name).exists():
            saved = json.loads(page_output.read_text(encoding="utf-8"))
            assert saved["source_sha256"] == source_hash
            summary = saved["summary"]
            print(f"REUSED {path.name}: {summary}", flush=True)
        else:
            with Image.open(path) as image:
                pixels = np.asarray(image.convert("L").convert("RGB"))
            with torch.no_grad():
                result = model.predict_detections_and_associations(
                    [pixels], text_detection_threshold=0.15,
                    character_character_matching_threshold=0.85)[0]
            scores = captured["scores"]
            comparison = {str(t): labels(scores, t) for t in (0.65, 0.75, 0.85)}
            summary = {"image": path.name, "detections": len(result["characters"]),
                       "clusters": {t: len(set(v)) for t, v in comparison.items()}}
            view = dict(result)
            view["character_names"] = [f"cluster-{label}" for label in result["character_cluster_labels"]]
            model.visualise_single_image_prediction(pixels, view, str(images_dir / path.name))
            atomic_json(page_output, {"source": str(path.resolve()), "source_sha256": source_hash,
                        "threshold": 0.85, "result": plain(result), "matching_scores": scores.tolist(),
                        "comparison": comparison, "summary": summary})
            print(f"DONE {path.name}: {summary}", flush=True)
        pages.append(summary)
    report = {"source": str(source.resolve()), "threshold": 0.85, "model": MODEL,
              "revision": REVISION, "device": device, "pages": pages,
              "total_detections": sum(p["detections"] for p in pages),
              "total_page_local_clusters": {str(t): sum(p["clusters"][str(t)] for p in pages)
                                            for t in (0.65, 0.75, 0.85)},
              "scope": "Page-local detect/clustering only. No OCR, bank assignment, analysis or translation. Cluster counts are not unique chapter character counts."}
    atomic_json(output / "result.json", report)
    print("COMPLETE " + json.dumps({k: report[k] for k in ("total_detections", "total_page_local_clusters")}), flush=True)


if __name__ == "__main__":
    main()
