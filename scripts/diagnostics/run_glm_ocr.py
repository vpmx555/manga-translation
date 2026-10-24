"""Frozen GPU-only GLM-OCR replay of verified, previously extracted manga crops."""
import argparse
import copy
import hashlib
import html
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import time
import traceback


def sha(path):
    with Path(path).open("rb") as stream:
        value = hashlib.file_digest(stream, "sha256")
    return value.hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(pending, path)


def normalized(value):
    return " ".join(value.split())


def review(output, report):
    rows = report["results"]
    cells, transcript = [], []
    for row in rows:
        label = f'P{row["page"]:02d} T{row["text_index"] + 1}'
        values = [html.escape(label + " · " + row["status"]),
                  f'<img loading="lazy" src="{row["crop"]}">',
                  html.escape(row["hayai_text"]), html.escape(row["paddle_mobile_text"]),
                  html.escape(row["paddle_medium_text"]),
                  html.escape(row.get("raw_text", "")), html.escape(row.get("error", ""))]
        cells.append(f'<tr id="p{row["page"]}-t{row["text_index"] + 1}">' +
                     "".join("<td>" + value + "</td>" for value in values) + "</tr>")
        transcript.append(label + "\n" + row.get("raw_text", ""))
    summary = {key: report[key] for key in ("status", "pages", "targets", "completed", "succeeded", "failed", "empty", "truncated", "elapsed_seconds")}
    header = '<!doctype html><meta charset="utf-8"><title>Iris Zero — GLM-OCR GPU</title><style>body{font:16px Arial;margin:20px}table{border-collapse:collapse;min-width:1500px;width:100%}td,th{border:1px solid #bbb;padding:8px;vertical-align:top;white-space:pre-wrap;min-width:130px}img{max-width:240px;max-height:360px}</style>'
    notice = 'Cùng 283 crop MAGI. Văn bản GLM hiển thị nguyên xuống dòng; JSON có thêm bản chuẩn hóa khoảng trắng. Các engine khác là bản đối chiếu, không phải ground truth. Vùng lỗi/rỗng/cắt do giới hạn token được giữ nguyên để review.'
    (output / "review.html").write_text(header + '<h1>Iris Zero Chapter 1 — GLM-OCR</h1><p>' + notice + '</p><pre>' + html.escape(json.dumps(summary, ensure_ascii=False, indent=2)) + '</pre><table><tr><th>Vùng</th><th>Crop</th><th>Hayai</th><th>Paddle v5 mobile</th><th>Paddle v6 medium mặc định</th><th>GLM-OCR</th><th>Lỗi</th></tr>' + "".join(cells) + '</table>', encoding="utf-8")
    (output / "transcript.txt").write_text("\n\n".join(transcript) + "\n", encoding="utf-8")
    (output / "review.md").write_text('# GLM-OCR — Iris Zero Chapter 1\n\n```json\n' + json.dumps(summary, ensure_ascii=False, indent=2) + '\n```\n\n[Review crop và các engine](review.html)\n\n[Transcript](transcript.txt)\n', encoding="utf-8")


