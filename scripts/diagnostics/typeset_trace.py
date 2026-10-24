"""Visualize actual production typesetting intermediates, without changing layout."""
import html
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi

from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.v6.typeset import draw_body_centered_line, slots
from manga_pipeline.v6.visual import bbox, disk
from manga_pipeline.visual.geometry import read_mask, write_png

COLORS = [(0, 170, 255), (255, 100, 40), (170, 70, 240), (20, 200, 110)]


def finalize_page_trace(directory, original, final):
    """The final comparison includes later groups on the same page."""
    directory = Path(directory)
    record = read_json(directory / "trace.json")
    left, right = original.crop(record["crop"]), final.crop(record["crop"])
    image = Image.new("RGB", (left.width*2,left.height), "white")
    image.paste(left); image.paste(right, (left.width,0))
    write_png(directory / "14-original-restored.png", image)
    old = "Gốc bên trái; tái tạo OCR bên phải."
    new = "Gốc bên trái; tái tạo sau khi mọi group trên trang hoàn tất bên phải."
    for stage in record["stages"]:
        if stage["image"] == "14-original-restored.png":
            stage["caption"] = new
    atomic_json(directory / "trace.json", record)
    for name in ("README.vi.md", "index.html"):
        path = directory / name
        path.write_text(path.read_text(encoding="utf-8").replace(old,new), encoding="utf-8")
    return {path.relative_to(directory.parent).as_posix():file_hash(path)
        for path in (directory / name for name in ("14-original-restored.png", "trace.json", "README.vi.md", "index.html"))}


def tint(image, mask, color, alpha=.40):
    values = np.asarray(image).copy()
    values[mask] = np.round(values[mask] * (1-alpha) + np.array(color) * alpha).astype(np.uint8)
    return Image.fromarray(values)


def font_coverage(path):
    """Read TTF cmap directly so tofu glyphs cannot count as Vietnamese support."""
    import struct
    data = Path(path).read_bytes()
    table_count = struct.unpack_from(">H", data, 4)[0]
    cmap = None
    for index in range(table_count):
        offset = 12 + index * 16
        tag, _, location, _ = struct.unpack_from(">4sIII", data, offset)
        if tag == b"cmap":
            cmap = location
            break
    if cmap is None:
        raise ValueError("Font has no cmap table")
    codes = set()
    count = struct.unpack_from(">H", data, cmap+2)[0]
    for index in range(count):
        _, _, relative = struct.unpack_from(">HHI", data, cmap+4+index*8)
        table = cmap+relative
        form = struct.unpack_from(">H", data, table)[0]
        if form == 4:
            segments = struct.unpack_from(">H", data, table+6)[0]//2
            ends = table+14
            starts = ends+2*segments+2
            deltas = starts+2*segments
            ranges = deltas+2*segments
            for segment in range(segments):
                start = struct.unpack_from(">H", data, starts+2*segment)[0]
                end = struct.unpack_from(">H", data, ends+2*segment)[0]
                delta = struct.unpack_from(">h", data, deltas+2*segment)[0]
                distance = struct.unpack_from(">H", data, ranges+2*segment)[0]
                for code in range(start, min(end, 65534)+1):
                    glyph = (code+delta) & 65535 if not distance else struct.unpack_from(">H", data,
                        ranges+2*segment+distance+2*(code-start))[0]
                    if distance and glyph:
                        glyph = (glyph+delta) & 65535
                    if glyph:
                        codes.add(code)
        elif form == 12:
            groups = struct.unpack_from(">I", data, table+12)[0]
            for group in range(groups):
                start, end, glyph = struct.unpack_from(">III", data, table+16+12*group)
                codes.update(range(start+(glyph == 0), end+1))
    required = set(range(0x1EA0, 0x1EFA)) | {ord(c) for c in "ăĂâÂđĐêÊôÔơƠưƯàáảãạèéẻẽẹìíỉĩịòóỏõọùúủũụỳýỷỹỵ"}
    return {"required_codepoints": len(required), "missing_characters": "".join(chr(c) for c in sorted(required-codes)),
        "font_sha256": file_hash(path)}


