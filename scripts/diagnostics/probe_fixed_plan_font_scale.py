"""Redraw cached typeset plans at a smaller glyph font, without changing plans."""
import argparse
import shutil
import sys
import zipfile
from pathlib import Path
from time import monotonic

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT / "src"))

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.visual.geometry import write_png
from manga_pipeline.visual.stages import verify_files
from audit_typeset_overlap import local_line, pair_overlap

POLICY = "fixed-plan-glyph-font-scale-v1"


def line_audit(lines,font):
    layers = [local_line(line,font) for line in lines]
    records = []
    for index,(line,(_,origin,box)) in enumerate(zip(lines,layers),1):
        records.append({"line":index,"text":line["text"],"draw_origin":[line["x"],line["y"]],
            "planned_width_px":line["width"],"planned_height_px":line["height"],
            "actual_ink_bbox":[origin[0]+box[0],origin[1]+box[1],origin[0]+box[2],origin[1]+box[3]] if box else None})
    pairs = []
    for a in range(len(layers)):
        for b in range(a+1,len(layers)):
            count = pair_overlap(layers[a],layers[b])
            if count:
                pairs.append({"line_indices":[a+1,b+1],"shared_pixels":count})
    return records,pairs


def comparison(before,after,crop=None,scale=.65):
    before = before.crop(crop) if crop else before
    after = after.crop(crop) if crop else after
    image = Image.new("RGB",(before.width*2,before.height+30),"white")
    image.paste(before,(0,30));image.paste(after,(before.width,30))
    draw = ImageDraw.Draw(image)
    draw.text((8,8),"Original font / saved plan",fill="black")
    draw.text((before.width+8,8),f"Glyph font {scale*100:g}% / identical plan",fill="black")
    return image


