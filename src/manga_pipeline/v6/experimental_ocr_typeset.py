"""Opt-in OCR-constrained, area-proportional typesetting; not a pipeline default."""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .typeset import safe_parts, segment_layout, slots

POLICY = "experimental-ocr-area-v1"


def ocr_regions(store, row, groups, size, config):
    """One physical region per OCR box, restricted by the existing body margin."""
    bodies = safe_parts(store, row, groups, size, config)
    allowed = np.zeros((size[1], size[0]), dtype=bool)
    for body in bodies:
        allowed |= body["mask"]
    claimed = np.zeros_like(allowed)
    regions = []
    for source in sorted(row["source_boxes"], key=lambda s: s.get("reading_order", 0)):
        x1,y1,x2,y2 = source["bbox"]
        # Conservative pixel bounds ensure no glyph extends past a float OCR box.
        bounds = [max(0,min(size[0],math.ceil(x1))), max(0,min(size[1],math.ceil(y1))),
            max(0,min(size[0],math.floor(x2))), max(0,min(size[1],math.floor(y2)))]
        rectangle = np.zeros_like(allowed)
        rectangle[bounds[1]:bounds[3],bounds[0]:bounds[2]] = True
        permitted = rectangle & allowed
        if np.any(permitted & claimed):
            raise ValueError("Overlapping usable OCR regions need review")
        claimed |= permitted
        area = int(permitted.sum())
        if not area:
            raise ValueError(f"Empty usable OCR region: {source['source_text_id']}")
        regions.append({"id": source["source_text_id"]+":ocr-zone", "mask": permitted,
            "body_mask_array": rectangle, "bbox": bounds, "original_ocr_bbox": list(source["bbox"]),
            "source_text_id": source["source_text_id"], "geometry_part_ids": source.get("part_ids", []),
            "anchor": [(x1+x2)/2,(y1+y2)/2], "reading_order": source.get("reading_order",0),
            "margin_px": config["body_margin_px"], "ocr_rectangle_area_px": int(rectangle.sum()),
            "usable_area_px": area})
        regions[-1]["allocation_area_px"] = area
        regions[-1]["allowed_mask_array"] = allowed
    total = sum(region["usable_area_px"] for region in regions)
    for region in regions:
        region["area_share"] = region["usable_area_px"]/total
    return regions


def area_allocation(text, regions):
    """Partition consecutive whole words by character quotas before font fitting."""
    words = text.split()
    if not words or not regions or any(region["usable_area_px"] <= 0 for region in regions):
        return None
    prefix = [0]
    for word in words:
        prefix.append(prefix[-1]+len(word))
    total_chars = prefix[-1]
    total_area = sum(region.get("allocation_area_px",region["usable_area_px"]) for region in regions)
    require_nonempty = len(words) >= len(regions)
    states = {0: (0.0, [])}
    for index, region in enumerate(regions):
        share = region.get("allocation_area_px",region["usable_area_px"])/total_area
        remaining = len(regions)-index-1
        next_states = {}
        for begin, (cost, plan) in states.items():
            minimum = begin+1 if require_nonempty else begin
            maximum = len(words)-remaining if require_nonempty else len(words)
            for end in range(minimum,maximum+1):
                actual = prefix[end]-prefix[begin]
                penalty = (actual/total_chars-share)**2
                proposal = (cost+penalty,plan+[{"part_id":region["id"], "word_range":[begin,end],
                    "text":" ".join(words[begin:end]), "usable_area_px":region["usable_area_px"],
                    "allocation_area_px":region.get("allocation_area_px",region["usable_area_px"]),
                    "area_share":share,"target_characters":total_chars*share,
                    "allocated_characters":actual,"character_share":actual/total_chars}])
                previous = next_states.get(end)
                if previous is None or proposal[0] < previous[0]:
                    next_states[end] = proposal
        states = next_states
    best = states.get(len(words))
    if best is None:
        return None
    return {"parts":best[1], "total_characters":total_chars, "total_usable_area_px":total_area,
        "share_squared_error":best[0], "count_policy":"NFC codepoints excluding whitespace; whole-word boundaries"}