def run(input_dir, output, config):
    import numpy as np
    from PIL import Image
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoProcessor, GlmOcrForConditionalGeneration

    output.mkdir(parents=True, exist_ok=True)
    targets = json.loads((input_dir / "targets.json").read_text(encoding="utf-8"))
    rows = copy.deepcopy(targets)
    for row in rows:
        crop = input_dir / row["crop"]
        if sha(crop) != row["file_sha256"]:
            raise ValueError("Crop file hash mismatch: " + row["id"])
        with Image.open(crop) as image:
            pixels = np.asarray(image.convert("RGB"))
        if hashlib.sha256(pixels.tobytes()).hexdigest() != row["crop_sha256"]:
            raise ValueError("Crop pixel hash mismatch: " + row["id"])
        target = output / row["crop"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(crop, target)
        row["status"] = "pending"
    request_hash = fingerprint({"config": config, "targets": targets})
    save(output / "request.json", {"config": config, "targets": targets, "request_hash": request_hash})
    report = {"status": "running", "pages": len({row["page"] for row in rows}),
              "targets": len(rows), "completed": 0, "succeeded": 0, "failed": 0,
              "empty": 0, "truncated": 0, "elapsed_seconds": 0,
              "request_hash": request_hash, "config": config, "results": rows,
              "events": [], "last_progress_at": time.time()}
    save(output / "result.json", report)
    started = time.monotonic()
    (output / "cache").mkdir(exist_ok=True)
    try:
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise RuntimeError("Exactly one working CUDA GPU is required; CPU fallback is disabled")
        if not torch.cuda.is_bf16_supported():
            raise RuntimeError("Selected GPU must support bfloat16")
        probe = torch.ones((64, 64), device="cuda", dtype=torch.bfloat16)
        if float((probe @ probe)[0, 0].item()) != 64:
            raise RuntimeError("CUDA matrix multiplication failed")
        save(output / "environment.json", {
            "gpu": torch.cuda.get_device_name(0), "cuda": torch.version.cuda,
            "torch": torch.__version__, "cuda_matmul_verified": True,
            "packages": dict(sorted((dist.metadata["Name"], dist.version)
                                    for dist in importlib.metadata.distributions()
                                    if dist.metadata["Name"])),
        })
        print("CUDA verified; downloading frozen GLM-OCR revision", flush=True)
        model_dir = Path(snapshot_download(config["model"], revision=config["revision"],
                                          allow_patterns=["*.json", "*.safetensors", "*.jinja"]))
        save(output / "model-assets.json", [{"name": path.name, "sha256": sha(path), "bytes": path.stat().st_size}
                                             for path in sorted(model_dir.iterdir()) if path.is_file()])
        processor = AutoProcessor.from_pretrained(model_dir)
        processor.tokenizer.padding_side = "left"
        model = GlmOcrForConditionalGeneration.from_pretrained(
            model_dir, dtype=torch.bfloat16, attn_implementation="sdpa").eval().to("cuda")
        if any(parameter.device.type != "cuda" for parameter in model.parameters()):
            raise RuntimeError("Model parameters were offloaded from CUDA")
        torch.cuda.reset_peak_memory_stats()

        def infer(batch):
            messages = [[{"role": "user", "content": [
                {"type": "image", "url": str((input_dir / row["crop"]).resolve())},
                {"type": "text", "text": config["prompt"]}]}] for row in batch]
            inputs = processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                                  padding=True, return_dict=True, return_tensors="pt").to("cuda")
            width = inputs["input_ids"].shape[1]
            with torch.inference_mode():
                generated = model.generate(**inputs, max_new_tokens=config["max_new_tokens"],
                                           do_sample=False, return_dict_in_generate=False)
            new_ids = generated[:, width:]
            decoded = processor.batch_decode(new_ids, skip_special_tokens=True,
                                             clean_up_tokenization_spaces=False)
            if len(decoded) != len(batch):
                raise ValueError("GLM output count does not match crop batch")
            eos = model.generation_config.eos_token_id
            eos = {eos} if isinstance(eos, int) else set(eos or [])
            values = []
            for text, ids in zip(decoded, new_ids.tolist()):
                length = next((index for index, token in enumerate(ids) if token in eos), len(ids))
                values.append({"raw_text": text, "glm_text": normalized(text), "output_tokens": length,
                               "truncated": length >= config["max_new_tokens"], "status": "success"})
            return values

        cursor, cap = 0, config["batch_size"]
        while cursor < len(rows):
            batch = rows[cursor:cursor + cap]
            cached = []
            for row in batch:
                path = output / "cache" / (fingerprint([request_hash, row["id"]]) + ".json")
                value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
                if value and (value["request_hash"] != request_hash or fingerprint(value["result"]) != value["result_hash"]):
                    raise ValueError("Invalid GLM OCR cache")
                cached.append(value["result"] if value else None)
            if all(value is not None for value in cached):
                values = cached
            else:
                try:
                    values = infer(batch)
                except torch.cuda.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    if len(batch) > 1:
                        cap = max(1, len(batch) // 2)
                        report["events"].append({"event": "oom_reduce_batch", "batch_size": cap, "at": cursor})
                        continue
                    values = [{"status": "error", "glm_text": "", "raw_text": "", "error": "CUDA out of memory at batch size 1"}]
                except Exception as exc:
                    report["events"].append({"event": "batch_error_single_target_recovery", "at": cursor,
                                             "error": type(exc).__name__ + ": " + str(exc)})
                    values = []
                    cap = 1
                    for row in batch:
                        try:
                            values.extend(infer([row]))
                        except Exception as target_error:
                            values.append({"status": "error", "glm_text": "", "raw_text": "",
                                           "error": type(target_error).__name__ + ": " + str(target_error)})
                for row, value in zip(batch, values):
                    path = output / "cache" / (fingerprint([request_hash, row["id"]]) + ".json")
                    save(path, {"request_hash": request_hash, "result": value, "result_hash": fingerprint(value)})
            for row, value in zip(batch, values):
                row.update(value)
            cursor += len(batch)
            report.update(completed=cursor, succeeded=sum(row["status"] == "success" for row in rows),
                          failed=sum(row["status"] == "error" for row in rows),
                          empty=sum(row["status"] == "success" and not row.get("glm_text") for row in rows),
                          truncated=sum(row.get("truncated", False) for row in rows),
                          elapsed_seconds=time.monotonic() - started, last_progress_at=time.time())
            save(output / "result.json", report)
            print(f'GLM-OCR {cursor}/{len(rows)} crops, errors={report["failed"]}', flush=True)
        report["gpu_peak_bytes"] = torch.cuda.max_memory_allocated()
        report["status"] = "completed_with_errors" if report["failed"] else "completed"
    except Exception as exc:
        report.update(status="failed", error=type(exc).__name__ + ": " + str(exc),
                      elapsed_seconds=time.monotonic() - started)
        (output / "failure.log").write_text(traceback.format_exc(), encoding="utf-8")
        print(report["error"], flush=True)
    indexed = {row["id"]: row for row in rows}
    document = json.loads((input_dir / "document.json").read_text(encoding="utf-8"))
    for text in document["texts"]:
        text["text"] = indexed[text["id"]].get("glm_text", "")
    document["ocr"] = config
    document["diagnostic"] = {"scope": "OCR-only GLM GPU test; detection/layout/bank unchanged",
                              "request_hash": request_hash, "status": report["status"]}
    save(output / "ocr-result.json", document)
    report["writer_stopped"] = True
    save(output / "result.json", report)
    review(output, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    run(args.input, args.output, json.loads(args.config.read_text(encoding="utf-8")))
