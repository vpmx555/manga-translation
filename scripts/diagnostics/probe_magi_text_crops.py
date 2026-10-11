"""Local detector-only text crop inspection; no OCR, associations or bank writes."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--threshold", type=float, default=0.3)
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1:
        parser.error("threshold must be within [0, 1]")
    args.output.mkdir(parents=True, exist_ok=False)
    log = (args.output / "progress.log").open("w", encoding="utf-8", buffering=1)

    def emit(message):
        line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + message
        print(line, flush=True)
        log.write(line + "\n")

    def save(name, value):
        target = args.output / name
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, target)

    report = {"kind": "magi_text_detection_only", "source": str(args.source.resolve()),
              "threshold": args.threshold, "baseline_threshold": 0.15, "pid": os.getpid(),
              "status": "loading", "pages": [], "failures": []}
    save("report.json", report)
    emit("Loading cached MAGI v2 on CPU; detection only")
    try:
        import numpy as np
        import torch
        from PIL import Image, ImageDraw
        from transformers import AutoModel
        from manga_pipeline.extraction.magi import MODEL, REVISION

        model = AutoModel.from_pretrained(MODEL, revision=REVISION, trust_remote_code=True,
                                         local_files_only=True).eval().to("cpu")
        report.update(model=MODEL, revision=REVISION, device="cpu", status="running")
        paths = sorted(p for p in args.source.rglob("*") if p.suffix.lower() in
                       {".jpg", ".jpeg", ".png", ".webp"})
        if not paths:
            raise RuntimeError("No input images")
        report["expected_pages"] = len(paths)
        save("report.json", report)
        emit(f"READY pages={len(paths)} threshold={args.threshold}")
        for number, path in enumerate(paths, 1):
            started = time.monotonic()
            relative = path.relative_to(args.source)
            page_dir = args.output / relative.parent / path.stem
            page_dir.mkdir(parents=True)
            try:
                with Image.open(path) as image:
                    original = image.convert("RGB")
                pixels = np.asarray(original.convert("L").convert("RGB"))
                with torch.inference_mode():
                    inputs = model.processor.preprocess_inputs_for_detection([pixels])
                    output = model._get_detection_transformer_output(**inputs)
                    logits, boxes = model._get_predicted_bboxes_and_classes(output)
                    scores, labels = logits.max(-1)
                    scores = scores.sigmoid()
                    # Same normalized center-to-corner conversion and filtering as MAGI.
                    centers, sizes = boxes[..., :2], boxes[..., 2:]
                    corners = torch.cat((centers - sizes / 2, centers + sizes / 2), dim=-1)
                    width, height = original.size
                    corners *= torch.tensor([width, height, width, height])
                    selected = model.processor._get_indices_of_texts_to_keep(
                        scores, labels.long(), corners, args.threshold)[0]
                    baseline = model.processor._get_indices_of_texts_to_keep(
                        scores, labels.long(), corners, 0.15)[0]
                overlay = original.copy()
                draw = ImageDraw.Draw(overlay)
                records = []
                for crop_id, query in enumerate(selected, 1):
                    raw = corners[0, query].tolist()
                    # Export exact detected region with integer enclosure, no extra padding.
                    x1, y1 = max(0, math.floor(raw[0])), max(0, math.floor(raw[1]))
                    x2, y2 = min(width, math.ceil(raw[2])), min(height, math.ceil(raw[3]))
                    if x2 <= x1 or y2 <= y1:
                        raise ValueError(f"Invalid box for query {query}: {raw}")
                    crop_name = f"crop-{crop_id:03d}.png"
                    original.crop((x1, y1, x2, y2)).save(page_dir / crop_name)
                    score = float(scores[0, query])
                    draw.rectangle((x1, y1, x2, y2), outline="red", width=2)
                    draw.text((x1, max(0, y1 - 12)), f"{crop_id}: {score:.3f}", fill="red")
                    records.append({"id": crop_id, "query": query, "score": score,
                                    "box": raw, "crop_box": [x1, y1, x2, y2], "file": crop_name})
                overlay.save(page_dir / "boxes.jpg", quality=95)
                page = {"source": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "size": [width, height], "threshold": args.threshold,
                        "count": len(records), "baseline_count": len(baseline),
                        "removed_count": len(baseline) - len(selected), "crops": records,
                        "seconds": round(time.monotonic() - started, 2)}
                (page_dir / "detections.json").write_text(json.dumps(page, indent=2), encoding="utf-8")
                report["pages"].append({"page": str(relative), "count": page["count"],
                                        "baseline_count": page["baseline_count"], "seconds": page["seconds"]})
                emit(f"DONE {number}/{len(paths)} {relative}: crops={len(records)} baseline={len(baseline)} seconds={page['seconds']}")
            except Exception as exc:
                report["failures"].append({"page": str(relative), "error": repr(exc)})
                emit(f"FAILED {relative}: {exc!r}")
            save("report.json", report)
        report["status"] = "complete" if not report["failures"] else "partial"
        report["total_crops"] = sum(p["count"] for p in report["pages"])
        report["baseline_crops"] = sum(p["baseline_count"] for p in report["pages"])
        save("report.json", report)
        # Standalone contact-sheet index points to every page overlay and original crop.
        rows = []
        for page in report["pages"]:
            rel = Path(page["page"])
            folder = rel.parent / rel.stem
            rows.append(f'<h2>{rel} ({page["count"]} crops; baseline {page["baseline_count"]})</h2>'
                        f'<a href="{folder.as_posix()}/detections.json">JSON</a><br>'
                        f'<img loading="lazy" src="{folder.as_posix()}/boxes.jpg" style="max-width:900px;width:100%">')
        (args.output / "index.html").write_text(f'<!doctype html><meta charset="utf-8"><title>MAGI text crops {args.threshold}</title>'
                                               f'<h1>MAGI text detection threshold {args.threshold}</h1>' + '\n'.join(rows), encoding="utf-8")
        (args.output / "review.md").write_text(
            f"# Local MAGI crop inspection\n\nStatus: {report['status']}\n\n"
            f"Pages: {len(report['pages'])}/{len(paths)}; failures: {len(report['failures'])}.\n\n"
            f"Threshold {args.threshold}: {report['total_crops']} crops; baseline 0.15: {report['baseline_crops']}.\n\n"
            "Detection only; no OCR, dialogue classification, speaker association or bank updates.\n"
            "See index.html, per-page boxes.jpg, detections.json and crop PNGs.\n", encoding="utf-8")
        emit(f"FINISHED status={report['status']} crops={report['total_crops']} baseline={report['baseline_crops']}")
        return 0 if not report["failures"] else 1
    except Exception as exc:
        report.update(status="failed", error=repr(exc))
        save("report.json", report)
        emit(f"FAILED {exc!r}")
        raise
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
