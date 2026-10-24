"""Read saved plans/glyphs and audit line collisions; no fitting or model calls."""
import argparse
import json
import math
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT / "src"))

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.visual.stages import verify_files

COLORS = [(0,140,255),(230,90,15),(140,30,220),(15,170,70)]


def draw_position(line):
    return line.get("baseline_y",line["y"]), line.get("draw_anchor","lt")


def local_line(line,font,anchor=None):
    y, saved_anchor = draw_position(line)
    if anchor is None:
        anchor = saved_anchor
    else:
        y = line["y"]
    box = font.getbbox(line["text"],anchor=anchor)
    x0,y0 = math.floor(line["x"]+min(0,box[0]))-2,math.floor(y+min(0,box[1]))-2
    width = math.ceil(line["x"]+box[2])-x0+3
    height = math.ceil(y+box[3])-y0+3
    image = Image.new("L",(max(1,width),max(1,height)))
    ImageDraw.Draw(image).text((line["x"]-x0,y-y0),line["text"],font=font,fill=255,anchor=anchor)
    box = image.getbbox()
    return image,(x0,y0),list(box) if box else None


def pair_overlap(a,b):
    image_a,(ax,ay),_ = a
    image_b,(bx,by),_ = b
    left,top = max(ax,bx),max(ay,by)
    right,bottom = min(ax+image_a.width,bx+image_b.width),min(ay+image_a.height,by+image_b.height)
    if right <= left or bottom <= top:
        return 0
    ma = np.asarray(image_a.crop((left-ax,top-ay,right-ax,bottom-ay)))>0
    mb = np.asarray(image_b.crop((left-bx,top-by,right-bx,bottom-by)))>0
    return int(np.count_nonzero(ma & mb))


def crop_visual(directory,page_number,utterance,part_index,part,lines,font,page_image):
    bounds = part["bbox"]
    crop = (max(0,bounds[0]-18),max(0,bounds[1]-18),min(page_image.width,bounds[2]+18),min(page_image.height,bounds[3]+18))
    w,h = crop[2]-crop[0],crop[3]-crop[1]
    counts = np.zeros((h,w),dtype=np.uint16)
    colors = Image.new("RGB",(w,h),"white")
    planned = page_image.crop(crop)
    actual = page_image.crop(crop)
    planned_draw,actual_draw = ImageDraw.Draw(planned),ImageDraw.Draw(actual)
    for index,line in enumerate(lines):
        color = COLORS[index % len(COLORS)]
        image = Image.new("L",(w,h))
        y, anchor = draw_position(line)
        ImageDraw.Draw(image).text((line["x"]-crop[0],y-crop[1]),line["text"],font=font,fill=255,anchor=anchor)
        counts += (np.asarray(image)>0).astype(np.uint16)
        colors.paste(color,mask=image)
        box = font.getbbox(line["text"],anchor=anchor)
        planned_draw.rectangle((line["x"]-crop[0],line["y"]-crop[1],line["x"]-crop[0]+line["width"],line["y"]-crop[1]+line["height"]),outline=color,width=1)
        actual_draw.rectangle((line["x"]-crop[0]+box[0],y-crop[1]+box[1],line["x"]-crop[0]+box[2],y-crop[1]+box[3]),outline=color,width=1)
    colored = np.asarray(colors).copy()
    colored[counts>1] = (255,0,180)
    colors = Image.fromarray(colored)
    images = [("Saved render",page_image.crop(crop)),("Planned line boxes",planned),("Actual glyph boxes",actual),("Line colors / pink overlap",colors)]
    combined = Image.new("RGB",(w*4,h+32),"white")
    draw = ImageDraw.Draw(combined)
    for index,(title,image) in enumerate(images):
        combined.paste(image,(index*w,32));draw.text((index*w+5,8),title,fill="black")
    target = directory / f"page-{page_number:02d}-utterance-{utterance}-part-{part_index}.png"
    combined.save(target)
    return {"image":target.name,"sha256":file_hash(target),"crop":list(crop),
        "overlap_pixel_locations":int(np.count_nonzero(counts>1)),"maximum_layers_at_pixel":int(counts.max())}


