"""Opt-in OCR-area typesetting probe from verified inpaint artifacts; local only."""
import argparse
import copy
import html
import re
import sys
import unicodedata
from pathlib import Path
from time import monotonic

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT / "src"))

import numpy as np
from PIL import Image, ImageDraw
from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.v6.config import DEFAULT_CONFIG, validate_config
from manga_pipeline.v6.experimental_ocr_typeset import POLICY, fit_with_expansion, ocr_regions, rectangle_gap
from manga_pipeline.v6.normalize import normalize
from manga_pipeline.v6.typeset import resolve_font
from manga_pipeline.visual.geometry import write_png
from manga_pipeline.visual.stages import verify_files
from typeset_trace import COLORS, finalize_page_trace, font_coverage, render_trace


def probe(source_directory, output, step_px=2, gap_px=10):
    if step_px < 1 or gap_px < 1:
        raise ValueError("Expansion step and protected gap must be positive")
    started = monotonic()
    source_directory,output = source_directory.resolve(),output.resolve()
    if output.exists():
        raise ValueError("Use a new output directory; baseline/probe outputs are immutable")
    inpaint = read_json(source_directory / "result.json")
    baseline = read_json(source_directory / "ocr-typeset-result.json")
    if inpaint["status"] != "completed" or inpaint.get("plan_only") or baseline["failures"]:
        raise ValueError("Verified completed inpaint and baseline typeset are required")
    store = RunStore(inpaint["run_dir"])
    raw = store.read("bubble_geometry")
    config = copy.deepcopy(baseline["typeset_config"])
    config.update(body_margin_px=10,body_margin_ratio=0.0,font_path=DEFAULT_CONFIG["font_path"])
    validate_config(config)
    font_path = resolve_font(config)
    if file_hash(font_path) != read_json(source_directory / "pages/page-1/ocr-restored/typeset-plan.json")["font_sha256"]:
        raise ValueError("Baseline font changed")
    coverage = font_coverage(font_path)
    if coverage["missing_characters"]:
        raise ValueError("Font lacks Vietnamese glyphs")
    selected = {text_id for page in inpaint["pages"] for text_id in page["selected_text_ids"]}
    sources = {row["id"]:row for row in raw["texts"]}
    document = normalize(raw)
    retained = []
    for original_row in document["utterances"]:
        ids = [text_id for text_id in original_row["source_text_ids"] if text_id in selected]
        if not ids:
            continue
        row = copy.deepcopy(original_row)
        row["source_text_ids"] = ids
        row["source_boxes"] = [s for s in row["source_boxes"] if s["source_text_id"] in selected]
        row["text_original"] = [sources[text_id]["text"] for text_id in ids]
        row["text"] = " ".join(re.sub(r"\s+"," ",unicodedata.normalize("NFC",text)).strip() for text in row["text_original"])
        retained.append(row)
    groups = {g["id"]:g for page in raw["pages"] for g in page.get("bubble_groups",[])}
    baseline_pages = {p["page"]:p for p in baseline["pages"]}
    output.mkdir(parents=True)
    pages,failures,issues,gallery = [],[],[],[]
    for page in inpaint["pages"]:
        page_started = monotonic()
        try:
            verify_files(source_directory,page)
            verify_files(source_directory,baseline_pages[page["page"]])
            directory = output / "pages" / f"page-{page['page']}"
            original = Image.open(source_directory / "pages" / f"page-{page['page']}" / "original.png").convert("RGB")
            clean = Image.open(source_directory / "pages" / f"page-{page['page']}" / "clean.png").convert("RGB")
            old = Image.open(source_directory / "pages" / f"page-{page['page']}" / "ocr-restored/rendered.png").convert("RGB")
            rendered = clean.copy()
            files,plans,traces = {},[],[]
            glyph_union = np.zeros((original.height,original.width),dtype=bool)

            def save(name,image):
                path = directory / name
                write_png(path,image)
                relative = path.relative_to(output).as_posix()
                files[relative] = file_hash(path)
                return {"mask_path":relative,"mask_sha256":files[relative]}

            rows = [row for row in retained if row["page_id"] == page["page_id"]]
            for index,row in enumerate(rows,1):
                trials,growth = [],[]
                regions,plan,glyphs = [],None,[]
                error = None
                try:
                    regions = ocr_regions(store,row,groups,original.size,config)
                    plan,glyphs,regions,growth = fit_with_expansion(row["text"],regions,font_path,config,
                        trace=trials.append,step_px=step_px,gap_px=gap_px)
                except ValueError as exc:
                    error = str(exc)
                status = "fitted" if plan else "needs_review"
                record = {"utterance_id":row["id"],"group_id":row["group_id"],"source_text_ids":row["source_text_ids"],
                    "ocr_text":row["text"],"text_original":row["text_original"],"status":status,
                    "font_size_px":plan["font_size_px"] if plan else None,"allocation":plan["allocation"] if plan else None,
                    "expansion_rounds":len(growth),"regions":[]}
                if not plan:
                    issue = {"page":page["page"],"utterance":index,"utterance_id":row["id"],
                        "reason":error or "overflow_after_permitted_expansion"}
                    issues.append(issue)
                    record["issue"] = issue
                for region_index,region in enumerate(regions,1):
                    stem = f"utterance-{index}-region-{region_index}"
                    safe_record = save(stem+"-safe.png",Image.fromarray(region["mask"].astype(np.uint8)*255))
                    saved = {key:region[key] for key in ("id","bbox","original_ocr_bbox","source_text_id",
                        "geometry_part_ids","anchor","margin_px","ocr_rectangle_area_px","allocation_area_px","usable_area_px","area_share")}
                    saved["blocked_directions"] = region.get("blocked_directions",{})
                    saved["expanded"] = region.get("expanded",False)
                    saved["safe_mask"] = safe_record
                    if plan:
                        item,glyph = plan["parts"][region_index-1],glyphs[region_index-1]
                        saved.update(item)
                        saved["glyph_mask"] = save(stem+"-glyph.png",glyph)
                        pixels = np.asarray(glyph)>0
                        if np.any(pixels & ~region["mask"]):
                            raise ValueError("Glyph escapes permitted OCR region")
                        if np.any(pixels & ~region["allowed_mask_array"]):
                            raise ValueError("Glyph violates body margin/bridge/tail exclusion")
                        if np.any(pixels & glyph_union):
                            raise ValueError("Glyphs overlap another OCR region/group")
                        glyph_union |= pixels
                        rendered.paste((0,0,0),mask=glyph)
                    record["regions"].append(saved)
                for a in range(len(regions)):
                    for b in range(a+1,len(regions)):
                        if np.any(regions[a]["mask"] & regions[b]["mask"]):
                            raise ValueError("OCR layout regions overlap")
                        if (regions[a].get("expanded") or regions[b].get("expanded")) and rectangle_gap(regions[a]["bbox"],regions[b]["bbox"]) < gap_px:
                            raise ValueError("Expanded regions violate the protected gap")
                if regions:
                    trace_files,trace_record = render_trace(store,row,regions,config,font_path,trials,plan,glyphs,
                        original,clean,rendered,directory / f"utterance-{index}-trace",mode=POLICY,growth_steps=growth)
                    prefix = directory.relative_to(output).as_posix()
                    files.update({prefix+"/"+relative:sha for relative,sha in trace_files.items()})
                    traces.append(trace_record)
                plans.append(record)
                gallery.append({"page":page["page"],"utterance":index,"status":status,"font_size_px":record["font_size_px"],
                    "regions":len(regions),"growth_rounds":len(growth),"directory":directory.relative_to(output).as_posix(),
                    "trace":bool(regions),"crop_boxes":[part["bbox"] for part in groups[row["group_id"]]["parts"]]})
            changed = np.any(np.asarray(rendered)!=np.asarray(clean),axis=2)
            if np.any(changed & ~glyph_union):
                raise ValueError("Composite modified pixels outside glyphs")
            for trace in traces:
                updated = finalize_page_trace(directory / trace["directory"],original,rendered)
                prefix = directory.relative_to(output).as_posix()
                files.update({prefix+"/"+relative:sha for relative,sha in updated.items()})
            for entry in (e for e in gallery if e["page"] == page["page"]):
                boxes = entry.pop("crop_boxes")
                crop = (max(0,min(b[0] for b in boxes)-24),max(0,min(b[1] for b in boxes)-24),
                    min(original.width,max(b[2] for b in boxes)+24),min(original.height,max(b[3] for b in boxes)+24))
                triplet = comparison(("Original",original),("Body layout",old),("OCR area layout",rendered),crop=crop)
                entry["comparison"] = save(f"utterance-{entry['utterance']}-comparison.png",triplet)["mask_path"]
            save("original.png",original);save("clean.png",clean);save("rendered.png",rendered)
            save("comparison.png",comparison(("Original",original),("Body layout",old),("OCR area layout",rendered)))
            path = directory / "typeset-plan.json"
            atomic_json(path,{"policy":POLICY,"config":config,"font_path":str(font_path),"font_sha256":file_hash(font_path),
                "neighbor_gap_px":gap_px,"expansion_step_px":step_px,"plans":plans})
            files[path.relative_to(output).as_posix()] = file_hash(path)
            record = {"page":page["page"],"page_id":page["page_id"],"files":files,"traces":traces,
                "selected_source_texts":len(page["selected_text_ids"]),
                "restored_source_texts":sum(len(plan["source_text_ids"]) for plan in plans if plan["status"] == "fitted"),
                "expanded_groups":sum(plan["expansion_rounds"]>0 for plan in plans),
                "runtime_seconds":round(monotonic()-page_started,3)}
            verify_files(output,record)
            pages.append(record)
            print(f"page {page['page']}: {record['restored_source_texts']}/{record['selected_source_texts']} OCR regions; expanded_groups={record['expanded_groups']}",flush=True)
        except Exception as exc:
            failures.append({"page":page["page"],"error":str(exc)})
            print(f"page {page['page']}: failed: {exc}",flush=True)
    result = {"policy":POLICY,"status":"failed" if failures else ("needs_review" if issues else "completed"),
        "source_probe":str(source_directory),"source_inpaint_result_sha256":file_hash(source_directory / "result.json"),
        "source_typeset_result_sha256":file_hash(source_directory / "ocr-typeset-result.json"),
        "geometry_run":str(store.directory),"config":config,"font_coverage":coverage,"pipeline_default_changed":False,
        "translation_performed":False,"detection_performed":False,"ocr_performed":False,"inpaint_performed":False,
        "neighbor_gap_px":gap_px,"expansion_step_px":step_px,"expected_pages":len(inpaint["pages"]),
        "completed_pages":len(pages),"selected_source_texts":len(selected),
        "restored_source_texts":sum(p["restored_source_texts"] for p in pages),
        "expanded_groups":sum(p["expanded_groups"] for p in pages),"pages":pages,"issues":issues,"failures":failures,
        "runtime_seconds":round(monotonic()-started,3)}
    atomic_json(output / "result.json",result)
    write_gallery(output,result,gallery)
    print(f"result: {output / 'result.json'}",flush=True)
    return 1 if failures else 0


