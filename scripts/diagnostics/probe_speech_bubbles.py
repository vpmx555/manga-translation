"""Local Koharu speech-bubble diagnostics with default geometry rules."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import sys
import traceback
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def natural(path):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", path.name)]


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stories", nargs="+", choices=["after-school", "habatake"], default=["after-school"])
    args = parser.parse_args()
    import numpy as np
    import torch
    from PIL import Image, ImageDraw, ImageFont
    from manga_pipeline.visual.config import LAYOUT_CONFIG
    from manga_pipeline.visual.koharu import Koharu
    from manga_pipeline.visual.bubbles import classify_bubble
    from manga_pipeline.storage.io import atomic_json, file_hash

    torch.set_num_threads(min(8, os.cpu_count() or 1))
    config = dict(LAYOUT_CONFIG)
    args.output.mkdir(parents=True, exist_ok=True)
    print(f"Loading Koharu; defaults={json.dumps(config)}", flush=True)
    backend = Koharu(config)
    predict = backend._predict
    def monitored_predict(image, cls, threshold):
        start = time.monotonic()
        result = predict(image, cls, threshold)
        print(f"  detection class={cls} threshold={threshold} count={len(result)} seconds={time.monotonic()-start:.1f}", flush=True)
        return result
    backend._predict = monitored_predict
    colors = {"normal": "lime", "thought_hint": "magenta", "shout_hint": "red",
              "caption_hint": "cyan", "whisper_hint": "orange", "unknown": "yellow"}
    sources = {"after-school": Path("D:/download/EN-data/After School/EN/Chapter 1"),
               "habatake": Path("D:/download/EN-data/Habatake Majutsu Sekai No Shitei Tachi!/EN/Chapter 1")}
    for story, source in sources.items():
        if story not in args.stories:
            continue
        out = args.output / story
        (out / "masks").mkdir(parents=True, exist_ok=True)
        (out / "crops").mkdir(exist_ok=True)
        paths = sorted([p for p in source.iterdir() if p.suffix.lower() in {'.png','.jpg','.jpeg','.webp','.bmp'}], key=natural)
        report = {"model": config["model"], "revision": config["revision"], "config": config,
                  "model_asset_sha256": backend.asset_hashes, "device": backend.device,
                  "source": str(source), "expected_pages": len(paths), "pages": [], "failures": []}
        if (out / "result.json").exists():
            previous = json.loads((out / "result.json").read_text(encoding="utf-8"))
            if previous["config"] != config or previous["source"] != str(source) or previous["model_asset_sha256"] != backend.asset_hashes:
                raise ValueError("Existing probe provenance differs; use a new output folder")
            for record in previous["pages"]:
                if file_hash(paths[record["page"] - 1]) != record["source_sha256"]:
                    raise ValueError("Source page changed")
                if not (out / record["overlay"]).is_file() or any(not (out / b[k]).is_file() for b in record["bubbles"] for k in ("mask", "crop")):
                    raise ValueError("Existing page artifacts incomplete")
            report["pages"] = previous["pages"]
        completed = {p["page"] for p in report["pages"]}
        for page, path in enumerate(paths, 1):
            if page in completed:
                print(f"{story} page {page}/{len(paths)} reused verified result", flush=True)
                continue
            print(f"{story} page {page}/{len(paths)} start", flush=True)
            try:
                with Image.open(path) as image:
                    rgb = image.convert("RGB")
                pixels = np.asarray(rgb)
                detected = backend.detect(rgb)
                overlay = rgb.copy()
                draw = ImageDraw.Draw(overlay)
                size = max(14, min(30, rgb.width // 65))
                try:
                    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size)
                except OSError:
                    font = ImageFont.load_default()
                records = []
                for i, bubble in enumerate(detected["bubbles"], 1):
                    geometry = classify_bubble(bubble["contour"], pixels)
                    style = geometry["bubble_style"]
                    color = colors.get(style, "yellow")
                    box = bubble["bbox"]
                    contour = [tuple(p) for p in bubble["contour"]]
                    if len(contour) > 1:
                        draw.line(contour + [contour[0]], fill=color, width=3)
                    draw.rectangle(box, outline=color, width=2)
                    label = f"B{i} {style} {bubble['confidence']:.3f}"
                    x, y = box[:2]
                    bounds = draw.textbbox((0, 0), label, font=font)
                    tw, th = bounds[2] - bounds[0], bounds[3] - bounds[1] + 5
                    x = max(0, min(x, rgb.width - tw - 4))
                    y = max(0, y - th)
                    draw.rectangle((x, y, x + tw + 4, y + th + 4), fill="black")
                    draw.text((x + 2, y), label, fill=color, font=font)
                    mask_path = f"masks/page-{page:03d}-bubble-{i:03d}.png"
                    crop_path = f"crops/page-{page:03d}-bubble-{i:03d}.png"
                    Image.fromarray(bubble["mask"].astype(np.uint8) * 255).save(out / mask_path)
                    rgb.crop(tuple(box)).save(out / crop_path)
                    records.append({"id": f"B{i}", **{k:v for k,v in bubble.items() if k != "mask"},
                                    "geometry": geometry, "mask": mask_path, "crop": crop_path})
                overlay_path = f"page-{page:03d}.png"
                overlay.save(out / overlay_path)
                report["pages"].append({"page": page, "source": str(path), "source_sha256": file_hash(path),
                                        "width": rgb.width, "height": rgb.height, "panels": detected["panels"],
                                        "bubbles": records, "overlay": overlay_path})
                print(f"{story} page {page}/{len(paths)} complete: bubbles={len(records)}", flush=True)
            except Exception as exc:
                report["failures"].append({"page": page, "source": str(path), "error": str(exc)})
                traceback.print_exc()
            atomic_json(out / "result.json", report)
        bubbles = [b for p in report["pages"] for b in p["bubbles"]]
        summary = {"expected_pages": len(paths), "pages": len(report["pages"]), "bubbles": len(bubbles),
                   "styles": dict(Counter(b["geometry"]["bubble_style"] for b in bubbles)),
                   "shapes": dict(Counter(b["geometry"]["bubble_shape"] for b in bubbles)),
                   "failures": report["failures"], "config": config}
        atomic_json(out / "summary.json", summary)
        review = ["# Speech bubble diagnostics", "", f"Koharu thresholds: bubble {config['bubble_threshold']}; panel {config['panel_threshold']}; mask 0.50.",
                  "Labels are geometry hints, not semantic speech classifications.", "", "| Page | Bubble | Detection score | Shape | Border | Style |", "|---|---|---|---|---|---|"]
        for page in report["pages"]:
            for b in page["bubbles"]:
                g = b["geometry"]
                review.append(f"| {page['page']} | {b['id']} | {b['confidence']:.6f} | {g['bubble_shape']} | {g['border_style']} | {g['bubble_style']} |")
        (out / "review.md").write_text("\n".join(review) + "\n", encoding="utf-8")
        print(f"{story} finished: {json.dumps(summary)}", flush=True)


if __name__ == "__main__":
    main()