def audit(source,output):
    source,output = source.resolve(),output.resolve()
    if output.exists():
        raise ValueError("Use a new audit directory")
    result = read_json(source / "result.json")
    baseline = Path(result["source_probe"])
    output.mkdir(parents=True)
    pages,affected = [],[]
    totals = {"pages":0,"groups":0,"regions":0,"lines":0,"regions_with_pixel_overlap":0,
        "groups_with_pixel_overlap":0,"line_pairs_with_pixel_overlap":0,"regions_with_bbox_overlap":0,
        "line_pairs_with_bbox_overlap":0,"lines_taller_than_planned":0,"cross_part_overlap_pixels":0,
        "saved_glyph_reconstruction_mismatch_pixels":0,"final_composite_mismatch_pixels":0,"verified_artifacts":0}
    for page in result["pages"]:
        verify_files(source,page)
        totals["verified_artifacts"] += len(page["files"])
        number = page["page"]
        data = read_json(source / "plans" / f"page-{number:02d}.json")
        font_path = Path(data["font_path"])
        if file_hash(font_path) != data["font_sha256"]:
            raise ValueError("Recorded font changed")
        page_image = Image.open(source / "pages" / f"page-{number:02d}.png").convert("RGB")
        expected = Image.open(baseline / "pages" / f"page-{number}" / "clean.png").convert("RGB")
        occupied = np.zeros((page_image.height,page_image.width),dtype=bool)
        page_regions,affected_groups = [],set()
        for utterance,row in enumerate(data["plans"],1):
            totals["groups"] += 1
            if row["status"] != "fitted":
                continue
            font = ImageFont.truetype(str(font_path),row["font_size_px"])
            for part_index,part in enumerate(row["parts"],1):
                totals["regions"] += 1
                local = []
                line_metrics = []
                for line_index,line in enumerate(part["lines"],1):
                    totals["lines"] += 1
                    image,origin,pixel_bbox = local_line(line,font)
                    actual_bbox = font.getbbox(line["text"],anchor="lt")
                    baseline_bbox = font.getbbox(line["text"],anchor="ls")
                    real_height = actual_bbox[3]-actual_bbox[1]
                    taller = real_height > line["height"]
                    totals["lines_taller_than_planned"] += int(taller)
                    local.append((image,origin,pixel_bbox))
                    line_metrics.append({"line":line_index,"text":line["text"],"x":line["x"],"y":line["y"],
                        "planned_height_px":line["height"],"actual_height_px":real_height,
                        "bbox_lt":list(actual_bbox),"bbox_ls":list(baseline_bbox),
                        "global_ink_bbox":[origin[0]+pixel_bbox[0],origin[1]+pixel_bbox[1],origin[0]+pixel_bbox[2],origin[1]+pixel_bbox[3]] if pixel_bbox else None})
                pixel_pairs,bbox_pairs = [],[]
                for a in range(len(local)):
                    for b in range(a+1,len(local)):
                        pa,pb = line_metrics[a]["global_ink_bbox"],line_metrics[b]["global_ink_bbox"]
                        if pa and pb:
                            width = max(0,min(pa[2],pb[2])-max(pa[0],pb[0]))
                            height = max(0,min(pa[3],pb[3])-max(pa[1],pb[1]))
                            if width and height:
                                bbox_pairs.append({"line_indices":[a+1,b+1],"vertical_overlap_px":height,"bbox_intersection_px":width*height})
                        count = pair_overlap(local[a],local[b])
                        if count:
                            pixel_pairs.append({"line_indices":[a+1,b+1],"shared_pixels":count})
                stored = Image.open(source / part["glyph_mask"]).convert("L")
                reconstructed = Image.new("L",stored.size)
                draw = ImageDraw.Draw(reconstructed)
                for line in part["lines"]:
                    y, anchor = draw_position(line)
                    draw.text((line["x"],y),line["text"],font=font,fill=255,anchor=anchor)
                mismatch = int(np.count_nonzero(np.asarray(stored)!=np.asarray(reconstructed)))
                totals["saved_glyph_reconstruction_mismatch_pixels"] += mismatch
                pixels = np.asarray(stored)>0
                totals["cross_part_overlap_pixels"] += int(np.count_nonzero(pixels & occupied))
                occupied |= pixels
                expected.paste((0,0,0),mask=stored)
                totals["line_pairs_with_pixel_overlap"] += len(pixel_pairs)
                totals["line_pairs_with_bbox_overlap"] += len(bbox_pairs)
                totals["regions_with_pixel_overlap"] += int(bool(pixel_pairs))
                totals["regions_with_bbox_overlap"] += int(bool(bbox_pairs))
                record = {"page":number,"utterance":utterance,"part":part_index,"utterance_id":row["utterance_id"],
                    "part_id":part["part_id"],"font_size_px":row["font_size_px"],"font_metrics":list(font.getmetrics()),
                    "line_top_steps_px":[b["y"]-a["y"] for a,b in zip(part["lines"],part["lines"][1:])],
                    "bbox":part["bbox"],"lines":line_metrics,"pixel_overlap_pairs":pixel_pairs,"bbox_overlap_pairs":bbox_pairs,
                    "saved_glyph_mismatch_pixels":mismatch,"expanded":part.get("expanded",False)}
                if pixel_pairs:
                    affected_groups.add(row["utterance_id"])
                    record["visual"] = crop_visual(output,number,utterance,part_index,part,part["lines"],font,page_image)
                    affected.append(record)
                page_regions.append(record)
        final_mismatch = int(np.count_nonzero(np.any(np.asarray(expected)!=np.asarray(page_image),axis=2)))
        totals["final_composite_mismatch_pixels"] += final_mismatch
        totals["groups_with_pixel_overlap"] += len(affected_groups)
        totals["pages"] += 1
        pages.append({"page":number,"regions":page_regions,"affected_groups":len(affected_groups),"final_composite_mismatch_pixels":final_mismatch})
        print(f"page {number}: line overlap in {len(affected_groups)} groups",flush=True)
    artifact = {"source":str(source),"source_result_sha256":file_hash(source / "result.json"),"totals":totals,
        "expanded_groups":result["expanded_groups"],"source_marked_completed":result["status"],
        "method":result["method"],"line_spacing":result["typeset_config"]["line_spacing"],"pages":pages,
        "classification":("line collision inside a single region; declared font metrics underestimate Vietnamese ink height"
            if totals["line_pairs_with_pixel_overlap"] else "no line glyph collisions detected in these saved plans")}
    atomic_json(output / "analysis.json",artifact)
    markdown = ["# Điều tra chữ tiếng Việt bị chồng dòng", "",
        f"Source: `{source.name}`. Chỉ đọc artifact đã lưu và raster từng dòng từ plan; không chạy fitting, OCR, inpaint, dịch hoặc pipeline.", "",
        "## Kết quả", "",
        f"Kiểm tra {totals['pages']} trang, {totals['groups']} group, {totals['regions']} vùng dàn và {totals['lines']} dòng.",
        f"**{totals['groups_with_pixel_overlap']} group / {totals['regions_with_pixel_overlap']} vùng** có các glyph dòng khác nhau giao nhau tại cùng pixel; **{totals['line_pairs_with_pixel_overlap']} cặp dòng** bị chồng. Ngoài ra {totals['line_pairs_with_bbox_overlap']} cặp có ink bbox giao nhau.",
        f"Có {totals['lines_taller_than_planned']} dòng cao hơn height trong plan. Glyph thuộc các part khác nhau giao nhau: {totals['cross_part_overlap_pixels']} pixel.", "",
        "## Nguyên nhân", "",
        f"`slots()` dùng `font.getmetrics()` để tính `height=ascent+descent`, rồi `step=ceil(height*{result['typeset_config']['line_spacing']})`. Font AstroCity khai báo metrics thấp hơn chiều cao glyph tiếng Việt có dấu. `segment_layout()` lưu height này vào mỗi dòng; renderer vẽ bằng `anchor=lt`, nhưng không đo chiều cao ink thực tế để quyết định bước dòng.", "",
        "`fit_ocr_area()` vẽ mọi dòng của một region vào chung một glyph mask rồi chỉ kiểm tra mask có nằm trong safe region không. `typeset_cached_translation.py` kiểm tra glyph mask đó với các mask part đã vẽ trước. Hai dòng chồng nhau trong cùng mask không bị phát hiện. Vì vậy status fitted/completed và outside_safe=0 không xác nhận các dòng không đè nhau.", "",
        "## Bằng chứng trang 1, group 1", ""]
    first = next((record for record in affected if record["page"]==1 and record["utterance"]==1),None)
    if first:
        markdown.extend([f"Font {first['font_size_px']}px; metrics ascent/descent `{first['font_metrics']}`; bước dòng `{first['line_top_steps_px']}`.", "",
            "| Dòng | Text | Y | Height plan | Height ink thực tế |", "| --- | --- | --- | --- | --- |"])
        markdown.extend(f"| {line['line']} | {line['text']} | {line['y']} | {line['planned_height_px']} | {line['actual_height_px']} |" for line in first["lines"])
        markdown.extend(["",f"Pixel giao nhau: `{first['pixel_overlap_pairs']}`.","",f"![Bốn panel đối chiếu]({first['visual']['image']})", ""])
    markdown.extend(["## Vì sao không phải do hai vùng mở rộng chạm nhau?", "",
        f"Lượt source ghi nhận {result['expanded_groups']} group mở rộng. Các mask part khác nhau không giao glyph ({totals['cross_part_overlap_pixels']} pixel). Raster lại đúng font/text/tọa độ tái hiện saved glyph, sai khác {totals['saved_glyph_reconstruction_mismatch_pixels']} pixel; paste các glyph lên clean cache tái hiện trang cuối, sai khác {totals['final_composite_mismatch_pixels']} pixel. Lỗi có ngay trong glyph mask riêng, trước composite nền.", "",
        "## Hướng sửa đề xuất, chưa sửa renderer trong lượt điều tra", "",
        "1. Đo ascent/descent hoặc ink bbox thực tế của các ký tự/dòng tiếng Việt trong group ở từng font size. Dùng baseline chung và bước dòng đủ cho bao glyph thực tế cộng khoảng cách, thay vì chỉ tin metrics khai báo của TTF.",
        "2. Raster từng dòng riêng; kiểm tra giao glyph giữa các dòng và khoảng hở tối thiểu trước khi hợp mask. Kiểm tra mask hợp với safe region vẫn giữ nguyên, nhưng không thay thế kiểm tra chồng dòng.",
        "3. Tính lại capacity/slots sau khi đổi bước dòng. Nếu không vừa, hạ common font hoặc mở rộng theo hướng hợp lệ, giữ margin viền 10px và bảo vệ vùng láng giềng. Không thay font size riêng từng vùng trong cùng group.",
        "4. Bổ sung regression có dấu tiếng Việt với AstroCity. Kiểm tra font có glyph/cmap chỉ xác nhận ký tự tồn tại, không xác nhận metrics hoặc khoảng dòng.", "",
        "## Vị trí có chồng glyph", "",
        "| Trang | Group trong trang | Part | Font px | Cặp giao pixel | Visual |", "| --- | --- | --- | --- | --- | --- |"])
    for record in affected:
        markdown.append(f"| {record['page']} | {record['utterance']} | {record['part']} | {record['font_size_px']} | {len(record['pixel_overlap_pairs'])} | [Ảnh]({record['visual']['image']}) |")
    markdown.extend(["", "[Chi tiết tọa độ, metrics và cặp giao nhau](analysis.json). Ảnh: saved render / hộp dòng theo plan / hộp ink thực tế / từng dòng theo màu, vùng giao pixel tô hồng.", ""])
    (output / "README.vi.md").write_text("\n".join(markdown),encoding="utf-8")
    print(json.dumps(totals),flush=True)


