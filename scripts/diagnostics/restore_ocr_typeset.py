"""Place original OCR text onto completed inpaint images using v6 typesetting.

This diagnostic has no translation, semantic analysis, OCR repair or shortening.
It reuses safe_parts/fit from the production v6 renderer.
"""
from __future__ import annotations

import argparse
import copy
import re
import sys
import unicodedata
from pathlib import Path
from time import monotonic

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.storage.runs import RunStore


def restore(probe_directory, *, detailed=True, recorded_config=False):
    import numpy as np
    from PIL import Image
    from manga_pipeline.v6.normalize import normalize
    from manga_pipeline.v6.typeset import fit, resolve_font, safe_parts
    from manga_pipeline.visual.geometry import write_png
    from manga_pipeline.visual.stages import verify_files
    from manga_pipeline.v6.config import DEFAULT_CONFIG, validate_config
    from typeset_trace import finalize_page_trace, font_coverage, render_trace

    probe_directory = probe_directory.resolve()
    inpaint = read_json(probe_directory / "result.json")
    if inpaint.get("status") != "completed" or inpaint.get("plan_only"):
        raise ValueError("The inpaint probe must be completed before restoring OCR text")
    store = RunStore(inpaint["run_dir"])
    raw = store.read("bubble_geometry")
    config = {**DEFAULT_CONFIG, **copy.deepcopy(store.manifest["config"]["v6"])}
    if not recorded_config:
        for key in ("body_margin_px", "body_margin_ratio", "font_path"):
            config[key] = DEFAULT_CONFIG[key]
    validate_config(config)
    font_path = resolve_font(config)
    coverage = font_coverage(font_path)
    if coverage["missing_characters"]:
        raise ValueError("Configured font lacks Vietnamese characters: " + coverage["missing_characters"])
    selected = {text_id for page in inpaint["pages"] for text_id in page["selected_text_ids"]}
    source_texts = {row["id"]: row for row in raw["texts"]}
    if selected - source_texts.keys():
        raise ValueError("Selected OCR IDs are missing from the geometry artifact")
    document = normalize(raw)
    retained = []
    for row in document["utterances"]:
        ids = [source_id for source_id in row["source_text_ids"] if source_id in selected]
        if not ids:
            continue
        row = copy.deepcopy(row)
        row["source_text_ids"] = ids
        row["source_boxes"] = [source for source in row["source_boxes"] if source["source_text_id"] in selected]
        row["text_original"] = [source_texts[source_id]["text"] for source_id in ids]
        row["text"] = " ".join(re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()
            for text in row["text_original"])
        retained.append(row)
    document["utterances"] = retained
    groups = {group["id"]: group for page in raw["pages"] for group in page.get("bubble_groups", [])}
    pages, failures, review, gallery = [], [], [], []
    for page in inpaint["pages"]:
        started = monotonic()
        try:
            verify_files(probe_directory, page)
            clean_relative = f"pages/page-{page['page']}/clean.png"
            if clean_relative not in page["files"]:
                raise ValueError("Clean image is missing from the committed inpaint outputs")
            source = page["source"]
            if file_hash(source["path"]) != source["sha256"]:
                raise ValueError("Original page changed")
            with Image.open(probe_directory / clean_relative) as image:
                rendered = image.convert("RGB")
            clean = rendered.copy()
            with Image.open(source["path"]) as image:
                original = image.convert("RGB")
            if original.size != rendered.size:
                raise ValueError("Inpaint and original page dimensions differ")
            directory = probe_directory / "pages" / f"page-{page['page']}" / "ocr-restored"
            files, plans, issues, traces = {}, [], [], []
            rows = [row for row in document["utterances"] if row["page_id"] == page["page_id"]]
            for index, row in enumerate(rows, 1):
                text = row["text"]
                parts = safe_parts(store, row, groups, rendered.size, config)
                trials = []
                plan, glyphs = fit(text, parts, font_path, config, trace=trials.append if detailed else None)
                status = "fitted" if plan else "needs_review"
                saved = {"utterance_id": row["id"], "group_id": row.get("group_id"),
                    "source_text_ids": row["source_text_ids"], "ocr_text": text,
                    "text_original": row["text_original"], "status": status,
                    "text_source": "original_ocr", "translation_performed": False,
                    "font_size_px": plan["font_size_px"] if plan else None, "parts": []}
                if not plan:
                    issue = {"utterance_id": row["id"], "source_text_ids": row["source_text_ids"],
                        "reason": "overflow_at_min_font" if any(part["mask"].any() for part in parts) else "empty_safe_region"}
                    issues.append(issue)
                for part_index, part in enumerate(parts, 1):
                    stem = f"utterance-{index}-part-{part_index}"
                    safe_image = Image.fromarray(part["mask"].astype(np.uint8) * 255)
                    safe_path = directory / f"{stem}-safe.png"
                    write_png(safe_path, safe_image)
                    safe_relative = safe_path.relative_to(probe_directory).as_posix()
                    files[safe_relative] = file_hash(safe_path)
                    record = {"part_id": part["id"], "bbox": part["bbox"],
                        "margin_px": part["margin_px"], "anchor": part["anchor"],
                        "safe_mask": {"mask_path": safe_relative, "mask_sha256": files[safe_relative]}}
                    if plan:
                        record.update(plan["parts"][part_index - 1])
                        glyph_path = directory / f"{stem}-glyph.png"
                        write_png(glyph_path, glyphs[part_index - 1])
                        glyph_relative = glyph_path.relative_to(probe_directory).as_posix()
                        files[glyph_relative] = file_hash(glyph_path)
                        record["glyph_mask"] = {"mask_path": glyph_relative, "mask_sha256": files[glyph_relative]}
                        rendered.paste((0, 0, 0), mask=glyphs[part_index - 1])
                    saved["parts"].append(record)
                if detailed:
                    trace_files, trace_record = render_trace(store, row, parts, config, font_path, trials,
                        plan, glyphs, original, clean, rendered, directory / f"utterance-{index}-trace")
                    prefix = directory.relative_to(probe_directory).as_posix()
                    files.update({prefix+"/"+relative: sha for relative,sha in trace_files.items()})
                    traces.append(trace_record)
                    gallery.append({"page": page["page"], "utterance": index, "status": status,
                        "path": prefix+"/"+trace_record["directory"]})
                plans.append(saved)
            for trace_record in traces:
                refreshed = finalize_page_trace(directory / trace_record["directory"], original, rendered)
                prefix = directory.relative_to(probe_directory).as_posix()
                files.update({prefix+"/"+relative:sha for relative,sha in refreshed.items()})
            comparison = Image.new("RGB", (original.width * 2, original.height), "white")
            comparison.paste(original, (0, 0))
            comparison.paste(rendered, (original.width, 0))
            for name, image in (("rendered.png", rendered), ("before-after.png", comparison)):
                target = directory / name
                write_png(target, image)
                relative = target.relative_to(probe_directory).as_posix()
                files[relative] = file_hash(target)
            plan_path = directory / "typeset-plan.json"
            atomic_json(plan_path, {"text_source": "original_ocr", "font_path": str(font_path),
                "font_sha256": file_hash(font_path), "config": config, "plans": plans, "needs_review": issues})
            files[plan_path.relative_to(probe_directory).as_posix()] = file_hash(plan_path)
            record = {"page": page["page"], "page_id": page["page_id"], "files": files,
                "utterances": len(plans), "fitted_utterances": sum(plan["status"] == "fitted" for plan in plans),
                "restored_source_texts": sum(len(plan["source_text_ids"]) for plan in plans if plan["status"] == "fitted"),
                "selected_source_texts": len(page["selected_text_ids"]), "needs_review": issues,
                "traces": traces,
                "runtime_seconds": round(monotonic() - started, 6)}
            pages.append(record)
            review.extend(issues)
            print(f"page {page['page']}: restored {record['restored_source_texts']}/"
                f"{record['selected_source_texts']} OCR regions; needs_review={len(issues)}", flush=True)
        except Exception as exc:
            failures.append({"page": page["page"], "error": str(exc)})
            print(f"page {page['page']}: failed: {exc}", flush=True)
    result = {"policy": "original-ocr-v6-typeset-probe-v1",
        "status": "failed" if failures else ("needs_review" if review else "completed"),
        "text_source": "original_ocr", "translation_performed": False,
        "analyze_performed": False, "shortening_performed": False,
        "detailed_visuals": detailed, "font_coverage": coverage, "typeset_config": config,
        "source_run": str(store.directory), "inpaint_result": str(probe_directory / "result.json"),
        "expected_pages": inpaint["expected_pages"], "completed_pages": len(pages),
        "selected_source_texts": len(selected),
        "restored_source_texts": sum(page["restored_source_texts"] for page in pages),
        "pages": pages, "needs_review": review, "failures": failures}
    atomic_json(probe_directory / "ocr-typeset-result.json", result)
    markdown = ["# Vẽ lại chữ OCR gốc bằng typeset v6", "",
        f"Trạng thái: **{result['status']}**; hoàn thành {len(pages)}/{result['expected_pages']} trang.",
        f"Đã đặt lại {result['restored_source_texts']}/{len(selected)} vùng chữ OCR gốc.", "",
        "Dùng safe_parts() và fit() của v6/typeset.py trên ảnh clean sau inpaint.",
        "Giữ chữ OCR gốc; không dịch, không analyze, không sửa OCR và không rút gọn bằng LLM.",
        "Phân bổ từ theo body, có margin và kiểm tra glyph nằm trong safe mask; không đặt chữ lên cầu nối/đuôi.",
        "Ảnh so sánh: gốc bên trái, ảnh sau inpaint và vẽ lại chữ gốc bên phải.", "",
        "| Trang | Vùng OCR đã vẽ lại | So sánh | Ảnh cuối | Kế hoạch |",
        "| --- | --- | --- | --- | --- |"]
    for page in pages:
        base = f"pages/page-{page['page']}/ocr-restored"
        markdown.append(f"| {page['page']} | {page['restored_source_texts']}/{page['selected_source_texts']} | "
            f"[Trước/sau]({base}/before-after.png) | [Ảnh cuối]({base}/rendered.png) | "
            f"[Plan]({base}/typeset-plan.json) |")
    if review or failures:
        markdown.extend(["", "Các mục không fit hoặc lỗi được lưu trong [JSON kết quả](ocr-typeset-result.json).", "",
            "Không sửa nội dung OCR hoặc vẽ tràn contour để ép vừa."])
    if detailed:
        markdown.extend(["", "## Visual chi tiết từng group", "",
            "[Gallery HTML](typeset-gallery.html). Mỗi group có candidate, OR masks, repair, opening, body/bridge/tail, OCR mapping, margin, anchor, mọi font trial, slots, phân bổ từ, glyph và composite.", "",
            "| Trang | Group trong trang | Trạng thái | Visual | Dữ liệu |", "| --- | --- | --- | --- | --- |"])
        for entry in gallery:
            markdown.append(f"| {entry['page']} | {entry['utterance']} | {entry['status']} | "
                f"[Visual]({entry['path']}/README.vi.md) | [JSON]({entry['path']}/trace.json) |")
        import html
        cards = "".join(f'<article><h2>Trang {e["page"]} · group {e["utterance"]} · {e["status"]}</h2>'
            f'<a href="{e["path"]}/index.html"><img loading="lazy" src="{e["path"]}/contact-sheet.png"></a></article>' for e in gallery)
        (probe_directory / "typeset-gallery.html").write_text('<!doctype html><meta charset="utf-8"><title>Chẩn đoán tái tạo OCR</title>'
            '<style>body{font-family:Arial;background:#eee;margin:24px}article{background:white;padding:16px;margin-bottom:20px}img{max-width:100%}</style>'
            '<h1>Chẩn đoán tái tạo OCR sau group</h1><p>Gốc và tái tạo; bấm vào ảnh để mở từng bước. Không dịch.</p>'+cards, encoding="utf-8")
    (probe_directory / "ocr-typeset-review.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    print(f"result: {probe_directory / 'ocr-typeset-result.json'}", flush=True)
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-dir", required=True, type=Path)
    parser.add_argument("--no-detail", action="store_true", help="Skip microstep visual artifacts")
    parser.add_argument("--recorded-config", action="store_true", help="Reproduce the recorded typesetting settings")
    args = parser.parse_args()
    return restore(args.probe_dir, detailed=not args.no_detail, recorded_config=args.recorded_config)


if __name__ == "__main__":
    raise SystemExit(main())