def probe(source,output,scale):
    started = monotonic()
    source,output = source.resolve(),output.resolve()
    if not 0 < scale <= 1:
        raise ValueError("Font scale must be in (0,1]")
    if output.exists():
        raise ValueError("Use a new probe output directory")
    result = read_json(source / "result.json")
    if result["status"] != "completed":
        raise ValueError("A completed cached typeset is required")
    source_result_hash = file_hash(source / "result.json")
    clean_root = Path(result["source_probe"])
    inpaint = read_json(clean_root / "result.json")
    clean_pages = {page["page"]:page for page in inpaint["pages"]}
    output.mkdir(parents=True)
    pages,gallery,issues,font_pairs,plan_hashes = [],[],[],[],{}
    original_group_collisions,new_group_collisions = set(),set()
    original_region_collisions,new_region_collisions = 0,0
    old_pairs,new_pairs,total_lines,cross_part_pixels,outside_safe_pixels = 0,0,0,0,0
    for page in result["pages"]:
        page_started = monotonic()
        number = page["page"]
        verify_files(source,page)
        verify_files(clean_root,clean_pages[number])
        files = {}
        # Archive the exact original plans and their referenced masks. Actual
        # scaled glyphs/metrics are separate, so the saved plan stays truthful.
        for relative,expected in page["files"].items():
            if relative.startswith("plans/"):
                original = (source / relative).resolve()
                copied = (output / relative).resolve()
                if not original.is_relative_to(source) or not copied.is_relative_to(output):
                    raise ValueError("Plan path leaves its artifact directory")
                copied.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(original,copied)
                if file_hash(copied) != expected:
                    raise ValueError("Copied original plan/mask changed")
                files[relative] = expected
        relative_plan = f"plans/page-{number:02d}.json"
        original_plan_hash = file_hash(source / relative_plan)
        if file_hash(output / relative_plan) != original_plan_hash:
            raise ValueError("Original plan was modified")
        plan_hashes[relative_plan] = original_plan_hash
        data = read_json(source / relative_plan)
        font_path = Path(data["font_path"])
        if file_hash(font_path) != data["font_sha256"]:
            raise ValueError("Original plan font changed")
        clean_relative = f"pages/page-{number}/clean.png"
        if file_hash(clean_root / clean_relative) != page["clean_sha256"]:
            raise ValueError("Source clean page changed")
        with Image.open(clean_root / clean_relative) as image:
            clean = image.convert("RGB")
        with Image.open(source / "pages" / f"page-{number:02d}.png") as image:
            before = image.convert("RGB")
        rendered = clean.copy()
        occupied = np.zeros((rendered.height,rendered.width),dtype=bool)
        metadata,group_boxes = [],[]
        page_old_pairs,page_new_pairs = 0,0

        def save(relative,image):
            path = output / relative
            write_png(path,image)
            files[relative] = file_hash(path)
            return relative

        for utterance,row in enumerate(data["plans"],1):
            if row["status"] != "fitted":
                issues.append({"page":number,"utterance":utterance,"reason":"source_plan_not_fitted"})
                continue
            nominal_size = row["font_size_px"]
            actual_size = nominal_size*scale
            original_font = ImageFont.truetype(str(font_path),nominal_size)
            actual_font = ImageFont.truetype(str(font_path),actual_size)
            # Pillow accepts fractional font sizes, retaining exactly the ratio.
            if actual_font.size != actual_size:
                raise ValueError("Font engine changed the requested render size")
            font_pairs.append({"planned_px":nominal_size,"render_px":actual_size})
            record = {"utterance_id":row["utterance_id"],"group_id":row["group_id"],
                "source_text_ids":row["source_text_ids"],"planned_font_size_px":nominal_size,
                "render_font_size_px":actual_size,"font_scale":scale,"parts":[]}
            boxes = []
            for part_index,part in enumerate(row["parts"],1):
                line_metrics,new_collisions = line_audit(part["lines"],actual_font)
                _,old_collisions = line_audit(part["lines"],original_font)
                total_lines += len(part["lines"])
                old_pairs += len(old_collisions);new_pairs += len(new_collisions)
                page_old_pairs += len(old_collisions);page_new_pairs += len(new_collisions)
                original_region_collisions += int(bool(old_collisions))
                new_region_collisions += int(bool(new_collisions))
                key = (number,row["utterance_id"])
                if old_collisions:
                    original_group_collisions.add(key)
                if new_collisions:
                    new_group_collisions.add(key)
                glyph = Image.new("L",rendered.size)
                draw = ImageDraw.Draw(glyph)
                for line in part["lines"]:
                    # Exact text, line breaks, x/y and anchor from the old plan.
                    draw.text((line["x"],line["y"]),line["text"],font=actual_font,fill=255,anchor="lt")
                pixels = np.asarray(glyph)>0
                safe = np.asarray(Image.open(source / part["safe_mask"]).convert("L"))>0
                escaped = int(np.count_nonzero(pixels & ~safe))
                collision = int(np.count_nonzero(pixels & occupied))
                outside_safe_pixels += escaped;cross_part_pixels += collision
                if escaped or collision or new_collisions:
                    issues.append({"page":number,"utterance":utterance,"part":part_index,
                        "outside_safe_pixels":escaped,"cross_part_overlap_pixels":collision,
                        "line_overlap_pairs":len(new_collisions)})
                occupied |= pixels
                rendered.paste((0,0,0),mask=glyph)
                path = save(f"render-masks/page-{number:02d}/utterance-{utterance}-part-{part_index}-glyph.png",glyph)
                record["parts"].append({"source_part_id":part["part_id"],"source_glyph_mask":part["glyph_mask"],
                    "render_glyph_mask":path,"source_safe_mask":part["safe_mask"],"lines":line_metrics,
                    "old_line_overlap_pairs":old_collisions,"new_line_overlap_pairs":new_collisions,
                    "outside_safe_pixels":escaped,"cross_part_overlap_pixels":collision})
                boxes.append(part["bbox"])
            metadata.append(record)
            group_boxes.append({"page":number,"utterance":utterance,"group_id":row["group_id"],"boxes":boxes,
                "nominal_size":nominal_size,"actual_size":actual_size})
        changed = np.any(np.asarray(rendered)!=np.asarray(clean),axis=2)
        if np.any(changed & ~occupied):
            raise ValueError("Composite changed pixels outside the scaled glyph masks")
        rendered_path = save(f"pages/page-{number:02d}.png",rendered)
        comparison_path = save(f"comparisons/page-{number:02d}.png",comparison(before,rendered,scale=scale))
        for group in group_boxes:
            boxes = group.pop("boxes")
            crop = (max(0,min(box[0] for box in boxes)-24),max(0,min(box[1] for box in boxes)-24),
                min(rendered.width,max(box[2] for box in boxes)+24),min(rendered.height,max(box[3] for box in boxes)+24))
            group["comparison"] = save(f"comparisons/page-{number:02d}-utterance-{group['utterance']}.png",comparison(before,rendered,crop,scale=scale))
            gallery.append(group)
        path = output / "render-metrics" / f"page-{number:02d}.json"
        atomic_json(path,{"source_plan":relative_plan,"source_plan_sha256":original_plan_hash,
            "font_scale":scale,"plans_changed":False,"draw_origins_changed":False,
            "font_path":str(font_path),"font_sha256":data["font_sha256"],"groups":metadata})
        files[path.relative_to(output).as_posix()] = file_hash(path)
        saved = {"page":number,"page_id":page["page_id"],"rendered":rendered_path,"comparison":comparison_path,
            "rendered_source_texts":page["rendered_source_texts"],"groups":len(metadata),
            "old_line_overlap_pairs":page_old_pairs,"new_line_overlap_pairs":page_new_pairs,
            "files":files,"runtime_seconds":round(monotonic()-page_started,3)}
        verify_files(output,saved)
        pages.append(saved)
        print(f"page {number}: line collision pairs {page_old_pairs} -> {page_new_pairs}; plan unchanged",flush=True)
    if file_hash(source / "result.json") != source_result_hash:
        raise ValueError("Source result changed during this probe")
    for relative,sha in plan_hashes.items():
        if file_hash(source / relative) != sha or file_hash(output / relative) != sha:
            raise ValueError("Plan changed during this probe")
    archive = output / f"After-School-Chapter-001-vi-font{scale*100:g}.cbz"
    with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_STORED) as package:
        for page in pages:
            package.write(output / page["rendered"],arcname=f"{page['page']:03d}.png")
    summary = {"policy":POLICY,"status":"needs_review" if issues else "completed","source":str(source),
        "source_result_sha256":source_result_hash,"font_scale":scale,"font_reduction_percent":(1-scale)*100,
        "plans_changed":False,"draw_origins_changed":False,"source_plan_hashes":plan_hashes,
        "translation_performed":False,"ocr_performed":False,"inpaint_performed":False,"fitting_performed":False,
        "pipeline_default_changed":False,"expected_pages":result["expected_pages"],"completed_pages":len(pages),
        "rendered_source_texts":sum(page["rendered_source_texts"] for page in pages),"lines":total_lines,
        "font_range_planned_px":[min(x["planned_px"] for x in font_pairs),max(x["planned_px"] for x in font_pairs)],
        "font_range_render_px":[min(x["render_px"] for x in font_pairs),max(x["render_px"] for x in font_pairs)],
        "old_groups_with_line_overlap":len(original_group_collisions),"new_groups_with_line_overlap":len(new_group_collisions),
        "old_regions_with_line_overlap":original_region_collisions,"new_regions_with_line_overlap":new_region_collisions,
        "old_line_overlap_pairs":old_pairs,"new_line_overlap_pairs":new_pairs,
        "outside_safe_pixels":outside_safe_pixels,"cross_part_overlap_pixels":cross_part_pixels,
        "pages":pages,"issues":issues,"runtime_seconds":round(monotonic()-started,3),
        "delivery":{"cbz":archive.name,"sha256":file_hash(archive)}}
    atomic_json(output / "result.json",summary)
    write_review(output,summary,gallery)
    print(f"result: {output / 'result.json'}",flush=True)


