"""Change only the text anchor on one cached page; no fitting or model calls."""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT / "src"))

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.v6.typeset import safe_parts
from manga_pipeline.visual.geometry import write_png
from manga_pipeline.visual.stages import verify_files
from audit_typeset_overlap import local_line, pair_overlap


def overlaps(lines,font,anchor):
    layers = [local_line(line,font,anchor=anchor) for line in lines]
    return [{"line_indices":[a+1,b+1],"shared_pixels":count}
        for a in range(len(layers)) for b in range(a+1,len(layers))
        if (count := pair_overlap(layers[a],layers[b]))]


def compare(left,right,anchor,crop=None):
    left = left.crop(crop) if crop else left
    right = right.crop(crop) if crop else right
    image = Image.new("RGB",(left.width*2,left.height+30),"white")
    image.paste(left,(0,30));image.paste(right,(left.width,30))
    draw = ImageDraw.Draw(image)
    draw.text((8,8),"Saved lt / exact plan",fill="black")
    draw.text((left.width+8,8),f"{anchor} / same font and wrapping",fill="black")
    return image


def probe(source,page_number,output,anchor):
    source,output = source.resolve(),output.resolve()
    if output.exists():
        raise ValueError("Use a new probe output directory")
    result = read_json(source / "result.json")
    source_result_hash = file_hash(source / "result.json")
    page = next(page for page in result["pages"] if page["page"] == page_number)
    verify_files(source,page)
    plan_relative = f"plans/page-{page_number:02d}.json"
    plan_hash = file_hash(source / plan_relative)
    data = read_json(source / plan_relative)
    font_path = Path(data["font_path"])
    if file_hash(font_path) != data["font_sha256"]:
        raise ValueError("Saved plan font changed")
    clean_root = Path(result["source_probe"])
    clean_path = clean_root / "pages" / f"page-{page_number}" / "clean.png"
    if file_hash(clean_path) != page["clean_sha256"]:
        raise ValueError("Cached clean page changed")
    clean = Image.open(clean_path).convert("RGB")
    before = Image.open(source / "pages" / f"page-{page_number:02d}.png").convert("RGB")
    rendered = clean.copy()
    store = RunStore(result["source_run"])
    raw = store.read("bubble_geometry")
    groups = {group["id"]:group for page in raw["pages"] for group in page.get("bubble_groups",[])}
    source_texts = {row["id"]:row for row in raw["texts"]}
    output.mkdir(parents=True)
    files,records,gallery = {},[],[]
    copied = output / plan_relative
    copied.parent.mkdir(parents=True)
    shutil.copy2(source / plan_relative,copied)
    files[plan_relative] = file_hash(copied)
    occupied = np.zeros((rendered.height,rendered.width),dtype=bool)
    total_old_pairs,total_new_pairs,total_outside_ocr,total_outside_body,total_cross = 0,0,0,0,0

    def save(relative,image):
        path = output / relative
        write_png(path,image)
        files[relative] = file_hash(path)
        return relative

    for index,row in enumerate(data["plans"],1):
        if row["status"] != "fitted":
            raise ValueError("Source page has a non-fitted group")
        font = ImageFont.truetype(str(font_path),row["font_size_px"])
        reference_bbox = font.getbbox("H",anchor="ls")
        proxy = {"id":row["utterance_id"],"group_id":row["group_id"],
            "source_boxes":[source_texts[text_id] for text_id in row["source_text_ids"]]}
        bodies = safe_parts(store,proxy,groups,rendered.size,data["config"])
        allowed_body = np.zeros_like(occupied)
        for body in bodies:
            allowed_body |= body["mask"]
        record = {"utterance":index,"utterance_id":row["utterance_id"],"group_id":row["group_id"],
            "font_size_px":row["font_size_px"],"render_font_size_px":row["font_size_px"],"parts":[]}
        boxes = []
        for part_index,part in enumerate(row["parts"],1):
            layer = Image.new("L",rendered.size)
            draw = ImageDraw.Draw(layer)
            draw_anchor = "ls" if anchor == "h-center" else anchor
            render_lines = [dict(line) for line in part["lines"]]
            if anchor == "h-center":
                for line in render_lines:
                    line["y"] = line["y"] + line["height"] / 2 - (reference_bbox[1] + reference_bbox[3]) / 2
            for line in render_lines:
                draw.text((line["x"],line["y"]),line["text"],font=font,fill=255,anchor=draw_anchor)
            old_pairs = overlaps(part["lines"],font,"lt")
            new_pairs = overlaps(render_lines,font,draw_anchor)
            pixels = np.asarray(layer)>0
            safe = np.asarray(Image.open(source / part["safe_mask"]).convert("L"))>0
            outside_ocr = int(np.count_nonzero(pixels & ~safe))
            outside_body = int(np.count_nonzero(pixels & ~allowed_body))
            cross = int(np.count_nonzero(pixels & occupied))
            total_old_pairs += len(old_pairs);total_new_pairs += len(new_pairs)
            total_outside_ocr += outside_ocr;total_outside_body += outside_body;total_cross += cross
            occupied |= pixels
            rendered.paste((0,0,0),mask=layer)
            path = save(f"glyphs/utterance-{index}-part-{part_index}.png",layer)
            saved = {"part_id":part["part_id"],"glyph_mask":path,"old_line_overlap_pairs":old_pairs,
                "new_line_overlap_pairs":new_pairs,"outside_saved_ocr_safe_pixels":outside_ocr,
                "outside_body_margin_pixels":outside_body,"cross_part_overlap_pixels":cross,
                "draw_origins":[[line["x"],line["y"]] for line in render_lines],
                "draw_anchor":draw_anchor,"reference_glyph":"H" if anchor == "h-center" else None,
                "reference_bbox_ls":list(reference_bbox) if anchor == "h-center" else None}
            record["parts"].append(saved)
            boxes.append(part["bbox"])
            if layer.getbbox():
                boxes.append(list(layer.getbbox()))
        record["boxes"] = boxes
        records.append(record)
    for record in records:
        boxes = record.pop("boxes")
        crop = (max(0,min(b[0] for b in boxes)-28),max(0,min(b[1] for b in boxes)-28),
            min(rendered.width,max(b[2] for b in boxes)+28),min(rendered.height,max(b[3] for b in boxes)+28))
        relative = save(f"comparisons/utterance-{record['utterance']}.png",compare(before,rendered,anchor,crop))
        gallery.append({"utterance":record["utterance"],"comparison":relative})
    rendered_relative = save(f"page-{page_number:02d}.png",rendered)
    comparison_relative = save(f"comparison-page-{page_number:02d}.png",compare(before,rendered,anchor))
    changed = np.any(np.asarray(clean)!=np.asarray(rendered),axis=2)
    if np.any(changed & ~occupied):
        raise ValueError("Composite changed pixels outside glyphs")
    if file_hash(copied) != plan_hash or file_hash(source / plan_relative) != plan_hash or file_hash(source / "result.json") != source_result_hash:
        raise ValueError("Source plan/result was modified")
    status = "needs_review" if total_new_pairs or total_outside_ocr or total_outside_body or total_cross else "completed"
    saved = {"policy":"saved-plan-anchor-only-probe-v1","status":status,"source":str(source),"page":page_number,
        "anchor":anchor,"source_anchor":"lt","font_scale":1.0,"line_spacing":data["config"]["line_spacing"],
        "draw_origins_changed":anchor == "h-center",
        "draw_origin_policy":"baseline = saved y + height/2 - H bbox center (ls)" if anchor == "h-center" else "exact saved x/y, no vertical offset","plan_changed":False,
        "source_plan_sha256":plan_hash,"source_result_sha256":source_result_hash,
        "rendered_source_texts":page["rendered_source_texts"],"groups":len(records),"parts":records,
        "old_line_overlap_pairs":total_old_pairs,"new_line_overlap_pairs":total_new_pairs,
        "outside_saved_ocr_safe_pixels":total_outside_ocr,"outside_body_margin_pixels":total_outside_body,
        "cross_part_overlap_pixels":total_cross,"files":files,"rendered":rendered_relative,"comparison":comparison_relative,
        "fitting_performed":False,"ocr_performed":False,"inpaint_performed":False,"translation_performed":False,
        "pipeline_default_changed":False}
    verify_files(output,saved)
    atomic_json(output / "result.json",saved)
    markdown = [f"# Page {page_number}: thử anchor={anchor}, plan giữ nguyên", "",
        f"Trạng thái: **{status}**; đã vẽ {page['rendered_source_texts']} vùng OCR trong {len(records)} group.", "",
        f"[Trang mới]({rendered_relative}) · [So sánh toàn trang]({comparison_relative}) · [Plan nguyên bản]({plan_relative}) · [Metrics](result.json)", "",
        "Chỉ đổi anchor; font size, text/ngắt dòng, x/y, width/height, line_spacing và vùng OCR/body giữ nguyên. Điểm vẽ dùng chính x/y đã lưu, Y nay là biên dưới của glyph với lb. Không cộng height vào Y, không fit lại, không clip glyph hoặc tự dịch vùng dàn để ép vừa.", "",
        f"Cặp dòng giao glyph: {total_old_pairs} → {total_new_pairs}. Glyph ngoài OCR safe mask cũ: {total_outside_ocr}px; ngoài body margin 10px: {total_outside_body}px; giao part: {total_cross}px.", "",
        "Đây là probe không sửa plan. Các chỉ số vượt vùng được ghi để quan sát tác động thật của việc thay anchor, thay vì tự thay các tham số khác.", "",
        "| Group | So sánh lt / lb |", "| --- | --- |"]
    if anchor == "h-center":
        markdown[6] = "Giữ nguyên font size, text, ngắt dòng, X, plan và vùng dàn. Baseline Y = tâm ô dòng đã lưu − tâm bbox của H (anchor ls). Vẽ nguyên chữ có dấu với anchor ls; dấu không tham gia xác định tâm thân chữ. Không fit lại hoặc clip glyph."
        markdown[-1] = "| --- | --- |"
        markdown[-2] = "| Group | So sánh lt / H-center |"
    markdown.extend(f"| {entry['utterance']} | [Ảnh]({entry['comparison']}) |" for entry in gallery)
    (output / "README.vi.md").write_text("\n".join(markdown)+"\n",encoding="utf-8")
    print(f"page {page_number}: anchor={anchor}, old/new line collisions={total_old_pairs}/{total_new_pairs}, outside OCR/body={total_outside_ocr}/{total_outside_body}")
    print(f"result: {output / 'result.json'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",required=True,type=Path)
    parser.add_argument("--page",required=True,type=int)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--anchor",choices=("lt","lb","ls","h-center"),default="lb")
    args = parser.parse_args()
    probe(args.source,args.page,args.output,args.anchor)