def fit_ocr_area(text, regions, font_path, config, *, trace=None, allocation=None):
    allocation = allocation or area_allocation(text, regions)
    if allocation is None:
        return None, []
    # Allocation is fixed independently of font size; no fill-to-bubble objective.
    for size in range(config["font_max_px"],config["font_min_px"]-1,-1):
        font = ImageFont.truetype(str(font_path),size)
        plan, glyphs, failure = [], [], None
        for region, assigned in zip(regions,allocation["parts"]):
            available = slots(region["mask"],font,config["line_spacing"],region["anchor"])
            lines = segment_layout(assigned["text"].split(),available,font,region["anchor"])
            item = {**assigned,"lines":lines or [], "capacity_px":sum(s["x2"]-s["x1"] for s in available),
                "used_width_px":sum(line["width"] for line in lines or []),
                "usable_area_px":region["usable_area_px"],"status":"fitted"}
            plan.append(item)
            if lines is None:
                item["status"] = "allocated_text_does_not_fit_ocr_region"
                failure = failure or "allocated_text_does_not_fit_ocr_region"
                continue
            layer = Image.new("L",(region["mask"].shape[1],region["mask"].shape[0]))
            draw = ImageDraw.Draw(layer)
            for line in lines:
                draw.text((line["x"],line["y"]),line["text"],font=font,fill=255,anchor="lt")
            if np.any((np.asarray(layer)>0) & ~region["mask"]):
                item["status"] = "glyph_outside_ocr_region"
                failure = failure or "glyph_outside_ocr_region"
            glyphs.append(layer)
        if trace:
            trace({"font_size_px":size,"status":failure or "fitted","parts":plan,
                "allocation_policy":POLICY,"share_squared_error":allocation["share_squared_error"]})
        if failure is None:
            tokens = [token for item in plan for line in item["lines"] for token in line["text"].split()]
            if tokens != text.split():
                raise ValueError("Experimental layout lost, duplicated or reordered OCR tokens")
            return {"font_size_px":size,"parts":plan,"allocation":allocation,"policy":POLICY},glyphs
    return None, []


def rectangle_gap(a, b):
    dx = max(0,a[0]-b[2],b[0]-a[2])
    dy = max(0,a[1]-b[3],b[1]-a[3])
    return math.hypot(dx,dy)


def grow_regions(regions, offenders, *, step_px=2, gap_px=10):
    """Grow each available direction; stop at body margin or protected neighbors."""
    changes = []
    directions = ("left","right","up","down")
    for region in regions:
        if region["id"] not in offenders:
            continue
        blocked = region.setdefault("blocked_directions",{})
        if region.get("nearby_region"):
            blocked.update({direction:"nearby_ocr_region" for direction in directions})
        for direction in directions:
            if direction in blocked:
                continue
            proposed = list(region["bbox"])
            h,w = region["mask"].shape
            index,delta = {"left":(0,-step_px),"right":(2,step_px),"up":(1,-step_px),"down":(3,step_px)}[direction]
            proposed[index] = max(0,min(w if index in (0,2) else h,proposed[index]+delta))
            if proposed == region["bbox"]:
                blocked[direction] = "image_edge"
                continue
            if any(rectangle_gap(proposed,other["bbox"]) < gap_px for other in regions if other["id"] != region["id"]):
                blocked[direction] = "neighbor_gap"
                continue
            rectangle = np.zeros_like(region["mask"])
            rectangle[proposed[1]:proposed[3],proposed[0]:proposed[2]] = True
            permitted = rectangle & region["allowed_mask_array"]
            if not np.any(permitted & ~region["mask"]):
                # A blocked strip cannot be jumped to reach another lobe/panel.
                blocked[direction] = "body_margin_boundary"
                continue
            previous = list(region["bbox"])
            region.update(bbox=proposed,mask=permitted,body_mask_array=rectangle,usable_area_px=int(permitted.sum()),expanded=True)
            changes.append({"region_id":region["id"],"direction":direction,"before":previous,"after":proposed})
    return changes


def fit_with_expansion(text, regions, font_path, config, *, trace=None, step_px=2, gap_px=10):
    if step_px < 1 or gap_px < 1:
        raise ValueError("Expansion step and gap must be positive")
    regions = [{**region,"bbox":list(region["bbox"]),"blocked_directions":{}} for region in regions]
    allocation = area_allocation(text,regions)
    trials, growth = [], []

    def observer(phase, round_number):
        def record(item):
            item = {**item,"phase":phase,"expansion_round":round_number}
            trials.append(item)
            if trace:
                trace(item)
        return record

    plan,glyphs = fit_ocr_area(text,regions,font_path,config,trace=observer("original_ocr",0),allocation=allocation)
    if plan:
        return plan,glyphs,regions,growth
    for region in regions:
        region["nearby_region"] = any(rectangle_gap(region["bbox"],other["bbox"]) < gap_px
            for other in regions if other["id"] != region["id"])
    round_number = 0
    while trials:
        offenders = {item["part_id"] for item in trials[-1]["parts"] if item.get("status") != "fitted"}
        changes = grow_regions(regions,offenders,step_px=step_px,gap_px=gap_px)
        if not changes:
            break
        round_number += 1
        growth.append({"round":round_number,"changes":changes,"regions":[{
            "id":region["id"],"bbox":list(region["bbox"]),"usable_area_px":region["usable_area_px"],
            "blocked_directions":dict(region["blocked_directions"])} for region in regions]})
        minimum_config = {**config,"font_max_px":config["font_min_px"]}
        plan,glyphs = fit_ocr_area(text,regions,font_path,minimum_config,
            trace=observer("expanded_minimum_font",round_number),allocation=allocation)
        if plan:
            plan,glyphs = fit_ocr_area(text,regions,font_path,config,
                trace=observer("expanded_common_font",round_number),allocation=allocation)
            break
    return plan,glyphs,regions,growth
