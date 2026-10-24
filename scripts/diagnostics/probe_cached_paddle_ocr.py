"""Replay verified, cached chapter crops with PaddleOCR and compare to Hayai."""
import argparse
import copy
import hashlib
import html
import importlib.metadata
import json
import os
import shutil
import sys
import time
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def normalized(text):
    return " ".join(text.split())


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", choices=("v5-mobile", "v5-server", "v6-medium"), default="v5-mobile")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--recognition-batch-size", type=int, default=16)
    parser.add_argument("--det-limit-side-len", type=int)
    parser.add_argument("--det-limit-type", choices=("min", "max"))
    parser.add_argument("--enable-mkldnn", action="store_true")
    parser.add_argument("--source-run", type=Path, help="Verified extraction location, including on another host")
    parser.add_argument("--compare-paddle", type=Path, help="Completed earlier Paddle diagnostic directory")
    args = parser.parse_args()
    model_cache = Path(__file__).resolve().parents[2] / "models" / "paddleocr"
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(model_cache))
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "bos")
    from PIL import Image, ImageDraw
    import numpy as np
    from paddleocr import PaddleOCR

    baseline_path = args.baseline / "result.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline["status"] != "completed" or baseline["completed"] != baseline["targets"]:
        raise ValueError("Hayai baseline is incomplete")
    raw_path = args.baseline / "ocr-result.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    contract = json.loads((args.baseline / "request.json").read_text(encoding="utf-8"))
    source_run = args.source_run or Path(contract["source_run"])
    extraction = next(source_run.glob("[0-9][0-9]_extract/result.json"))
    if file_hash(extraction) != contract["extract_hash"]:
        raise ValueError("Original extraction hash changed")
    variants = {
        "v5-mobile": ("PP-OCRv5_mobile_det", "en_PP-OCRv5_mobile_rec"),
        "v5-server": ("PP-OCRv5_server_det", "PP-OCRv5_server_rec"),
        "v6-medium": ("PP-OCRv6_medium_det", "PP-OCRv6_medium_rec"),
    }
    detection_model, recognition_model = variants[args.variant]
    config = {
        "backend": "paddleocr", "paddleocr_version": importlib.metadata.version("paddleocr"),
        "paddlepaddle_version": importlib.metadata.version("paddlepaddle"),
        "text_detection_model_name": detection_model,
        "text_recognition_model_name": recognition_model,
        "device": args.device, "cpu_threads": args.cpu_threads,
        "text_recognition_batch_size": args.recognition_batch_size,
        "use_doc_orientation_classify": False, "use_doc_unwarping": False,
        "use_textline_orientation": False, "text_rec_score_thresh": 0.0,
        "enable_mkldnn": args.enable_mkldnn,
    }
    if args.det_limit_side_len is not None:
        config["text_det_limit_side_len"] = args.det_limit_side_len
    if args.det_limit_type is not None:
        config["text_det_limit_type"] = args.det_limit_type
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ("crops", "lines", "cache"):
        (args.output / name).mkdir(exist_ok=True)
    atomic_json(args.output / "environment.json", {
        "python": sys.version, "executable": sys.executable,
        "packages": dict(sorted((dist.metadata["Name"], dist.version)
                                for dist in importlib.metadata.distributions()
                                if dist.metadata["Name"])),
    })
    rows = []
    previous = None
    if args.compare_paddle:
        previous = json.loads((args.compare_paddle / "result.json").read_text(encoding="utf-8"))
        if previous["status"] != "completed" or previous["completed"] != previous["targets"]:
            raise ValueError("Earlier Paddle diagnostic is incomplete")
        previous = {row["id"]: row for row in previous["results"]}
    for old in baseline["results"]:
        crop = args.baseline / old["crop"]
        with Image.open(crop) as image:
            pixels = np.asarray(image.convert("RGB"))
        if hashlib.sha256(pixels.tobytes()).hexdigest() != old["crop_sha256"]:
            raise ValueError("Cached crop pixel hash mismatch: " + old["id"])
        row = {key: old[key] for key in ("id", "page", "text_index", "bbox", "crop", "crop_sha256", "is_essential_text")}
        row["hayai_text"] = old["new_text"]
        if previous is not None:
            earlier = previous[old["id"]]
            if earlier["crop_sha256"] != old["crop_sha256"] or earlier["bbox"] != old["bbox"]:
                raise ValueError("Earlier Paddle diagnostic uses different crops")
            row["previous_paddle_text"] = earlier["paddle_text"]
        row["file_sha256"] = file_hash(crop)
        shutil.copyfile(crop, args.output / old["crop"])
        rows.append(row)
    request = {"policy": "paddle-cached-bubble-lines-v1", "config": config,
               "baseline": str(args.baseline.resolve()), "baseline_sha256": file_hash(baseline_path),
               "baseline_ocr_sha256": file_hash(raw_path), "extract_hash": contract["extract_hash"],
               "targets": rows}
    if args.compare_paddle:
        request["previous_paddle_sha256"] = file_hash(args.compare_paddle / "result.json")
    request_hash = digest(request)
    request_path = args.output / "request.json"
    if request_path.exists() and digest(json.loads(request_path.read_text(encoding="utf-8"))) != request_hash:
        raise ValueError("Diagnostic inputs changed; choose a new output directory")
    atomic_json(request_path, request)
    report = {"status": "running", "config": config, "pages": baseline["pages"],
              "targets": len(rows), "completed": 0, "request_hash": request_hash, "results": rows}
    atomic_json(args.output / "result.json", report)
    engine = None
    started = time.monotonic()
    for index, row in enumerate(rows):
        cache = args.output / "cache" / (row["crop_sha256"] + ".json")
        if cache.exists():
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if saved["request_hash"] != request_hash or saved["result_hash"] != digest(saved["result"]):
                raise ValueError("Invalid PaddleOCR cache: " + row["id"])
            payload = saved["result"]
        else:
            if engine is None:
                print("Loading PaddleOCR models: " + args.variant, flush=True)
                engine = PaddleOCR(**config_to_engine(config))
                assets = []
                for name in (config["text_detection_model_name"], config["text_recognition_model_name"]):
                    directory = Path(os.environ["PADDLE_PDX_CACHE_HOME"]) / "official_models" / name
                    for asset in sorted(directory.rglob("*")):
                        if asset.is_file():
                            assets.append({"path": str(asset), "sha256": file_hash(asset)})
                atomic_json(args.output / "model-assets.json", assets)
            try:
                predictions = list(engine.predict(str(args.output / row["crop"])))
            except Exception as exc:
                report.update(status="failed", failed_id=row["id"],
                              error=type(exc).__name__ + ": " + str(exc))
                atomic_json(args.output / "result.json", report)
                raise
            if len(predictions) != 1:
                raise ValueError("Expected exactly one OCR result per crop")
            prediction = predictions[0]
            texts = list(prediction["rec_texts"])
            scores = list(prediction["rec_scores"])
            boxes = list(prediction["rec_boxes"])
            if not len(texts) == len(scores) == len(boxes):
                raise ValueError("PaddleOCR line alignment mismatch")
            lines = [{"text": str(text), "score": float(score), "bbox": [int(x) for x in box]}
                     for text, score, box in zip(texts, scores, boxes)]
            payload = {"text": normalized(" ".join(texts)), "lines": lines,
                       "text_det_params": dict(prediction["text_det_params"])}
            atomic_json(cache, {"request_hash": request_hash, "result": payload, "result_hash": digest(payload)})
        row["paddle_text"] = payload["text"]
        row["lines"] = payload["lines"]
        if "text_det_params" in payload:
            row["text_det_params"] = payload["text_det_params"]
        row["content_changed"] = normalized(row["hayai_text"]) != normalized(row["paddle_text"])
        row["line_overlay"] = "lines/" + Path(row["crop"]).name
        with Image.open(args.output / row["crop"]) as image:
            image = image.convert("RGB")
            draw = ImageDraw.Draw(image)
            for number, line in enumerate(row["lines"], 1):
                draw.rectangle(line["bbox"], outline="red", width=1)
                draw.text((line["bbox"][0], line["bbox"][1]), str(number), fill="blue")
            image.save(args.output / row["line_overlay"])
        report["completed"] = index + 1
        report["elapsed_seconds"] = time.monotonic() - started
        atomic_json(args.output / "result.json", report)
        if (index + 1) % 16 == 0 or index + 1 == len(rows):
            print(f"PaddleOCR {index + 1}/{len(rows)} crops", flush=True)
    indexed = {row["id"]: row for row in rows}
    updated = copy.deepcopy(raw)
    for text in updated["texts"]:
        text["text"] = indexed[text["id"]]["paddle_text"]
    updated["ocr"] = config
    updated["diagnostic"] = {"scope": "OCR only; original MAGI boxes preserved",
                             "source_run": str(source_run), "request_hash": request_hash}
    atomic_json(args.output / "ocr-result.json", updated)
    report["status"] = "completed"
    report["changed"] = sum(row["content_changed"] for row in rows)
    report["empty"] = sum(not row["paddle_text"] for row in rows)
    report["essential_empty"] = sum(not row["paddle_text"] and row["is_essential_text"] for row in rows)
    report["essential_changed"] = sum(row["content_changed"] and row["is_essential_text"] for row in rows)
    report["alphanumeric_changed"] = sum(
        "".join(c for c in row["hayai_text"].casefold() if c.isalnum()) !=
        "".join(c for c in row["paddle_text"].casefold() if c.isalnum()) for row in rows)
    atomic_json(args.output / "result.json", report)
    sections, transcript = [], []
    for row in rows:
        label = f'P{row["page"]:02d} T{row["text_index"] + 1}'
        flag = "khác" if row["content_changed"] else "giống"
        cells = [html.escape(label + " · " + flag), f'<img loading="lazy" src="{row["crop"]}">',
                 html.escape(row["hayai_text"])]
        if previous is not None:
            cells.append(html.escape(row["previous_paddle_text"]))
        cells.extend([html.escape(row["paddle_text"]),
                      f'<img loading="lazy" src="{row["line_overlay"]}">'])
        sections.append('<tr id="p' + str(row["page"]) + '-t' + str(row["text_index"] + 1) + '">' +
                        "".join("<td>" + cell + "</td>" for cell in cells) + "</tr>")
        transcript.append(label + "\n" + row["paddle_text"])
    summary = {key: report[key] for key in ("pages", "targets", "completed", "changed", "empty", "essential_empty", "elapsed_seconds")}
    header = '<!doctype html><meta charset="utf-8"><title>Iris Zero — PaddleOCR vs Hayai</title><style>body{font:16px Arial;margin:24px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #bbb;padding:10px;vertical-align:top;white-space:pre-wrap}img{max-width:300px;max-height:380px}td{width:23%}td:first-child{width:8%}</style>'
    notice = 'Cùng crop MAGI đã xác minh SHA256. PaddleOCR tách dòng bên trong crop, giữ thứ tự dòng do engine trả về. Hayai là bản đối chiếu, không phải ground truth. Số câu khác không phải tỷ lệ lỗi.'
    previous_heading = '<th>Paddle trước</th>' if previous is not None else ''
    (args.output / "review.html").write_text(header + '<h1>Iris Zero chương 1 — ' + html.escape(args.variant) + ' / Hayai</h1><p>' + notice + '</p><pre>' + html.escape(json.dumps({"summary": summary, "config": config}, ensure_ascii=False, indent=2)) + '</pre><table><tr><th>Vùng</th><th>Crop chung</th><th>Hayai</th>' + previous_heading + '<th>PaddleOCR mới</th><th>Vùng dòng Paddle</th></tr>' + "".join(sections) + '</table>', encoding="utf-8")
    (args.output / "transcript.txt").write_text("\n\n".join(transcript) + "\n", encoding="utf-8")
    print(json.dumps(summary), flush=True)


def config_to_engine(config):
    return {key: value for key, value in config.items()
            if key not in {"backend", "paddleocr_version", "paddlepaddle_version"}}


if __name__ == "__main__":
    main()