def write_review(output,result,gallery):
    percent = result["font_scale"]*100
    lines = [f"# Probe: glyph {percent:g}%, plan giữ nguyên", "",
        f"Trạng thái **{result['status']}**; {result['completed_pages']}/{result['expected_pages']} trang, {result['rendered_source_texts']} vùng OCR đã dàn.", "",
        "[Gallery](gallery.html) · [Kết quả JSON](result.json) · [CBZ]("+result["delivery"]["cbz"]+")", "",
        "## Phương pháp", "",
        f"Font vẽ thật = font trong plan × {result['font_scale']}. Pillow nhận font size số thực nên không làm tròn hoặc áp lại min font của bước lập plan. Ví dụ 20px → 13px, 23px → 14.95px.",
        "Bản sao `plans/` gồm JSON và masks nguyên bản, byte-identical với source. Không tính lại ngắt dòng, word allocation, x/y, width/height, anchor, margin hoặc vùng mở rộng. Glyph mới nằm trong `render-masks/`; metadata cỡ chữ vẽ thật nằm ở `render-metrics/`.",
        "Vẽ đúng các tọa độ x/y cũ trên clean cache, không paste lên trang đã có chữ lớn. Giữ một font size thực tế chung cho mỗi group. Khi chiều rộng glyph nhỏ lại nhưng x không đổi, tâm chữ thực tế có thể lệch trái so với tâm hộp dòng cũ; ảnh so sánh thể hiện tác động này.", "",
        "## Kiểm tra chồng dòng", "",
        f"Cặp dòng giao glyph: **{result['old_line_overlap_pairs']} → {result['new_line_overlap_pairs']}**.",
        f"Group có chồng glyph: **{result['old_groups_with_line_overlap']} → {result['new_groups_with_line_overlap']}**; region: **{result['old_regions_with_line_overlap']} → {result['new_regions_with_line_overlap']}**.",
        f"Glyph ngoài safe mask: {result['outside_safe_pixels']}px; giao giữa part khác nhau: {result['cross_part_overlap_pixels']}px. Mọi plan được kiểm tra hash bằng bản gốc. Đây là probe riêng, chưa thay pipeline mặc định.",
        f"Font nominal trong chapter: {result['font_range_planned_px']}px; font thực tế: {result['font_range_render_px']}px. Plan min/max giữ nguyên; tỷ lệ render không có sàn 10px riêng.", "",
        "## Toàn trang", "", "| Trang | Cặp giao trước | Cặp giao sau | So sánh | Ảnh mới | Plan nguyên bản | Metrics thực tế |",
        "| --- | --- | --- | --- | --- | --- | --- |"]
    for page in result["pages"]:
        n = page["page"]
        lines.append(f"| {n} | {page['old_line_overlap_pairs']} | {page['new_line_overlap_pairs']} | [Ảnh]({page['comparison']}) | [Mới]({page['rendered']}) | [Plan](plans/page-{n:02d}.json) | [Metrics](render-metrics/page-{n:02d}.json) |")
    lines.extend(["", "## Từng group", "", "| Trang | Group | Font plan | Font thực tế | So sánh |", "| --- | --- | --- | --- | --- |"])
    cards = []
    for group in gallery:
        lines.append(f"| {group['page']} | {group['utterance']} | {group['nominal_size']} | {group['actual_size']:.2f} | [Ảnh]({group['comparison']}) |")
        cards.append(f'<article><h2>Trang {group["page"]} · group {group["utterance"]} · {group["nominal_size"]} → {group["actual_size"]:.2f}px</h2>'
            f'<a href="{group["comparison"]}"><img loading="lazy" src="{group["comparison"]}"></a></article>')
    if result["issues"]:
        lines.extend(["", "Các lỗi còn lại nằm trong result.json; không sửa plan hoặc cắt glyph để ép probe pass."])
    (output / "README.vi.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    (output / "gallery.html").write_text(f'<!doctype html><meta charset="utf-8"><title>Font {percent:g}%, plan unchanged</title>'
        '<style>body{font-family:Arial;background:#eee;margin:24px}article{background:white;padding:16px;margin:16px 0}img{max-width:100%}</style>'
        f'<h1>Font thực tế {percent:g}%, plan giữ nguyên</h1><p>Trái: font gốc. Phải: glyph giảm {result["font_reduction_percent"]:g}%, x/y và ngắt dòng giữ nguyên.</p>'+"".join(cards),encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--font-scale",type=float,default=.65)
    args = parser.parse_args()
    probe(args.source,args.output,args.font_scale)
