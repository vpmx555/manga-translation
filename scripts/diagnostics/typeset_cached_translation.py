"""Typeset a cached translation onto verified, completed diagnostic clean pages."""

from __future__ import annotations

import argparse
import copy
import html
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.storage.runs import RunStore


def canonical(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))


def build_mapping(inpaint, geometry, translation_run, translation_path):
    """Use image hashes and boxes: OCR IDs and ordering differ between these runs."""
    old_manifest = read_json(translation_run / "manifest.json")
    old_raw = read_json(translation_run / "01_extract" / "result.json")
    old_pages = {page["page"]: page for page in old_raw["pages"]}
    hashes = {str(Path(image["path"]).resolve()): image["sha256"]
              for image in old_manifest["source"]["images"]}
    for page in inpaint["pages"]:
        old_page = old_pages[page["page"]]
        old_hash = hashes.get(str(Path(old_page["image_path"]).resolve()))
        if not old_hash or old_hash != page["source"]["sha256"]:
            raise ValueError(f"Translation source image mismatch on page {page['page']}")

    old_texts = {row["id"]: row for row in old_raw["texts"]}
    candidates = []
    translation = read_json(translation_path)
    for row in translation["translations"]:
        source = row["source"]
        ids = source.get("source_text_ids") or [source.get("source_text_id") or source.get("id")]
        boxes = source.get("source_boxes") or [old_texts[text_id] for text_id in ids
                                                if text_id in old_texts]
        candidates.append((row, boxes))

    selected = {text_id for page in inpaint["pages"] for text_id in page["selected_text_ids"]}
    mapping = {}
    for source in geometry["texts"]:
        if source["id"] not in selected:
            continue
        matches = []
        for row, boxes in candidates:
            if row["source"].get("page") != source["page"]:
                continue
            if any(box.get("bbox") and len(box["bbox"]) == 4 and
                   all(abs(a - b) < 1 for a, b in zip(box["bbox"], source["bbox"]))
                   for box in boxes):
                matches.append(row)
        if len(matches) != 1:
            raise ValueError(f"Expected one translation for {source['id']}, found {len(matches)}")
        match = matches[0]
        if not match.get("translation", "").strip():
            raise ValueError(f"Empty cached translation: {match['id']}")
        mapping[source["id"]] = {
            "source_text_id": source["id"], "page": source["page"], "bbox": source["bbox"],
            "diagnostic_ocr_text": source["text"], "translation_id": match["id"],
            "translated_source_text": match["source"]["text"],
            "translation": unicodedata.normalize("NFC", match["translation"]),
            "source_needs_review": bool(match.get("needs_review")),
            "source_review_reason": match.get("review_reason", ""),
            "match_method": "identical_page_sha256_and_bbox_within_1px",
        }
    if selected != mapping.keys():
        raise ValueError("Not all erased source regions have a cached translation")
    return mapping, translation.get("style")


