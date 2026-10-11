"""Compare page-local MAGI clusters over story/EN/Chapter folders."""
import argparse
import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
THRESHOLDS = (0.8, 0.85)


def natural(path):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", path.name)]


def labels(scores, threshold):
    parent = list(range(len(scores)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(scores)):
        for j in range(i):
            if scores[i][j] > threshold:
                parent[find(i)] = find(j)
    roots = {}
    return [roots.setdefault(find(i), len(roots)) for i in range(len(scores))]


def plain(value):
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value.tolist() if hasattr(value, "tolist") else value


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--story", help="Run only this story and merge its reports into the dataset summary")
    args = parser.parse_args()
    source = args.input.resolve()
    chapters = sorted(source.glob("*/EN/Chapter *"), key=lambda p: (p.parent.parent.name, natural(p)))
    chapters = [p for p in chapters if p.is_dir()]
    if args.story:
        chapters = [p for p in chapters if p.parent.parent.name == args.story]
    if not chapters:
        raise ValueError(f"No story/EN/Chapter folders in {source}")
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoModel
    from manga_pipeline.extraction.magi import MODEL, REVISION
    from manga_pipeline.storage.io import atomic_json, file_hash

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModel.from_pretrained(MODEL, revision=REVISION, trust_remote_code=True,
                                     local_files_only=True).eval().to(device)
    captured = {}
    original = model._get_character_character_affinity_matrices

    def capture(*a, **kw):
        matrices = original(*a, **kw)
        captured["scores"] = matrices[0].detach().cpu().numpy()
        return matrices

    model._get_character_character_affinity_matrices = capture
    totals = {t: [] for t in THRESHOLDS}
    failures = []
    print(f"START source={source} chapters={len(chapters)} device={device}", flush=True)
    for chapter in chapters:
        paths = sorted([p for p in chapter.iterdir() if p.is_file() and
                        p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".avif"}], key=natural)
        if not paths:
            failures.append({"source": str(chapter), "error": "No supported image pages found"})
            print(f"ERROR {chapter}: No supported image pages found", flush=True)
        outputs = {t: source.parent / str(t) / source.name / chapter.relative_to(source) for t in THRESHOLDS}
        pages = {t: [] for t in THRESHOLDS}
        for out in outputs.values():
            (out / "visualizations").mkdir(parents=True, exist_ok=True)
        for path in paths:
            try:
                digest = file_hash(path)
                cached = None
                for t, out in outputs.items():
                    jp = out / f"page-{path.name}.json"
                    if jp.exists():
                        saved = json.loads(jp.read_text(encoding="utf-8"))
                        if (saved.get("source_sha256") == digest and saved.get("model") == MODEL
                                and saved.get("revision") == REVISION):
                            cached = saved
                            break
                with Image.open(path) as image:
                    pixels = np.asarray(image.convert("L").convert("RGB"))
                if cached:
                    result, scores = cached["result"], cached["matching_scores"]
                else:
                    captured.clear()
                    with torch.no_grad():
                        result = model.predict_detections_and_associations(
                            [pixels], text_detection_threshold=0.15,
                            character_character_matching_threshold=0.85)[0]
                    scores = captured["scores"].tolist()
                if len(scores) != len(result["characters"]):
                    raise ValueError("Affinity matrix/detection count mismatch")
                for t, out in outputs.items():
                    view = copy.deepcopy(result)
                    view["character_cluster_labels"] = labels(scores, t)
                    view["character_names"] = [f"cluster-{n}" for n in view["character_cluster_labels"]]
                    summary = {"image": path.name, "detections": len(view["characters"]),
                               "clusters": len(set(view["character_cluster_labels"]))}
                    model.visualise_single_image_prediction(pixels, view, str(out / "visualizations" / (path.name + ".png")))
                    atomic_json(out / f"page-{path.name}.json", {
                        "source": str(path), "source_sha256": digest, "threshold": t,
                        "model": MODEL, "revision": REVISION, "result": plain(view),
                        "matching_scores": scores, "summary": summary})
                    pages[t].append(summary)
                print(f"DONE {chapter.relative_to(source)}/{path.name}", flush=True)
            except Exception as exc:
                failures.append({"source": str(path), "error": str(exc)})
                print(f"ERROR {path}: {exc}", flush=True)
        for t, out in outputs.items():
            report = {"source": str(chapter), "threshold": t, "model": MODEL, "revision": REVISION,
                      "device": device, "expected_pages": len(paths), "completed_pages": len(pages[t]),
                      "pages": pages[t], "total_detections": sum(p["detections"] for p in pages[t]),
                      "total_page_local_clusters": sum(p["clusters"] for p in pages[t]),
                      "scope": "Page-local clusters; counts are not unique chapter characters."}
            atomic_json(out / "result.json", report)
            totals[t].append(report)
    for t in THRESHOLDS:
        summary_path = source.parent / str(t) / source.name / "result.json"
        reports = totals[t]
        report_failures = list(failures)
        if args.story and summary_path.exists():
            previous = json.loads(summary_path.read_text(encoding="utf-8"))
            story_path = source / args.story
            def belongs_to_story(item):
                return Path(item["source"]).is_relative_to(story_path)
            reports = [r for r in previous.get("chapters", []) if not belongs_to_story(r)] + reports
            report_failures = [r for r in previous.get("failures", []) if not belongs_to_story(r)] + report_failures
        incomplete = report_failures or any(
            r["expected_pages"] == 0 or r["completed_pages"] != r["expected_pages"] for r in reports)
        atomic_json(summary_path, {
            "source": str(source), "threshold": t, "chapters": reports, "failures": report_failures,
            "status": "incomplete" if incomplete else "complete"})
    print(f"COMPLETE chapters={len(chapters)} failures={len(failures)}", flush=True)
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
