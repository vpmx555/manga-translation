"""Run MAGI detection, classification, clustering and associations without OCR/transcripts."""
import argparse
import hashlib
import html
import json
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
    parser.add_argument("--text-threshold", type=float, default=0.45)
    parser.add_argument("--dialogue-threshold", type=float, default=-0.5)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    log = (args.output / "progress.log").open("w", encoding="utf-8", buffering=1)

    def emit(message):
        line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + message
        print(line, flush=True)
        log.write(line + "\n")

    def save():
        p = args.output / "report.json"
        temp = p.with_suffix(".json.tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, p)

    config = json.loads((ROOT / "configs/pipeline.json").read_text(encoding="utf-8"))
    thresholds = {"text_detection_threshold": args.text_threshold,
                  "text_classification_threshold": args.dialogue_threshold,
                  "character_detection_threshold": config["character_detection_threshold"],
                  "character_character_matching_threshold": config["character_character_matching_threshold"],
                  "panel_detection_threshold": 0.2, "tail_detection_threshold": 0.34,
                  "text_character_matching_threshold": 0.35, "text_tail_matching_threshold": 0.3}
    report = {"kind": "magi_full_no_ocr", "source": str(args.source.resolve()),
              "thresholds": thresholds, "dialogue_score_type": "raw_logit", "device": "cpu",
              "ocr": False, "transcript": False, "bank_updates": False,
              "clustering_scope": "per_page", "status": "loading", "pid": os.getpid(),
              "pages": [], "failures": []}
    save()
    try:
        import numpy as np
        import torch
        from PIL import Image, ImageDraw
        from transformers import AutoModel
        from manga_pipeline.extraction.magi import MODEL, REVISION

        emit("Loading local cached MAGI v2, CPU, no OCR/transcript")
        model = AutoModel.from_pretrained(MODEL, revision=REVISION, trust_remote_code=True,
                                         local_files_only=True).eval().to("cpu")
        captured = {}
        original_classify = model._get_text_classification

        def capture_classification(*a, **kw):
            result = original_classify(*a, **kw)
            captured["logits"] = result[0].detach().cpu().tolist()
            return result

        model._get_text_classification = capture_classification
        namespace = model.predict_detections_and_associations.__func__.__globals__
        original_sort = namespace["sort_text_boxes_in_reading_order"]

        def capture_sort(*a, **kw):
            order = original_sort(*a, **kw)
            captured["order"] = [int(i) for i in order]
            return order

        namespace["sort_text_boxes_in_reading_order"] = capture_sort
        paths = sorted(p for p in args.source.rglob("*") if p.suffix.lower() in
                       {".png", ".jpg", ".jpeg", ".webp"})
        if not paths:
            raise RuntimeError("No input images")
        report.update(model=MODEL, revision=REVISION, expected_pages=len(paths), status="running")
        save()
        for number, path in enumerate(paths, 1):
            relative = path.relative_to(args.source)
            folder = args.output / relative.parent / path.stem
            folder.mkdir(parents=True)
            started = time.monotonic()
            try:
                with Image.open(path) as source:
                    image = source.convert("RGB")
                pixels = np.asarray(image.convert("L").convert("RGB"))
                captured.clear()
                with torch.inference_mode():
                    result = model.predict_detections_and_associations([pixels], **thresholds)[0]
                logits = [captured["logits"][i] for i in captured["order"]]
                assert len(logits) == len(result["texts"])
                assert result["is_essential_text"] == [s > args.dialogue_threshold for s in logits]
                result["dialogue_logits"] = logits
                page = {"source": str(path.resolve()), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "thresholds": thresholds, "result": result}
                (folder / "results.json").write_text(json.dumps(page, indent=2), encoding="utf-8")
                view = dict(result)
                view["character_names"] = [f"cluster-{i}" for i in result["character_cluster_labels"]]
                model.visualise_single_image_prediction(pixels, view, str(folder / "magi.jpg"))
                overlay = image.copy()
                draw = ImageDraw.Draw(overlay)
                crops = model.processor.crop_image(np.asarray(image), result["texts"])
                for i, (box, score, keep, crop) in enumerate(zip(result["texts"], logits, result["is_essential_text"], crops), 1):
                    Image.fromarray(crop).save(folder / f"text-{i:03d}.png")
                    color = "green" if keep else "red"
                    draw.rectangle(box, outline=color, width=3)
                    draw.text((max(0, box[0]), max(0, box[1] - 12)), f"{i}: {score:.2f}", fill=color)
                overlay.save(folder / "dialogue.jpg", quality=95)
                summary = {"page": str(relative), "panels": len(result["panels"]),
                           "texts": len(result["texts"]), "dialogue": sum(result["is_essential_text"]),
                           "characters": len(result["characters"]), "tails": len(result["tails"]),
                           "clusters": len(set(result["character_cluster_labels"])),
                           "speaker_links": len(result["text_character_associations"]),
                           "tail_links": len(result["text_tail_associations"]),
                           "seconds": round(time.monotonic() - started, 2)}
                report["pages"].append(summary)
                emit(f"DONE {number}/{len(paths)} {relative}: {summary}")
            except Exception as exc:
                report["failures"].append({"page": str(relative), "error": repr(exc)})
                emit(f"FAILED {relative}: {exc!r}")
            save()
        namespace["sort_text_boxes_in_reading_order"] = original_sort
        report["status"] = "complete" if not report["failures"] else "partial"
        report["totals"] = {key: sum(page[key] for page in report["pages"])
                            for key in ("panels", "texts", "dialogue", "characters", "tails", "speaker_links", "tail_links")}
        save()
        rows = []
        for page in report["pages"]:
            rel = Path(page["page"])
            base = (rel.parent / rel.stem).as_posix()
            rows.append(f'<h2>{html.escape(str(rel))}</h2><p>{page["texts"]} texts, {page["dialogue"]} dialogue; '
                        f'<a href="{base}/results.json">JSON</a></p>'
                        f'<img loading="lazy" src="{base}/magi.jpg" style="max-width:900px;width:100%">'
                        f'<img loading="lazy" src="{base}/dialogue.jpg" style="max-width:900px;width:100%">')
        (args.output / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>MAGI no OCR</title>'
                                               '<h1>Text detection 0.45; dialogue logit &gt; -0.5</h1>'
                                               '<p>Dialogue view: green=essential; red=non-essential. No OCR/transcript.</p>'
                                               + '\n'.join(rows), encoding="utf-8")
        (args.output / "review.md").write_text('# MAGI without OCR/transcript\n\n'
                                              + json.dumps({"status": report["status"], "thresholds": thresholds,
                                                            "totals": report["totals"], "failures": report["failures"]}, indent=2)
                                              + '\n\nClustering is page-local. See index.html and per-page results.json.\n', encoding="utf-8")
        emit(f"FINISHED {report['status']}: {report['totals']}")
        return 0 if not report["failures"] else 1
    except Exception as exc:
        report.update(status="failed", error=repr(exc))
        save()
        emit(f"FAILED {exc!r}")
        raise
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