def render_trace(store, row, parts, config, font_path, trials, plan, glyphs, original, clean, final, directory, *, mode="bubble-body", growth_steps=()):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    files, stages = {}, []
    group = next((g for page in store.read("bubble_geometry")["pages"]
        for g in page.get("bubble_groups", []) if g["id"] == row.get("group_id")), None)
    if group:
        bounds = group["bbox"]
    else:
        boxes = [s["bbox"] for s in row["source_boxes"]]
        bounds = [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]
    crop = (max(0, int(bounds[0])-32), max(0, int(bounds[1])-32),
        min(original.width, math.ceil(bounds[2])+32), min(original.height, math.ceil(bounds[3])+32))

    def save(name, image, caption, category="stage"):
        path = directory / (name+".png")
        write_png(path, image.crop(crop))
        files[path.relative_to(directory.parent).as_posix()] = file_hash(path)
        record = {"image": path.name, "caption": caption, "category": category}
        stages.append(record)
        return record

    def boxes(image, sources):
        value = image.copy()
        draw = ImageDraw.Draw(value)
        for index, source in enumerate(sources):
            color = COLORS[index % len(COLORS)]
            draw.rectangle(source["bbox"], outline=color, width=3)
            draw.text(tuple(source["bbox"][:2]), f"OCR {index+1}", fill=color)
        return value

    save("00-source-ocr", boxes(original, row["source_boxes"]), "OCR riêng: bbox và thứ tự của từng chuỗi trước khi ghép.")
    masks = {}
    if group:
        raw_page = next(page for page in store.read("bubble_geometry")["pages"] if page["id"] == row["page_id"])
        layout = next(page for page in store.read("layout")["pages"] if page["page"] == raw_page["page"])
        for index, candidate in enumerate(c for c in layout["bubbles"] if c["id"] in group["candidate_ids"]):
            mask = read_mask(store.directory, candidate, original.size)
            save(f"01-candidate-{index+1}", tint(original, mask, COLORS[index % len(COLORS)]),
                f"Candidate {candidate['id']}, score={candidate['confidence']:.6f}, trước hợp nhất.")
        for field, color in (("observed", (0, 170, 255)), ("inferred", (255, 100, 40)), ("refined", (20, 200, 110))):
            masks[field] = read_mask(store.directory, group[field+"_mask"], original.size)
            save("02-"+field, tint(original, masks[field], color),
                f"{field}: {int(masks[field].sum())} px; observed = OR candidate, inferred = phần sửa có bằng chứng ảnh.")
        radius = max(2, min(16, round(math.sqrt(int(masks["refined"].sum()))*config["neck_width_ratio"])))
        opened = ndi.binary_opening(masks["refined"], structure=disk(radius))
        labels, count = ndi.label(opened)
        value = original.copy()
        core_records = []
        for number in range(1, count+1):
            core = labels == number
            area = int(core.sum())
            accepted = area >= config["min_body_area"]
            value = tint(value, core, (20, 200, 110) if accepted else (240, 40, 40))
            core_records.append({"component": number, "area_px": area, "accepted": accepted, "bbox": bbox(core)})
        save("03-opening-cores", value, f"Opening radius={radius}px; xanh: lõi ≥{config['min_body_area']}px², đỏ: lõi dưới ngưỡng.")
        value = original.copy()
        for index, part in enumerate(group["parts"]):
            value = tint(value, read_mask(store.directory, part["body_mask"], original.size), COLORS[index % len(COLORS)])
        for field, color in (("bridge", (255, 0, 180)), ("tail", (255, 200, 0))):
            masks[field] = read_mask(store.directory, group[field+"_mask"], original.size)
            value = tint(value, masks[field], color, .7)
        save("04-parts-bridge-tail", value, "Body theo màu; cầu nối hồng; đuôi vàng. Chỉ bridge đã được geometry nhận diện mới bị loại khỏi vùng chữ.")
        save("05-ocr-group-mapping", boxes(value, row["source_boxes"]), "OCR → group → part. Ghép nội dung không tự tạo thêm part hình học.")
    else:
        radius, core_records = None, []
    save("06-clean", clean, "Ảnh sạch sau erase/white-fill/AOT, trước tái tạo chữ.")
    value = clean.copy()
    body_records = []
    for index, part in enumerate(parts):
        if "body_mask_array" in part:
            body = part["body_mask_array"]
        elif group:
            body = read_mask(store.directory, next(p for p in group["parts"] if p["id"] == part["id"])["body_mask"], original.size)
        else:
            body = part["mask"]
        value = tint(value, body & ~part["mask"], (240, 40, 40))
        value = tint(value, part["mask"], COLORS[index % len(COLORS)])
        draw = ImageDraw.Draw(value)
        x,y = part["anchor"]
        draw.line((x-8,y,x+8,y), fill=(0,0,0), width=2)
        draw.line((x,y-8,x,y+8), fill=(0,0,0), width=2)
        body_records.append({"part_id": part["id"], "body_area_px": int(body.sum()),
            "safe_area_px": int(part["mask"].sum()), "margin_px": part["margin_px"], "anchor": part["anchor"],
            "source_text_ids": [part["source_text_id"]] if "source_text_id" in part else
                [s["source_text_id"] for s in row["source_boxes"] if part["id"] in s.get("part_ids", [])],
            **({"original_ocr_bbox":part["original_ocr_bbox"],"area_share":part["area_share"]} if "area_share" in part else {})})
    caption = "Đỏ: vùng bbox bị cấm bởi margin/bridge/tail; màu: vùng dàn OCR khả dụng; dấu +: tâm bbox OCR gốc." if mode != "bubble-body" else "Đỏ: margin/bridge/tail bị cấm; màu body: safe mask; dấu +: điểm neo trung bình OCR của part."
    save("07-margin-safe-anchor", value, caption)
    for step in growth_steps:
        value = boxes(clean,row["source_boxes"])
        for index, region in enumerate(step["regions"]):
            part = next(p for p in parts if p["id"] == region["id"])
            rectangle = np.zeros_like(part["mask"])
            x1,y1,x2,y2 = region["bbox"]
            rectangle[y1:y2,x1:x2] = True
            value = tint(value,rectangle & part["allowed_mask_array"],COLORS[index % len(COLORS)])
        save(f"07-expansion-{step['round']:03d}",value,
            f"Mở rộng vòng {step['round']}; bbox OCR gốc giữ trên ảnh, hướng bị chặn và diện tích mới nằm trong trace.json.")
    trial_records = []
    for trial_index, trial in enumerate(trials,1):
        font = ImageFont.truetype(str(font_path), trial["font_size_px"])
        value = clean.copy()
        draw = ImageDraw.Draw(value)
        slot_records = []
        for index, part in enumerate(parts):
            available = slots(part["mask"], font, config["line_spacing"], part["anchor"])
            slot_records.append({"part_id": part["id"], "slots": available})
            for slot in available:
                draw.rectangle((slot["x1"], slot["y"], slot["x2"]-1, slot["y"]+slot["height"]-1), outline=COLORS[index % len(COLORS)], width=1)
        save(f"08-trial-{trial_index:03d}-font-{trial['font_size_px']:02d}-slots", value,
            f"Font {trial['font_size_px']}px: dải ngang hợp lệ theo chiều cao font; {trial['status']}.", "trial")
        allocated = value.copy()
        draw = ImageDraw.Draw(allocated)
        for index, item in enumerate(trial["parts"]):
            for line in item["lines"]:
                draw_body_centered_line(draw,line,font,fill=COLORS[index % len(COLORS)])
        if trial["parts"]:
            save(f"09-trial-{trial_index:03d}-font-{trial['font_size_px']:02d}-allocation", allocated,
                f"Phân bổ thật tại {trial['font_size_px']}px: từ liên tiếp mỗi part, kết quả {trial['status']}.", "trial")
        trial_records.append({**trial, "available": slot_records})
    if plan:
        chosen = ImageFont.truetype(str(font_path), plan["font_size_px"])
        value = clean.copy()
        draw = ImageDraw.Draw(value)
        for index, item in enumerate(plan["parts"]):
            for line in item["lines"]:
                draw.rectangle((line["x"], line["y"], line["x"]+line["width"], line["y"]+line["height"]), outline=COLORS[index % len(COLORS)], width=2)
                draw_body_centered_line(draw,line,chosen,fill=(0,0,0))
        save("10-chosen-line-boxes", value, "Ngắt dòng được chọn sau group; bbox và cỡ chữ lấy từ plan thực tế.")
        composed = clean.copy()
        for index, (part, glyph) in enumerate(zip(parts, glyphs), 1):
            save(f"11-part-{index}-glyph", tint(clean, np.asarray(glyph)>0, COLORS[(index-1) % len(COLORS)], .9),
                f"Glyph thật part {index}: kiểm tra từng pixel thuộc safe mask.")
            composed.paste((0,0,0), mask=glyph)
            save(f"12-compose-part-{index}", composed, f"Composite tích lũy sau part {index}; giữ nền sạch ngoài glyph.")
    save("13-final-group", final, "Kết quả group trên trang đang tái tạo.")
    left, right = original.crop(crop), final.crop(crop)
    compare = Image.new("RGB", (left.width*2, left.height), "white")
    compare.paste(left); compare.paste(right, (left.width, 0))
    path = directory / "14-original-restored.png"
    write_png(path, compare)
    files[path.relative_to(directory.parent).as_posix()] = file_hash(path)
    stages.append({"image": path.name, "caption": "Gốc bên trái; tái tạo OCR bên phải.", "category": "stage"})
    record = {"layout_mode": mode, "utterance_id": row["id"], "group_id": row.get("group_id"), "text": row["text"],
        "text_original": row["text_original"], "source_boxes": row["source_boxes"], "word_tokens": row["text"].split(),
        "crop": list(crop), "config": config, "opening_radius_px": radius, "opening_components": core_records,
        "bridge_pixels": int(masks.get("bridge", np.zeros((1,1),dtype=bool)).sum()),
        "tail_pixels": int(masks.get("tail", np.zeros((1,1),dtype=bool)).sum()),
        "parts": body_records, "font_trials": trial_records, "growth_steps":list(growth_steps), "selected_plan": plan, "stages": stages}
    path = directory / "trace.json"
    atomic_json(path, record)
    files[path.relative_to(directory.parent).as_posix()] = file_hash(path)
    normal = [stage for stage in stages if stage["category"] == "stage"]
    sample_names = ("00-source-ocr", "04-parts-bridge-tail", "07-margin-safe-anchor", "10-chosen-line-boxes", "13-final-group")
    samples = [stage for stage in normal if any(stage["image"].startswith(name) for name in sample_names)]
    width, height = 360, 520
    sheet = Image.new("RGB", (width*len(samples), height), "#eeeeee")
    for index, stage in enumerate(samples):
        with Image.open(directory / stage["image"]) as image:
            preview = image.convert("RGB")
            preview.thumbnail((width-12,height-44))
        sheet.paste(preview, (index*width+(width-preview.width)//2, 40))
        ImageDraw.Draw(sheet).text((index*width+8,8), stage["image"].split(".")[0], fill="black")
    path = directory / "contact-sheet.png"
    write_png(path, sheet)
    files[path.relative_to(directory.parent).as_posix()] = file_hash(path)
    title = "Tái tạo " + row["id"]
    markdown = ["# "+title, "", "[Toàn bộ dữ liệu trung gian](trace.json) · [Contact sheet](contact-sheet.png)", "",
        "## Chuỗi trước/sau group", ""]
    markdown.extend(f"- OCR {i}: `{text}`" for i,text in enumerate(row["text_original"],1))
    markdown.extend(["", "Chuỗi chung: `"+row["text"]+"`", "", "## Body và vùng chữ", "",
        "| Part | Body px² | Safe px² | Margin px | OCR nguồn |", "| --- | --- | --- | --- | --- |"])
    for part in body_records:
        markdown.append(f"| {part['part_id'].split(':')[-1]} | {part['body_area_px']} | {part['safe_area_px']} | {part['margin_px']} | {', '.join(part['source_text_ids'])} |")
    if mode != "bubble-body":
        markdown.extend(["", "## Phân bổ theo diện tích OCR gốc", "",
            "| Vùng | Diện tích quota px² | Tỷ lệ diện tích | Ký tự mục tiêu | Ký tự phân bổ | Đoạn từ |",
            "| --- | --- | --- | --- | --- | --- |"])
        assigned_parts = plan["parts"] if plan else (trials[-1]["parts"] if trials else [])
        for assigned in assigned_parts:
            markdown.append(f"| {assigned['part_id']} | {assigned.get('allocation_area_px')} | "
                f"{assigned.get('area_share',0):.2%} | {assigned.get('target_characters',0):.2f} | "
                f"{assigned.get('allocated_characters')} | {assigned.get('word_range')} |")
    markdown.extend(["", "## Visual từng bước", ""])
    for stage in stages:
        markdown.extend([f"### {stage['image']}", "", stage["caption"], "", f"![{stage['image']}]({stage['image']})", ""])
    (directory / "README.vi.md").write_text("\n".join(markdown), encoding="utf-8")
    cards = "".join(f'<figure><figcaption>{html.escape(s["caption"])}</figcaption><a href="{s["image"]}"><img loading="lazy" src="{s["image"]}"></a></figure>' for s in normal)
    attempts = "".join(f'<figure><figcaption>{html.escape(s["caption"])}</figcaption><a href="{s["image"]}"><img loading="lazy" src="{s["image"]}"></a></figure>' for s in stages if s["category"] == "trial")
    (directory / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>'+html.escape(title)+
        '</title><style>body{font-family:Arial;margin:24px;background:#eee}main{display:flex;flex-wrap:wrap;gap:12px}figure{margin:0;padding:12px;background:white;max-width:460px}img{max-width:100%}figcaption{margin-bottom:10px}pre{white-space:pre-wrap}</style><h1>'+html.escape(title)+
        '</h1><p><a href="trace.json">JSON</a> · <a href="README.vi.md">Markdown</a></p><pre>'+html.escape(row["text"])+
        '</pre><main>'+cards+'</main><details><summary>Mọi lần thử font, slots và phân bổ từ</summary><main>'+attempts+'</main></details>', encoding="utf-8")
    for name in ("README.vi.md", "index.html"):
        files[(directory/name).relative_to(directory.parent).as_posix()] = file_hash(directory/name)
    return files, {"utterance_id": row["id"], "directory": directory.name,
        "stage_images": len(stages), "font_trials": len(trials), "parts": len(parts)}
