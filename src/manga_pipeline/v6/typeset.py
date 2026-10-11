"""Measured, mask-contained layout with a common font size for every group."""
import copy
import math
import os
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi
from ..progress import NullProgress
from ..storage.io import atomic_json, file_hash, fingerprint
from ..visual.geometry import clipped_box, read_mask, write_png
from ..visual.stages import verify_files
from .visual import bbox, disk, save_mask
from .translation import shorten


def resolve_font(config):
    candidates = [config.get("font_path"), str(Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts/arial.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            font = ImageFont.truetype(str(candidate), config["font_min_px"])
            if not font.getmask("Tiếng Việt Ắ ỵ").getbbox():
                raise ValueError("Font cannot render Vietnamese")
            return Path(candidate)
    raise ValueError("No Vietnamese-capable font found; configure v6.font_path")


def intervals(row):
    padded = np.pad(row.astype(np.int8), (1,1))
    edges = np.diff(padded)
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist()))


def slots(mask, font, spacing, anchor):
    bounds = bbox(mask)
    if bounds is None:
        return []
    ascent, descent = font.getmetrics()
    height = ascent+descent
    step = max(height, math.ceil(height*spacing))
    x1,y1,x2,y2 = bounds
    count = (y2-y1)//step
    if not count:
        return []
    top = y1+max(0, (y2-y1-(count-1)*step-height)//2)
    output = []
    for y in range(top, y2-height+1, step):
        permitted = mask[y:y+height].all(axis=0)
        runs = intervals(permitted)
        if not runs:
            continue
        # A line chooses a single contiguous run, never spans a hole or bridge.
        left, right = max(runs, key=lambda pair: ((pair[1]-pair[0]), -abs((pair[0]+pair[1])/2-anchor[0])))
        if right-left >= max(2, font.getlength("i")):
            output.append({"x1": left, "x2": right, "y": y, "height": height})
    return output


def line_width(text, font):
    box = font.getbbox(text, anchor="lt")
    return max(font.getlength(text), box[2]-min(0,box[0]))


def segment_layout(words, available, font, anchor):
    if not words:
        return []
    best = None
    for start in range(len(available)):
        index, lines = 0, []
        for slot in available[start:]:
            begin = index
            while index < len(words) and line_width(" ".join(words[begin:index+1]), font) <= slot["x2"]-slot["x1"]-2:
                index += 1
            if index == begin:
                break
            text = " ".join(words[begin:index])
            width = line_width(text, font)
            lines.append({"text": text, "x": (slot["x1"]+slot["x2"]-width)/2,
                          "y": slot["y"], "width": width, "height": slot["height"]})
            if index == len(words):
                middle = (lines[0]["y"]+lines[-1]["y"]+slot["height"])/2
                score = (len(lines), abs(middle-anchor[1]))
                if best is None or score < best[0]:
                    best = (score, lines)
                break
    return best[1] if best else None


def allocate(text, parts, font, config):
    """Contiguous word slices, balanced by measured capacity rather than OCR counts."""
    words = text.split()
    prepared = [(part, slots(part["mask"], font, config["line_spacing"], part["anchor"])) for part in parts]
    capacities = [sum(s["x2"]-s["x1"] for s in available) for _,available in prepared]
    if not words or not sum(capacities):
        return None
    density = line_width(text, font)/max(1,sum(capacities))
    # Dynamic program chooses one consecutive slice per contour.
    states = {0: (0., [])}
    for (part, available), capacity in zip(prepared, capacities):
        next_states = {}
        for begin, (cost, plan) in states.items():
            for end in range(begin, len(words)+1):
                lines = segment_layout(words[begin:end], available, font, part["anchor"])
                if lines is None:
                    continue
                used = sum(line["width"] for line in lines)
                penalty = capacity*(used/max(1,capacity)-density)**2
                proposal = (cost+penalty, plan+[{"part_id": part["id"], "lines": lines,
                    "word_range": [begin,end], "capacity_px": capacity, "used_width_px": used}])
                if end not in next_states or proposal[0] < next_states[end][0]:
                    next_states[end] = proposal
        states = next_states
        if not states:
            return None
    if len(words) not in states:
        return None
    return states[len(words)][1]


def fit(text, parts, font_path, config):
    for size in range(config["font_max_px"], config["font_min_px"]-1, -1):
        font = ImageFont.truetype(str(font_path), size)
        plan = allocate(text, parts, font, config)
        if plan is None:
            continue
        if len(text.split()) >= len(parts) and any(not p["lines"] for p in plan):
            continue
        glyphs = []
        good = True
        for item, part in zip(plan, parts):
            layer = Image.new("L", (part["mask"].shape[1], part["mask"].shape[0]))
            draw = ImageDraw.Draw(layer)
            for line in item["lines"]:
                draw.text((line["x"],line["y"]), line["text"], font=font, fill=255, anchor="lt")
            mask = np.asarray(layer) > 0
            if np.any(mask & ~part["mask"]):
                good = False
                break
            glyphs.append(layer)
        if good:
            rendered = " ".join(line["text"] for item in plan for line in item["lines"]).split()
            if rendered != text.split():
                raise ValueError("Typesetting lost, duplicated or reordered tokens")
            return {"font_size_px": size, "parts": plan}, glyphs
    return None, []


def safe_parts(store, row, groups, size, config):
    h,w = size[1],size[0]
    group = groups.get(row.get("group_id"))
    result = []
    if group:
        bridge = read_mask(store.directory, group["bridge_mask"], size)
        tail = read_mask(store.directory, group["tail_mask"], size)
        parts = group["parts"]
    else:
        bridge = tail = np.zeros((h,w),dtype=bool)
        parts = [{"id": row["id"]+":standalone", "bbox": row["bbox"]}]
    for part in parts:
        if group:
            body = read_mask(store.directory, part["body_mask"], size)
        else:
            body = np.zeros((h,w),dtype=bool)
            for source in row["source_boxes"]:
                x1,y1,x2,y2 = clipped_box(source["bbox"], size)
                body[y1:y2,x1:x2] = True
        bounds = bbox(body)
        short = min(bounds[2]-bounds[0],bounds[3]-bounds[1]) if bounds else 0
        margin = max(config["body_margin_px"], round(short*config["body_margin_ratio"]))
        safe = ndi.binary_erosion(body, structure=disk(margin)) & ~bridge & ~tail
        anchors = [s for s in row["source_boxes"] if part["id"] in s.get("part_ids", [])]
        anchors = anchors or row["source_boxes"]
        points = [[(s["bbox"][0]+s["bbox"][2])/2,(s["bbox"][1]+s["bbox"][3])/2] for s in anchors]
        anchor = np.mean(points,axis=0).tolist() if points else [(bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2]
        rank = min((s.get("reading_order",0) for s in anchors), default=0)
        result.append({"id": part["id"], "mask": safe, "anchor": anchor, "margin_px": margin,
                       "reading_order": rank, "bbox": part["bbox"]})
    result.sort(key=lambda p: (p["reading_order"],p["bbox"][1],-p["bbox"][0]))
    return result


def measured_budget(parts, font_path, config):
    font = ImageFont.truetype(str(font_path),config["font_min_px"])
    available = [slots(p["mask"],font,config["line_spacing"],p["anchor"]) for p in parts]
    return {"font_size_px": config["font_min_px"], "usable_lines": sum(len(x) for x in available),
            "total_line_width_px": sum(s["x2"]-s["x1"] for items in available for s in items)}


def typeset(store, provider, progress=None):
    document = store.read("translate")
    raw = store.read("bubble_geometry")
    config = store.manifest["config"]["v6"]
    font_path = resolve_font(config)
    groups = {g["id"]: g for p in raw["pages"] for g in p.get("bubble_groups",[])}
    clean = {p["page_id"]: p for p in store.read("inpaint")["pages"]} if "inpaint" in store.steps else {}
    progress = progress or NullProgress()
    progress.reset(total=len(raw["pages"])*2,unit="render")
    pages, failures, review = [], [], []
    for page in raw["pages"]:
        rows = [r for r in document["utterances"] if r["page_id"] == page["id"]]
        source = store.manifest["source"]["images"][page["page"]-1]
        for style in ("natural","localized"):
            key = f"page:{page['id']}:{style}"
            request = {"policy":"mask-typeset-v6.1","rows":rows,"source":source,
                       "geometry":fingerprint(page),"clean":clean.get(page["id"]),
                       "font_sha256":file_hash(font_path),"config":config,"style":style}
            try:
                cached = store.target("typeset",key,request)
                if cached is not None:
                    verify_files(store.directory,cached)
                    pages.append(cached)
                    review.extend(cached.get("needs_review",[]))
                    progress.advance()
                    continue
                if file_hash(source["path"]) != source["sha256"]:
                    raise ValueError("Source image changed")
                if page["id"] in clean:
                    verify_files(store.directory,clean[page["id"]])
                    base = store.directory / clean[page["id"]]["clean_path"]
                    render_kind = "clean_render"
                else:
                    base = Path(source["path"])
                    render_kind = "placement_preview_original_text_present"
                with Image.open(base) as image:
                    output = image.convert("RGB")
                directory = store.artifact_path("typeset").parent / "pages" / f"page-{page['page']}" / style
                files, plans, needs_review = {}, [], []
                for row in rows:
                    if row.get("content_type") == "unknown":
                        issue = {"utterance_id":row["id"],"style":style,"reason":"unresolved_content_role"}
                        needs_review.append(issue)
                        plans.append({**issue,"status":"needs_review","image_edit_performed":False})
                        continue
                    text = row["translations"][style]["translation"]
                    parts = safe_parts(store,row,groups,output.size,config)
                    budget = measured_budget(parts,font_path,config)
                    plan, glyphs = fit(text,parts,font_path,config)
                    attempts = []
                    if plan is None and budget["total_line_width_px"] > 0:
                        for attempt in range(1,config["shorten_attempts"]+1):
                            try:
                                shorter = shorten(store,provider,row,style,text,budget,attempt)
                                attempts.append({"attempt":attempt,**shorter})
                                text = shorter["translation"]
                                plan,glyphs = fit(text,parts,font_path,config)
                                if plan is not None:
                                    break
                            except Exception as exc:
                                attempts.append({"attempt":attempt,"error":str(exc)})
                    saved = {"utterance_id":row["id"],"group_id":row.get("group_id"),"translation":text,
                             "status":"fitted" if plan else "needs_review","attempts":attempts,
                             "font":{"path":str(font_path),"sha256":request["font_sha256"],
                                     "size_px":plan["font_size_px"] if plan else config["font_min_px"]},
                             "layout_budget":budget,"parts":plan["parts"] if plan else []}
                    if row["translations"][style].get("needs_review") or attempts:
                        saved["semantic_review_required"] = True
                    if plan is None:
                        issue = {"utterance_id":row["id"],"style":style,"reason":"overflow_at_min_font" if budget["total_line_width_px"] else "empty_safe_region"}
                        needs_review.append(issue)
                    for index,part in enumerate(parts):
                        stem = fingerprint([row["id"],part["id"]])[:16]
                        safe = save_mask(store.directory,directory,stem+"-safe",part["mask"])
                        files[safe["mask_path"]] = safe["mask_sha256"]
                        if plan:
                            record = plan["parts"][index]
                            record.update(safe_mask=safe,anchor=part["anchor"],margin_px=part["margin_px"])
                            path = directory / (stem+"-glyph.png")
                            write_png(path,glyphs[index])
                            relative = path.relative_to(store.directory).as_posix()
                            files[relative] = file_hash(path)
                            record["glyph_mask"] = {"mask_path":relative,"mask_sha256":files[relative]}
                            output.paste((0,0,0),mask=glyphs[index])
                    plans.append(saved)
                path = directory / "rendered.png"
                write_png(path,output)
                files[path.relative_to(store.directory).as_posix()] = file_hash(path)
                plan_path = directory / "typeset-plan.json"
                atomic_json(plan_path,{"policy":"mask-typeset-v6.1","render_kind":render_kind,"plans":plans})
                files[plan_path.relative_to(store.directory).as_posix()] = file_hash(plan_path)
                cached = {"page_id":page["id"],"page":page["page"],"style":style,"render_kind":render_kind,
                          "files":files,"plans":plans,"needs_review":needs_review,
                          "effects_rendered":False,"runtime_limits":{"shorten_attempts":config["shorten_attempts"]}}
                store.commit_target("typeset",key,request,result=cached)
                pages.append(cached)
                review.extend(needs_review)
            except Exception as exc:
                store.commit_target("typeset",key,request,error=str(exc))
                failures.append({"page_id":page["id"],"style":style,"error":str(exc)})
            progress.advance()
    return {"policy":"mask-typeset-v6.1","pages":pages,"needs_review":review,"failures":failures,
            "effects_image_edits":False,"clean_pages_available":bool(clean)}
