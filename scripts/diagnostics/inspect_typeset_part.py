"""Inspect a cached body/safe mask and typeset failure without changing outputs."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))


def inspect(args):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from scipy import ndimage as ndi
    from manga_pipeline.storage.io import atomic_json, read_json
    from manga_pipeline.storage.runs import RunStore
    from manga_pipeline.v6.typeset import allocate, fit, line_width, resolve_font, safe_parts, slots
    from manga_pipeline.v6.visual import bbox, disk
    from manga_pipeline.visual.geometry import read_mask, write_png

    probe = args.probe_dir.resolve()
    inpaint = read_json(probe / "result.json")
    store = RunStore(inpaint["run_dir"])
    raw = store.read("bubble_geometry")
    layout = store.read("layout")
    page = next(page for page in raw["pages"] if page["page"] == args.page)
    plan = read_json(probe / "pages" / f"page-{args.page}" / "ocr-restored" / "typeset-plan.json")
    target = plan["plans"][args.utterance - 1]
    group = next(group for group in page["bubble_groups"] if group["id"] == target["group_id"])
    row = {"id": target["utterance_id"], "group_id": group["id"],
        "source_boxes": [text for text in raw["texts"] if text["id"] in target["source_text_ids"]]}
    source = store.manifest["source"]["images"][args.page - 1]
    with Image.open(source["path"]) as image:
        original = image.convert("RGB")
    config = plan["config"]
    parts = safe_parts(store, row, {group["id"]: group}, original.size, config)
    part = parts[args.part - 1]
    geometry_part = next(item for item in group["parts"] if item["id"] == part["id"])
    body = read_mask(store.directory, geometry_part["body_mask"], original.size)
    masks = {key: read_mask(store.directory, group[key], original.size)
        for key in ("observed_mask", "refined_mask", "inferred_mask", "bridge_mask", "tail_mask")}
    font_path = resolve_font(config)
    font = ImageFont.truetype(str(font_path), config["font_min_px"])
    available = slots(part["mask"], font, config["line_spacing"], part["anchor"])
    maximum_width = max((line["x2"] - line["x1"] for line in available), default=0)
    allocated = allocate(target["ocr_text"], parts, font, config)
    without_target, _ = fit(target["ocr_text"], [p for p in parts if p["id"] != part["id"]], font_path, config)

    def describe(mask):
        bounds = bbox(mask)
        _, components = ndi.label(mask)
        return {"area": int(mask.sum()), "bbox": bounds, "components": components,
            "width": bounds[2] - bounds[0] if bounds else 0,
            "height": bounds[3] - bounds[1] if bounds else 0}

    radius = max(2, min(16, round(math.sqrt(int(masks["refined_mask"].sum())) * config["neck_width_ratio"])))
    opened = ndi.binary_opening(masks["refined_mask"], structure=disk(radius))
    labels, count = ndi.label(opened)
    seeds = [describe(labels == label) for label in range(1, count + 1)
        if int((labels == label).sum()) >= config["min_body_area"]]
    candidates = []
    candidate_masks = []
    layout_page = next(item for item in layout["pages"] if item["page"] == args.page)
    for item in layout_page["bubbles"]:
        if item["id"] not in group["candidate_ids"]:
            continue
        mask = read_mask(store.directory, item, original.size)
        candidate_masks.append((item["id"], mask))
        candidates.append({"id": item["id"], "score": item.get("score", item.get("confidence")),
            "body_overlap_pixels": int((body & mask).sum()),
            "body_overlap_fraction": float((body & mask).sum() / max(1, body.sum()))})
    report = {"page": args.page, "source": source["path"], "utterance": target["utterance_id"],
        "part": part["id"], "source_texts": target["source_text_ids"],
        "text": target["ocr_text"], "body": describe(body), "safe": describe(part["mask"]),
        "margin_px": part["margin_px"], "observed_body_pixels": int((body & masks["observed_mask"]).sum()),
        "inferred_body_pixels": int((body & masks["inferred_mask"]).sum()),
        "ocr_assigned_to_target": [text["id"] for text in row["source_boxes"] if part["id"] in text.get("part_ids", [])],
        "anchor": part["anchor"], "opening_radius": radius, "opening_seeds": seeds,
        "candidate_evidence": candidates, "min_font_px": config["font_min_px"],
        "target_slots": len(available), "max_slot_width_px": maximum_width,
        "word_widths_at_min_font": [{"word": word, "width": round(line_width(word, font), 3)}
            for word in dict.fromkeys(target["ocr_text"].split())],
        "allocation_at_min_font": [{"part_id": item["part_id"], "word_range": item["word_range"],
            "lines": len(item["lines"]), "capacity_px": item["capacity_px"]} for item in allocated["parts"]]
            if isinstance(allocated, dict) else ([{"part_id": item["part_id"], "word_range": item["word_range"],
                "lines": len(item["lines"]), "capacity_px": item["capacity_px"]} for item in allocated] if allocated else None),
        "counterfactual_ignore_target": {"diagnostic_only": True, "fitted": without_target is not None,
            "font_size_px": without_target["font_size_px"] if without_target else None}}
    directory = probe / f"investigation-page-{args.page}-utterance-{args.utterance}-part-{args.part}"
    atomic_json(directory / "analysis.json", report)
    x1, y1, x2, y2 = group["bbox"]
    crop = (max(0, x1 - 30), max(0, y1 - 30), min(original.width, x2 + 30), min(original.height, y2 + 30))

    def overlay(title, layers, annotations=False):
        result = original.convert("RGBA")
        for mask, color in layers:
            alpha = Image.fromarray(mask.astype(np.uint8) * 100)
            layer = Image.new("RGBA", result.size, color + (0,))
            layer.putalpha(alpha)
            result = Image.alpha_composite(result, layer)
        if annotations:
            draw = ImageDraw.Draw(result)
            for text in row["source_boxes"]:
                draw.rectangle(text["bbox"], outline=(0, 100, 255), width=2)
            draw.rectangle(geometry_part["bbox"], outline=(255, 0, 0), width=2)
        image = result.convert("RGB").crop(crop)
        tile = Image.new("RGB", (image.width, image.height + 30), "white")
        tile.paste(image, (0, 30))
        ImageDraw.Draw(tile).text((5, 7), title, font=font, fill="black")
        return tile

    tiles = [overlay("Source; blue=OCR, red=part2 bbox", [], True)]
    tiles.extend(overlay("Candidate " + candidate_id, [(mask, (180, 40, 220))]) for candidate_id, mask in candidate_masks)
    tiles.append(overlay("Observed mask", [(masks["observed_mask"], (40, 150, 240))]))
    body1 = read_mask(store.directory, group["parts"][0]["body_mask"], original.size)
    tiles.append(overlay("Body1 blue; body2 red; bridge orange", [(body1, (40, 150, 240)),
        (body, (255, 0, 0)), (masks["bridge_mask"], (255, 130, 0))]))
    tiles.append(overlay("Safe masks; target part2 red", [(p["mask"], (255, 0, 0) if p["id"] == part["id"] else (40, 150, 240)) for p in parts]))
    grid = Image.new("RGB", (tiles[0].width * 3, tiles[0].height * 2), "white")
    for index, tile in enumerate(tiles[:6]):
        grid.paste(tile, ((index % 3) * tile.width, (index // 3) * tile.height))
    write_png(directory / "geometry-crop.png", grid)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    print(f"evidence: {directory}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-dir", required=True, type=Path)
    parser.add_argument("--page", type=int, default=3)
    parser.add_argument("--utterance", type=int, default=2)
    parser.add_argument("--part", type=int, default=2)
    inspect(parser.parse_args())