def comparison(*images,crop=None):
    values = [(name,image.crop(crop) if crop else image) for name,image in images]
    w,h = values[0][1].size
    output = Image.new("RGB",(w*len(values),h+32),"white")
    draw = ImageDraw.Draw(output)
    for index,(name,image) in enumerate(values):
        output.paste(image,(w*index,32));draw.text((w*index+8,8),name,fill="black")
    return output


def write_gallery(output,result,gallery):
    lines = ["# Probe dàn chữ theo vùng OCR và tỷ lệ diện tích", "",
        f"Local: **{result['restored_source_texts']}/{result['selected_source_texts']}** OCR trên {result['completed_pages']}/{result['expected_pages']} trang. Trạng thái: **{result['status']}**. Chưa đưa vào pipeline mặc định.", "",
        "[Gallery HTML](gallery.html) · [Kết quả JSON](result.json)", "",
        "Mỗi vùng = bbox OCR gốc giao safe body mask có margin 10px. Phân bổ chuỗi chung theo tỷ lệ diện tích OCR khả dụng, đếm ký tự không gồm khoảng trắng, chia tại ranh giới từ và giữ thứ tự. Quota giữ nguyên khi mở rộng.", "",
        f"Thử mọi cỡ font từ {result['config']['font_max_px']} xuống {result['config']['font_min_px']} trên vùng gốc, dùng một font size cho cả group. Chỉ khi không fit ở min font mới mở rộng: mỗi vòng tăng {result['expansion_step_px']}px các hướng còn khả dụng; hướng không thêm được pixel an toàn hoặc tiến quá gần vùng khác sẽ bị khóa. Khoảng cách bảo vệ giữa các bbox vùng dàn là {result['neighbor_gap_px']}px. Vùng gốc đã gần nhau hơn mức này không mở rộng. Không xóa/dịch/sửa OCR hoặc gọi model trong probe.", "",
        f"Group có mở rộng trong lượt dữ liệu này: **{result['expanded_groups']}**. Ảnh so sánh: gốc bên trái, dàn theo body ở giữa, phương pháp OCR-area bên phải.", "",
        "| Trang | Group | Số OCR region | Font chung px | Vòng mở rộng | So sánh | Từng bước |", "| --- | --- | --- | --- | --- | --- | --- |"]
    cards = []
    for entry in gallery:
        trace = f"{entry['directory']}/utterance-{entry['utterance']}-trace"
        visual = f"[Visual]({trace}/README.vi.md)" if entry["trace"] else entry["status"]
        lines.append(f"| {entry['page']} | {entry['utterance']} | {entry['regions']} | {entry['font_size_px']} | {entry['growth_rounds']} | [So sánh]({entry['comparison']}) | {visual} |")
        target = trace+"/index.html" if entry["trace"] else entry["comparison"]
        cards.append(f'<article><h2>Trang {entry["page"]} · group {entry["utterance"]} · {entry["regions"]} OCR · font {entry["font_size_px"]}px</h2>'
            f'<a href="{target}"><img loading="lazy" src="{entry["comparison"]}"></a></article>')
    if result["issues"] or result["failures"]:
        lines.extend(["", "Các vùng không fit/lỗi nằm trong result.json; không ép chữ vượt mask hoặc khoảng cách bảo vệ."])
    (output / "README.vi.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    (output / "gallery.html").write_text('<!doctype html><meta charset="utf-8"><title>OCR area typeset probe</title>'
        '<style>body{font-family:Arial;background:#eee;margin:24px}article{background:white;padding:16px;margin:16px 0}img{max-width:100%}</style>'
        '<h1>Gốc / dàn body / dàn OCR-area</h1><p>Bấm ảnh để xem từng bước. Phương pháp đang thử nghiệm.</p>'+"".join(cards),encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-probe",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--expansion-step-px",type=int,default=2)
    parser.add_argument("--neighbor-gap-px",type=int,default=10)
    args = parser.parse_args()
    raise SystemExit(probe(args.source_probe,args.output,args.expansion_step_px,args.neighbor_gap_px))