def render(args):
    import numpy as np
    from PIL import Image, ImageDraw
    from manga_pipeline.v6.config import validate_config
    from manga_pipeline.v6.normalize import normalize
    from manga_pipeline.v6.typeset import fit, resolve_font, safe_parts
    from manga_pipeline.v6.experimental_ocr_typeset import (
        POLICY as OCR_POLICY, fit_with_expansion, ocr_regions, rectangle_gap,
    )
    from manga_pipeline.visual.geometry import write_png
    from manga_pipeline.visual.stages import verify_files
    from typeset_trace import font_coverage

    started = monotonic()
    probe = args.probe_dir.resolve()
    output = args.output.resolve()
    if output.exists():
        raise ValueError(f"Output already exists; choose a new directory: {output}")
    inpaint = read_json(probe / "result.json")
    recorded = read_json(probe / "ocr-typeset-result.json")
    if inpaint.get("status") != "completed" or inpaint.get("plan_only"):
        raise ValueError("Requires completed diagnostic inpaint outputs")
    if len(inpaint["pages"]) != inpaint["expected_pages"] or inpaint.get("failures"):
        raise ValueError("Diagnostic pages are incomplete")
    store = RunStore(inpaint["run_dir"])
    geometry = store.read("bubble_geometry")
    config = copy.deepcopy(recorded["typeset_config"])
    config["line_spacing"] = args.line_spacing
    validate_config(config)
    font_path = resolve_font(config)
    coverage = font_coverage(font_path)
    if coverage["missing_characters"]:
        raise ValueError("Font lacks Vietnamese glyphs: " + coverage["missing_characters"])
    mapping, style = build_mapping(inpaint, geometry, args.translation_run.resolve(),
                                   args.translation_json.resolve())
    use_ocr = args.method == "ocrtypeset"
    method_label = "OCRTypeset" if use_ocr else "bubble body"
    document = normalize(geometry)
    rows = []
    owners = {}
    for source_row in document["utterances"]:
        ids = [text_id for text_id in source_row["source_text_ids"] if text_id in mapping]
        if not ids:
            continue
        row = copy.deepcopy(source_row)
        row["source_text_ids"] = ids
        row["source_boxes"] = [box for box in row["source_boxes"] if box["source_text_id"] in mapping]
        translation_ids = list(dict.fromkeys(mapping[text_id]["translation_id"] for text_id in ids))
        for translation_id in translation_ids:
            if translation_id in owners:
                raise ValueError(f"Translation reused in different groups: {translation_id}")
            owners[translation_id] = row["id"]
        row["translation_ids"] = translation_ids
        row["translation"] = " ".join(next(mapping[text_id]["translation"] for text_id in ids
                                           if mapping[text_id]["translation_id"] == translation_id)
                                      for translation_id in translation_ids)
        rows.append(row)

    output.mkdir(parents=True)
    (output / "pages").mkdir()
    (output / "comparisons").mkdir()
    (output / "plans").mkdir()
    atomic_json(output / "translation-mapping.json", {
        "translation_json": str(args.translation_json.resolve()), "style": style,
        "translation_sha256": file_hash(args.translation_json),
        "selected_source_texts": len(mapping), "matches": list(mapping.values()),
    })
    groups = {group["id"]: group for page in geometry["pages"] for group in page.get("bubble_groups", [])}
    results, issues, thumbnails = [], [], []
    for page in inpaint["pages"]:
        page_started = monotonic()
        print(f"page {page['page']}: typesetting cached Vietnamese", flush=True)
        verify_files(probe, page)
        clean_relative = f"pages/page-{page['page']}/clean.png"
        if clean_relative not in page["files"]:
            raise ValueError("Uncommitted diagnostic clean image")
        if file_hash(page["source"]["path"]) != page["source"]["sha256"]:
            raise ValueError("Original source changed")
        with Image.open(probe / clean_relative) as image:
            clean = image.convert("RGB")
        with Image.open(page["source"]["path"]) as image:
            original = image.convert("RGB")
        if clean.size != original.size:
            raise ValueError("Clean and original dimensions differ")
        rendered = clean.copy()
        allowed = np.zeros((clean.height, clean.width), dtype=bool)
        glyph_union = np.zeros_like(allowed)
        plans = []
        files = {}
        mask_dir = output / "plans" / f"page-{page['page']:02d}"
        mask_dir.mkdir()
        for index, row in enumerate((r for r in rows if r["page_id"] == page["page_id"]), 1):
            print(f"  group {index}: fitting {len(row['source_text_ids'])} region(s)", flush=True)
            parts = (ocr_regions(store, row, groups, clean.size, config) if use_ocr else
                     safe_parts(store, row, groups, clean.size, config))
            text = row["translation"]
            layout_text = text
            trials = []
            wrapped = False
            growth = []
            has_long_token = any(len(word) > 24 for word in text.split())
            if use_ocr and has_long_token:
                # The cached OCR contains long unbroken stammering sounds. Break
                # those tokens for layout only, preserving every source character.
                layout_text = " ".join(" ".join(word[i:i+8] for i in range(0, len(word), 8))
                                       if len(word) > 24 else word for word in text.split())
                if canonical(layout_text) != canonical(text):
                    raise ValueError("Word wrapping changed translation characters")
                wrapped = True
            if use_ocr:
                plan, glyphs, parts, growth = fit_with_expansion(
                    layout_text, parts, font_path, config, trace=trials.append,
                    step_px=args.expansion_step_px, gap_px=args.neighbor_gap_px)
            else:
                plan, glyphs = fit(layout_text, parts, font_path, config, trace=trials.append)
                if plan is None and has_long_token:
                    layout_text = " ".join(" ".join(word[i:i+8] for i in range(0, len(word), 8))
                                           if len(word) > 24 else word for word in text.split())
                    if canonical(layout_text) != canonical(text):
                        raise ValueError("Word wrapping changed translation characters")
                    wrapped = True
                    plan, glyphs = fit(layout_text, parts, font_path, config, trace=trials.append)
            record = {
                "utterance_id": row["id"], "group_id": row.get("group_id"),
                "source_text_ids": row["source_text_ids"], "translation_ids": row["translation_ids"],
                "translation": text, "layout_text": layout_text, "overflow_wrap": wrapped,
                "status": "fitted" if plan else "needs_review", "trials": trials,
                "font_size_px": plan["font_size_px"] if plan else None,
                "render_font_size_px": plan["font_size_px"] if plan else None,
                "render_font_scale": 1.0,
                "parts": plan["parts"] if plan else [],
                "method": args.method,
                "allocation": plan.get("allocation") if plan else None,
                "expansion_steps": growth,
            }
            if plan:
                drawn_text = " ".join(line["text"] for part in plan["parts"] for line in part["lines"])
                if canonical(drawn_text) != canonical(text):
                    raise ValueError("Typesetting changed translation content")
                for part_index, (part, glyph) in enumerate(zip(parts, glyphs), 1):
                    pixels = np.asarray(glyph) > 0
                    if np.any(pixels & ~part["mask"]):
                        raise ValueError("Glyph overflow outside the safe body mask")
                    if use_ocr and np.any(pixels & ~part["allowed_mask_array"]):
                        raise ValueError("Glyph violates the cached body margin/bridge/tail exclusion")
                    if np.any(pixels & glyph_union):
                        raise ValueError("Overlapping glyphs from different bubble parts")
                    allowed |= part["mask"]
                    glyph_union |= pixels
                    rendered.paste((0, 0, 0), mask=glyph)
                    glyph_path = mask_dir / f"utterance-{index}-part-{part_index}-glyph.png"
                    write_png(glyph_path, glyph)
                    relative = glyph_path.relative_to(output).as_posix()
                    files[relative] = file_hash(glyph_path)
                    record["parts"][part_index - 1]["glyph_mask"] = relative
                    if use_ocr:
                        safe_path = mask_dir / f"utterance-{index}-part-{part_index}-safe.png"
                        write_png(safe_path, Image.fromarray(part["mask"].astype(np.uint8) * 255))
                        safe_relative = safe_path.relative_to(output).as_posix()
                        files[safe_relative] = file_hash(safe_path)
                        record["parts"][part_index - 1].update({
                            key: part[key] for key in ("bbox", "original_ocr_bbox", "source_text_id",
                                                      "anchor", "allocation_area_px", "area_share")
                        })
                        record["parts"][part_index - 1].update(
                            safe_mask=safe_relative, expanded=bool(part.get("expanded")),
                            blocked_directions=part.get("blocked_directions", {}))
                if use_ocr:
                    for a in range(len(parts)):
                        for b in range(a + 1, len(parts)):
                            if np.any(parts[a]["mask"] & parts[b]["mask"]):
                                raise ValueError("Usable OCR regions overlap")
                            if ((parts[a].get("expanded") or parts[b].get("expanded")) and
                                    rectangle_gap(parts[a]["bbox"], parts[b]["bbox"]) < args.neighbor_gap_px):
                                raise ValueError("Expanded OCR regions violate the protected neighbor gap")
            else:
                issues.append({"page": page["page"], "utterance_id": row["id"],
                               "reason": "Cached translation does not fit the permitted layout regions"})
            plans.append(record)
        changed = np.any(np.asarray(clean) != np.asarray(rendered), axis=2)
        outside = int(np.count_nonzero(changed & ~allowed))
        if outside:
            raise ValueError("Rendering modified pixels outside safe bubble bodies")
        rendered_path = output / "pages" / f"page-{page['page']:02d}.png"
        comparison_path = output / "comparisons" / f"page-{page['page']:02d}.png"
        comparison = Image.new("RGB", (original.width * 2, original.height), "white")
        comparison.paste(original, (0, 0))
        comparison.paste(rendered, (original.width, 0))
        write_png(rendered_path, rendered)
        write_png(comparison_path, comparison)
        plan_path = output / "plans" / f"page-{page['page']:02d}.json"
        atomic_json(plan_path, {"page": page["page"], "plans": plans, "config": config,
                              "font_path": str(font_path), "font_sha256": file_hash(font_path)})
        for target in (rendered_path, comparison_path, plan_path):
            files[target.relative_to(output).as_posix()] = file_hash(target)
        thumbnail = rendered.copy()
        thumbnail.thumbnail((300, 426))
        thumbnails.append((page["page"], thumbnail))
        record = {"page": page["page"], "page_id": page["page_id"],
                  "source": page["source"], "clean_sha256": page["files"][clean_relative],
                  "utterances": len(plans), "selected_source_texts": len(page["selected_text_ids"]),
                  "rendered_source_texts": sum(len(plan["source_text_ids"]) for plan in plans
                                               if plan["status"] == "fitted"),
                  "fitted_utterances": sum(plan["status"] == "fitted" for plan in plans),
                  "outside_safe_changed_pixels": outside,
                  "overflow_wrapped_utterances": sum(plan["overflow_wrap"] for plan in plans),
                  "expanded_groups": sum(bool(plan["expansion_steps"]) for plan in plans),
                  "rendered_layout_regions": sum(len(plan["parts"]) for plan in plans),
                  "font_sizes": [plan["font_size_px"] for plan in plans], "files": files,
                  "runtime_seconds": round(monotonic() - page_started, 3)}
        results.append(record)
        print(f"page {page['page']}: rendered {record['rendered_source_texts']}/"
              f"{record['selected_source_texts']}, outside-safe pixels={outside}", flush=True)

    preview = Image.new("RGB", (948, 460 * ((len(thumbnails) + 2) // 3)), "#dddddd")
    draw = ImageDraw.Draw(preview)
    for index, (number, image) in enumerate(thumbnails):
        x, y = (index % 3) * 316 + 8, (index // 3) * 460 + 24
        preview.paste(image, (x, y))
        draw.text((x, y - 18), f"Page {number}", fill="black")
    write_png(output / "preview.png", preview)
    archive_path = output / "After-School-Chapter-001-vi.cbz"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for page in results:
            path = output / "pages" / f"page-{page['page']:02d}.png"
            archive.write(path, path.name)
    cards = []
    for page in results:
        n = page["page"]
        cards.append(f'<article><h2>Trang {n}</h2><a href="pages/page-{n:02d}.png">'
                     f'<img loading="lazy" src="pages/page-{n:02d}.png" alt="Trang {n}"></a>'
                     f'<p><a href="comparisons/page-{n:02d}.png">Đối chiếu gốc / tiếng Việt</a> · '
                     f'<a href="plans/page-{n:02d}.json">Dữ liệu dàn chữ</a></p></article>')
    (output / "gallery.html").write_text(
        '<!doctype html><html lang="vi"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>After School — Chương 1 tiếng Việt · {method_label}</title>'
        '<style>body{max-width:1200px;margin:24px auto;padding:0 16px;background:#eee;'
        'font:16px system-ui;color:#222}article{margin:24px 0;padding:16px;background:white}'
        'img{width:100%;max-width:900px;height:auto}a{color:#164f97}</style>'
        f'<h1>After School — Chương 1 tiếng Việt · {method_label}</h1>'
        f'<p>{len(results)} trang · {len(mapping)} vùng chữ đã xóa · Bản dịch {html.escape(style or "natural")}.</p>'
        + ('<p>OCRTypeset: dàn theo từng vùng OCR, chia chữ theo tỷ lệ diện tích vùng gốc; '
         f'mở rộng {args.expansion_step_px} px khi cần, giữ khoảng cách {args.neighbor_gap_px} px.</p>'
         if use_ocr else '')
        + '<p>Dùng bản dịch và ảnh đã làm sạch từ diagnostic. Các chữ ngoài vùng đã xóa giữ theo ảnh nguồn.</p>'
        '<p><a href="After-School-Chapter-001-vi.cbz">Tải CBZ</a> · '
        '<a href="translation-mapping.json">Đối chiếu bản dịch</a></p>'
        + "".join(cards) + '</html>', encoding="utf-8")
    result = {
        "policy": "cached-vietnamese-" + (OCR_POLICY if use_ocr else "diagnostic-typeset-v1"),
        "method": args.method,
        "status": "needs_review" if issues else "completed",
        "source_probe": str(probe), "source_run": str(store.directory),
        "translation_json": str(args.translation_json.resolve()),
        "translation_sha256": file_hash(args.translation_json), "translation_style": style,
        "translation_performed": False, "ocr_performed": False, "inpaint_performed": False,
        "shortening_performed": False, "font_coverage": coverage, "typeset_config": config,
        "render_font_scale": 1.0,
        "expected_pages": inpaint["expected_pages"], "completed_pages": len(results),
        "selected_source_texts": len(mapping),
        "rendered_source_texts": sum(page["rendered_source_texts"] for page in results),
        "utterances": len(rows), "pages": results, "needs_review": issues,
        "expanded_groups": sum(page["expanded_groups"] for page in results),
        "rendered_layout_regions": sum(page["rendered_layout_regions"] for page in results),
        "expansion_step_px": args.expansion_step_px if use_ocr else None,
        "neighbor_gap_px": args.neighbor_gap_px if use_ocr else None,
        "source_translation_needs_review": [m for m in mapping.values() if m["source_needs_review"]],
        "unselected_geometry_texts": [row["id"] for row in geometry["texts"] if row["id"] not in mapping],
        "outside_safe_changed_pixels": sum(page["outside_safe_changed_pixels"] for page in results),
        "runtime_seconds": round(monotonic() - started, 3),
        "delivery": {name: file_hash(output / name) for name in
                     ("preview.png", "gallery.html", archive_path.name, "translation-mapping.json")},
    }
    atomic_json(output / "result.json", result)
    summary = [f"# After School — dàn trang tiếng Việt bằng {method_label}", "",
               f"- Trạng thái: `{result['status']}`.",
               f"- {len(results)}/{inpaint['expected_pages']} trang; "
               f"{result['rendered_source_texts']}/{len(mapping)} vùng chữ được dàn trang.",
               f"- Bản dịch có sẵn: `{args.translation_json.resolve()}`; style `{style}`.",
               f"- Nguồn ảnh đã làm sạch: `{probe}`.",
               f"- Plan dùng line_spacing={config['line_spacing']}; font vẽ đúng cỡ font trong plan (scale=1.0).",
               "- Ghép theo SHA-256 ảnh nguồn và tọa độ vùng chữ; không dựa vào ID/thứ tự OCR.",
               "- Giữ nguyên nội dung bản dịch. Chỉ ngắt từ quá dài khi cần xuống dòng.",
               ("- OCRTypeset: mỗi OCR box là một vùng dàn; chia chữ theo tỷ lệ diện tích usable ban đầu, "
                "chọn font chung cho group, mở rộng có giới hạn khi không fit." if use_ocr else
                "- Dàn chữ theo safe body mask của group."),
               "- Font, margin và safe masks kế thừa diagnostic; không vẽ lên cầu nối/đuôi bubble.",
               "- Không chạy lại OCR, inpaint, phân tích ngữ nghĩa, dịch hay rút gọn.",
               f"- Số pixel thay đổi ngoài safe masks: {result['outside_safe_changed_pixels']}.",
               "- Chữ ngoài những vùng diagnostic đã xóa vẫn nằm trong ảnh gốc.", "",
               "[Gallery](gallery.html) · [CBZ](After-School-Chapter-001-vi.cbz) · "
               "[Preview](preview.png) · [Mapping](translation-mapping.json) · [Kết quả](result.json)", "",
               "| Trang | Vùng đã dàn | Ảnh | Đối chiếu |", "|---|---:|---|---|"]
    summary.extend(f"| {page['page']} | {page['rendered_source_texts']}/{page['selected_source_texts']} | "
                   f"[Tiếng Việt](pages/page-{page['page']:02d}.png) | "
                   f"[Gốc / Việt](comparisons/page-{page['page']:02d}.png) |" for page in results)
    if issues:
        summary.extend(["", "Các vùng không fit được ghi trong `result.json`."])
    (output / "review.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(f"result: {output / 'result.json'}", flush=True)
    print(f"status: {result['status']}; elapsed: {result['runtime_seconds']}s", flush=True)
    return 0 if not issues else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-dir", required=True, type=Path)
    parser.add_argument("--translation-run", required=True, type=Path)
    parser.add_argument("--translation-json", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--method", choices=("body", "ocrtypeset"), default="body")
    parser.add_argument("--line-spacing", type=float, default=1.6,
                        help="Line spacing multiplier used when recomputing the plan (default: 1.6)")
    parser.add_argument("--expansion-step-px", type=int, default=2)
    parser.add_argument("--neighbor-gap-px", type=int, default=10)
    return render(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
