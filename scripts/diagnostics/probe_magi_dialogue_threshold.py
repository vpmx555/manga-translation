"""Inspect MAGI dialogue/association scores without OCR or character-bank writes."""
import argparse
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from manga_pipeline.extraction.config import DETECTION_DEFAULTS


def natural(path):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", path.name)]


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--input", type=Path, default=Path("D:/download/EN-data/Iris Zero/EN/Chapter 1"))
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/diagnostics/iris-zero-dialogue-040")
    parser.add_argument("--threshold", type=float, default=DETECTION_DEFAULTS["text_classification_threshold"])
    parser.add_argument("--text-detection-threshold", type=float, default=DETECTION_DEFAULTS["text_detection_threshold"])
    parser.add_argument("--text-character-threshold", type=float, default=DETECTION_DEFAULTS["text_character_matching_threshold"])
    parser.add_argument("--pages", nargs="+", type=int, help="One-based pages; omit for the whole chapter")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    args = parser.parse_args()
    if not math.isfinite(args.threshold):
        parser.error("Dialogue threshold must be a finite logit")
    if not all(0 <= v <= 1 for v in (args.text_detection_threshold, args.text_character_threshold)):
        parser.error("Detection and association thresholds must be 0..1")
    paths = sorted([p for p in args.input.iterdir() if p.is_file() and
                    p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}], key=natural)
    if not paths or args.pages and any(p < 1 or p > len(paths) for p in args.pages):
        parser.error("No images or invalid page numbers")
    selected = [(i, p) for i, p in enumerate(paths, 1) if not args.pages or i in args.pages]
    import numpy as np
    import torch
    from PIL import Image, ImageDraw
    from transformers import AutoModel
    from manga_pipeline.extraction.magi import MODEL, REVISION
    from manga_pipeline.storage.io import atomic_json, file_hash
    device = args.device if args.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Loading MAGI on {device}; pages={len(selected)} threshold={args.threshold}", flush=True)
    model = AutoModel.from_pretrained(MODEL, revision=REVISION, trust_remote_code=True,
                                     local_files_only=True).eval().to(device)
    captured = {}
    classify = model._get_text_classification
    def capture(*a, **kw):
        scores = classify(*a, **kw)
        captured["scores"] = [s.detach().cpu().tolist() for s in scores]
        return scores
    model._get_text_classification = capture
    affinity = model._get_text_character_affinity_matrices
    def capture_affinity(*a, **kw):
        scores = affinity(*a, **kw)
        captured["affinities"] = [s.detach().cpu().tolist() for s in scores]
        return scores
    model._get_text_character_affinity_matrices = capture_affinity
    # MAGI sorts detections after scoring; record that permutation to align scores.
    namespace = model.predict_detections_and_associations.__func__.__globals__
    original_sort = namespace["sort_text_boxes_in_reading_order"]
    def capture_sort(*a, **kw):
        indices = original_sort(*a, **kw)
        captured.setdefault("orders", []).append([int(i) for i in indices])
        return indices
    namespace["sort_text_boxes_in_reading_order"] = capture_sort
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"model": MODEL, "revision": REVISION, "source": str(args.input.resolve()),
              "score_space": "Native _get_text_classification output; apply_sigmoid=False",
              "threshold": args.threshold, "baseline_threshold": 0.5, "device": device,
              "text_detection_threshold": args.text_detection_threshold,
              "text_character_matching_threshold": args.text_character_threshold,
              "association_score_space": "sigmoid probability",
              "ocr": False, "bank_modified": False, "pages": []}
    try:
        for page, path in selected:
            captured.clear()
            with Image.open(path) as image:
                rgb = image.convert("RGB")
                array = np.asarray(rgb.convert("L").convert("RGB"))
            with torch.inference_mode():
                result = model.predict_detections_and_associations([array],
                    text_detection_threshold=args.text_detection_threshold, character_detection_threshold=0.35,
                    character_character_matching_threshold=0.75,
                    text_character_matching_threshold=args.text_character_threshold,
                    text_classification_threshold=args.threshold)[0]
            scores = [captured["scores"][0][i] for i in captured["orders"][0]]
            affinities = [captured["affinities"][0][i] for i in captured["orders"][0]]
            if len(scores) != len(result["texts"]):
                raise ValueError("Score/text ordering mismatch")
            rows = []
            overlay = rgb.copy()
            draw = ImageDraw.Draw(overlay)
            for ci, box in enumerate(result["characters"], 1):
                draw.rectangle([float(v) for v in box], outline="cyan", width=2)
                draw.text((float(box[0]), float(box[1])), f"C{ci}", fill="cyan")
            for index, (box, score, flag) in enumerate(zip(result["texts"], scores, result["is_essential_text"]), 1):
                accepted, baseline = score > args.threshold, score > 0.5
                if bool(flag) != accepted:
                    raise ValueError("Captured scores do not match MAGI classification")
                changed = accepted != baseline
                color = "orange" if changed else "lime" if accepted else "red"
                coords = [float(v) for v in box]
                draw.rectangle(coords, outline=color, width=3)
                links = affinities[index - 1]
                best = max(range(len(links)), key=links.__getitem__) if links else None
                association_score = links[best] if best is not None else None
                linked = association_score is not None and association_score > args.text_character_threshold
                label = f"T{index} D={score:.3f} " + (f"C{best+1} A={association_score:.3f} {'YES' if linked else 'NO'}" if best is not None else "C=none")
                draw.text((coords[0], max(0, coords[1] - 12)), label, fill=color)
                crop = f"crops/page-{page:03d}-text-{index:03d}.png"
                target = args.output / crop
                target.parent.mkdir(exist_ok=True)
                rgb.crop(tuple(coords)).save(target)
                rows.append({"index": index, "box": coords, "score": score,
                    "at_test_threshold": accepted, "at_0_5": baseline, "changed": changed, "crop": crop,
                    "character_scores": links, "best_character_index": best,
                    "association_score": association_score, "association_accepted": linked,
                    "association_at_0_35": association_score is not None and association_score > 0.35})
            overlay.save(args.output / f"page-{page:03d}.png")
            report["pages"].append({"page": page, "source": str(path), "sha256": file_hash(path),
                                    "texts": rows, "characters": result["characters"],
                                    "text_character_associations": result["text_character_associations"]})
            atomic_json(args.output / "result.json", report)
            print(f"Page {page}: texts={len(rows)} changed={sum(r['changed'] for r in rows)}", flush=True)
    finally:
        namespace["sort_text_boxes_in_reading_order"] = original_sort
    rows = [r for p in report["pages"] for r in p["texts"]]
    atomic_json(args.output / "summary.json", {"pages": len(report["pages"]), "texts": len(rows),
        "threshold": args.threshold, "accepted": sum(r["at_test_threshold"] for r in rows),
        "accepted_at_0_5": sum(r["at_0_5"] for r in rows), "changed": sum(r["changed"] for r in rows)})
    review = ["# MAGI threshold probe", "", f"Dialogue logit threshold: {args.threshold}; text detection: {args.text_detection_threshold}; text-character association: {args.text_character_threshold}", "",
              "Character indices in JSON are zero-based; overlay labels are one-based.", "",
              "| Page | Text | Dialogue logit | Dialogue | Character | Association | Linked |", "|---|---|---|---|---|---|---|"]
    for page in report["pages"]:
        for row in page["texts"]:
            review.append(f"| {page['page']} | {row['index']} | {row['score']:.6f} | {row['at_test_threshold']} | {row['best_character_index']} | {row['association_score']} | {row['association_accepted']} |")
    (args.output / "review.md").write_text("\n".join(review) + "\n", encoding="utf-8")
    print(f"Results: {args.output}", flush=True)


if __name__ == "__main__":
    main()
