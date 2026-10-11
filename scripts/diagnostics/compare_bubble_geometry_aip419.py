"""Compare current bubble geometry against AIP419 contours and detector outputs."""
import argparse
from collections import Counter
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--reference", type=Path, default=Path("D:/download/AIP419-master/AIP419-master"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pages", nargs="+", type=int, default=[1, 10])
    parser.add_argument("--all-pages", action="store_true")
    args = parser.parse_args()
    import numpy as np
    import torch
    from PIL import Image, ImageDraw, ImageFont
    from shapely.geometry import Polygon
    from manga_pipeline.storage.io import atomic_json, file_hash
    from manga_pipeline.visual.bubbles import classify_bubble, match_bubble
    from manga_pipeline.visual.koharu import Koharu

    torch.set_num_threads(min(8, os.cpu_count() or 1))
    spec = importlib.util.spec_from_file_location("aip419_detector_comparison", args.reference / "core/detector.py")
    reference = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = reference
    spec.loader.exec_module(reference)
    args.output.mkdir(parents=True, exist_ok=True)
    report = json.loads((args.probe / "result.json").read_text(encoding="utf-8"))
    pairs, mappings = [], []
    magi = json.loads((ROOT / "outputs/diagnostics/after-school-chapter-001-d045-dialogue-m050-assoc025/result.json").read_text(encoding="utf-8"))
    for page in report["pages"]:
        assert file_hash(page["source"]) == page["source_sha256"]
        pixels = np.asarray(Image.open(page["source"]).convert("RGB"))
        originals, alternatives, masks = [], [], []
        for bubble in page["bubbles"]:
            mask = np.asarray(Image.open(args.probe / bubble["mask"]).convert("L")) > 0
            ys, xs = np.nonzero(mask)
            # AIP419 stores a tight mask crop plus an absolute origin.
            x, y = int(xs.min()), int(ys.min())
            cropped = mask[y:int(ys.max())+1, x:int(xs.max())+1]
            contour = reference._mask_to_contour(cropped, (x, y))
            geometry = classify_bubble(contour, pixels)
            old_poly = Polygon(bubble["contour"])
            new_poly = Polygon(contour) if len(contour) >= 3 else Polygon()
            valid = old_poly.is_valid and new_poly.is_valid and not new_poly.is_empty
            iou = old_poly.intersection(new_poly).area / old_poly.union(new_poly).area if valid else None
            pairs.append({"page": page["page"], "bubble": bubble["id"], "detection_score": bubble["confidence"],
                          "current_contour": bubble["contour"], "aip419_contour": contour,
                          "current_geometry": bubble["geometry"],
                          "current_classifier_on_aip419_contour": geometry,
                          "polygon_iou": iou, "current_area": old_poly.area, "aip419_area": new_poly.area,
                          "current_vertices": len(bubble["contour"]), "aip419_vertices": len(contour)})
            originals.append(bubble)
            alternatives.append({**bubble, "contour": contour, "geometry": geometry})
            masks.append(mask)
        for text in next(p for p in magi["pages"] if p["page"] == page["page"])["texts"]:
            before = match_bubble(text["box"], originals, masks, report["config"])
            after = match_bubble(text["box"], alternatives, masks, report["config"])
            mappings.append({"page": page["page"], "text": text["index"], "box": text["box"],
                             "current": before, "with_aip419_contour": after})
    summary = {"pages": len(report["pages"]), "bubbles": len(pairs),
               "style_changes": sum(p["current_geometry"]["bubble_style"] != p["current_classifier_on_aip419_contour"]["bubble_style"] for p in pairs),
               "shape_changes": sum(p["current_geometry"]["bubble_shape"] != p["current_classifier_on_aip419_contour"]["bubble_shape"] for p in pairs),
               "border_changes": sum(p["current_geometry"]["border_style"] != p["current_classifier_on_aip419_contour"]["border_style"] for p in pairs),
               "style_transitions": dict(Counter(p["current_geometry"]["bubble_style"] + " -> " + p["current_classifier_on_aip419_contour"]["bubble_style"] for p in pairs)),
               "mean_polygon_iou": sum(p["polygon_iou"] for p in pairs if p["polygon_iou"] is not None) / sum(p["polygon_iou"] is not None for p in pairs),
               "mapped_texts": sum(p["current"]["bubble_id"] is not None for p in mappings),
               "text_mapping_changes": sum(p["current"]["bubble_id"] != p["with_aip419_contour"]["bubble_id"] for p in mappings),
               "note": "AIP419 has no style classifier: alternative labels use our classifier on its contours, not native AIP419 labels."}
    atomic_json(args.output / "contour-comparison.json", {"summary": summary, "bubbles": pairs, "texts": mappings})
    print("Same-mask comparison", json.dumps(summary), flush=True)
    config = copy.deepcopy(report["config"])
    backend = Koharu(config)
    optimized = False
    try:
        backend.model.optimize_for_inference(compile=False)
        optimized = True
    except Exception as exc:
        print("Reference loader optimization unavailable", str(exc), flush=True)
    detector = reference.MangaDetector(device="cpu")
    detector._koharu_model = backend.model
    original_predict = backend.model.predict
    def predict(*a, **kw):
        start = time.monotonic()
        result = original_predict(*a, **kw)
        print(f"Reference model pass seconds={time.monotonic()-start:.1f}", flush=True)
        return result
    backend.model.predict = predict
    comparisons = []
    checkpoint = args.output / "detector-comparison.json"
    if checkpoint.exists():
        previous = json.loads(checkpoint.read_text(encoding="utf-8"))
        if previous["model"] != config["model"] or previous["revision"] != config["revision"]:
            raise ValueError("Existing detector results use a different checkpoint")
        if previous["reference_thresholds"] != {str(k):v for k,v in reference.KOHARU_CLASS_THRESHOLDS.items()}:
            raise ValueError("Existing reference thresholds differ")
        comparisons = previous["pages"]
    completed = {p["page"] for p in comparisons}
    for page in report["pages"]:
        if not args.all_pages and page["page"] not in args.pages:
            continue
        if page["page"] in completed:
            print(f"AIP419 page {page['page']} reused existing pinned-model result", flush=True)
            continue
        print(f"AIP419 page {page['page']} start", flush=True)
        image = Image.open(page["source"]).convert("RGB")
        detected, panels = detector.detect_with_panels(image)
        bubbles = [b for b in detected if b.label == "bubble"]
        records = []
        overlay = image.copy()
        draw = ImageDraw.Draw(overlay)
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", max(14, image.width // 65))
        for i, b in enumerate(bubbles, 1):
            record = {"id": i, "bbox": b.bbox, "confidence": b.confidence, "panel_id": b.panel_id,
                      "bubble_id": b.bubble_id, "contour": b.bubble_contour,
                      "mask_origin": b.bubble_mask_origin}
            records.append(record)
            draw.rectangle(b.bbox, outline="cyan", width=3)
            if b.bubble_contour:
                points = [tuple(p) for p in b.bubble_contour]
                draw.line(points+[points[0]], fill="cyan", width=3)
            draw.text((b.bbox[0], max(0,b.bbox[1]-25)), f"A{i} {b.confidence:.3f}", fill="red", font=font)
        overlay.save(args.output / f"aip419-page-{page['page']:03d}.png")
        comparisons.append({"page": page["page"], "current_bubbles": len(page["bubbles"]),
                            "source": page["source"], "source_sha256": page["source_sha256"],
                            "aip419_bubble_records": len(records), "aip419_panels": len(panels),
                            "aip419_text_records": sum(b.label != "bubble" for b in detected),
                            "bubbles": records})
        atomic_json(args.output / "detector-comparison.json", {"model": config["model"], "revision": config["revision"],
                    "optimization_compile_false": optimized, "pages": sorted(comparisons, key=lambda p:p["page"]),
                    "reference_thresholds": reference.KOHARU_CLASS_THRESHOLDS,
                    "note": "Shared pinned model; executes AIP419 detector method. Native unpinned checkpoint loader not used."})
        print(f"AIP419 page {page['page']} complete: {len(records)} bubble records vs current {len(page['bubbles'])}", flush=True)
    from render_bubble_comparison import render
    render(args.probe, args.output, args.output / "side-by-side")


if __name__ == "__main__":
    main()