def compare_saved_typesets(before,after,output):
    """Compare complete cached runs; write visuals without rendering new glyphs."""
    before,after,output = before.resolve(),after.resolve(),output.resolve()
    previous,current = read_json(before / "result.json"),read_json(after / "result.json")
    if previous["translation_sha256"] != current["translation_sha256"]:
        raise ValueError("Spacing comparison requires the same cached translation")
    previous_pages = {page["page"]:page for page in previous["pages"]}
    analysis = read_json(output / "analysis.json")
    old_audit = before / "investigation-overlap-20261012" / "analysis.json"
    old_pairs = None
    if old_audit.is_file():
        old = read_json(old_audit)
        if old["source_result_sha256"] == file_hash(before / "result.json"):
            old_pairs = old["totals"]["line_pairs_with_pixel_overlap"]
    lines = ["# So sánh plan giãn dòng", "",
        f"Line spacing **{previous['typeset_config']['line_spacing']} → {current['typeset_config']['line_spacing']}**. Font được vẽ đúng cỡ trong plan mới; render scale=1.0. Tính lại ngắt dòng/font/tọa độ theo vùng OCR cũ.", "",
        f"Cặp dòng giao glyph **{old_pairs} → {analysis['totals']['line_pairs_with_pixel_overlap']}**; số group còn giao glyph: {analysis['totals']['groups_with_pixel_overlap']}/{analysis['totals']['groups']}.", "",
        "[Chi tiết audit](README.vi.md) · [Gallery so sánh](comparison-gallery.html)", "",
        "| Trang | Group | Font cũ | Font mới | So sánh |", "| --- | --- | --- | --- | --- |"]
    cards,files = [],{}
    for page in current["pages"]:
        number = page["page"]
        verify_files(before,previous_pages[number])
        left = Image.open(before / "pages" / f"page-{number:02d}.png").convert("RGB")
        right = Image.open(after / "pages" / f"page-{number:02d}.png").convert("RGB")
        if left.size != right.size:
            raise ValueError("Comparison page dimensions differ")
        old_plans = {row["group_id"]:row for row in read_json(before / "plans" / f"page-{number:02d}.json")["plans"]}
        new_plans = read_json(after / "plans" / f"page-{number:02d}.json")["plans"]
        for index,row in enumerate(new_plans,1):
            if not row["parts"]:
                continue
            if row.get("render_font_scale",1.0) != 1.0:
                raise ValueError("The new run still scales its render font")
            boxes = [part["bbox"] for part in row["parts"]]
            crop = (max(0,min(b[0] for b in boxes)-24),max(0,min(b[1] for b in boxes)-24),
                min(left.width,max(b[2] for b in boxes)+24),min(left.height,max(b[3] for b in boxes)+24))
            a,b = left.crop(crop),right.crop(crop)
            image = Image.new("RGB",(a.width*2,a.height+30),"white")
            image.paste(a,(0,30));image.paste(b,(a.width,30))
            draw = ImageDraw.Draw(image)
            draw.text((5,8),f"Spacing {previous['typeset_config']['line_spacing']}",fill="black")
            draw.text((a.width+5,8),f"Spacing {current['typeset_config']['line_spacing']}; full font",fill="black")
            name = f"spacing-page-{number:02d}-utterance-{index}.png"
            image.save(output / name)
            files[name] = file_hash(output / name)
            old_font = old_plans[row["group_id"]]["font_size_px"]
            lines.append(f"| {number} | {index} | {old_font} | {row['font_size_px']} | [Ảnh]({name}) |")
            cards.append(f'<article><h2>Trang {number} · group {index} · font {old_font} → {row["font_size_px"]}px</h2>'
                f'<a href="{name}"><img loading="lazy" src="{name}"></a></article>')
    (output / "spacing-comparison.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    (output / "comparison-gallery.html").write_text('<!doctype html><meta charset="utf-8"><title>Line spacing comparison</title>'
        '<style>body{font-family:Arial;background:#eee;margin:24px}article{background:white;padding:16px;margin:16px 0}img{max-width:100%}</style>'
        '<h1>Plan giãn dòng, font nguyên cỡ</h1>'+"".join(cards),encoding="utf-8")
    atomic_json(output / "comparison-files.json",files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--compare-with",type=Path)
    args = parser.parse_args()
    audit(args.source,args.output)
    if args.compare_with:
        compare_saved_typesets(args.compare_with,args.source,args.output)
