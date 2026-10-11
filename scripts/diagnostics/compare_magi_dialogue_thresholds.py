"""Rescore saved native MAGI classification outputs without extra normalization."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--thresholds", type=float, nargs="+", default=[0.5, -0.5])
    args = parser.parse_args()
    from PIL import Image, ImageDraw
    data = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    summaries = []
    for threshold in args.thresholds:
        folder = args.output / f"threshold-{threshold:+g}"
        folder.mkdir(exist_ok=True)
        report = {"model": data["model"], "revision": data["revision"],
            "source": data["source"], "threshold": threshold,
            "score_space": "Native _get_text_classification output; apply_sigmoid=False",
            "method": "Reuse scores from completed inference; no added normalization",
            "pages": []}
        count = accepted = changes = 0
        changed_pages = []
        for page in data["pages"]:
            with Image.open(page["source"]) as image:
                overlay = image.convert("RGB")
            draw = ImageDraw.Draw(overlay)
            rows = []
            for original in page["texts"]:
                score = original["score"]
                flag = score > threshold
                baseline = score > 0.5
                changed = flag != baseline
                row = dict(original, accepted=flag, changed_from_0_5=changed)
                rows.append(row)
                accepted += flag
                count += 1
                changes += changed
                color = "orange" if changed else "lime" if flag else "red"
                box = row["box"]
                draw.rectangle(box, outline=color, width=3)
                draw.text((box[0], max(0, box[1] - 12)), f"{row['index']}: {score:.3f}", fill=color)
            overlay.save(folder / f"page-{page['page']:03d}.png")
            report["pages"].append({"page": page["page"], "source": page["source"], "texts": rows})
            if any(r["changed_from_0_5"] for r in rows):
                changed_pages.append(page["page"])
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries.append({"threshold": threshold, "pages": len(data["pages"]), "texts": count,
            "accepted": accepted, "rejected": count - accepted,
            "changed_from_0_5": changes, "changed_pages": changed_pages})
    (args.output / "summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    print(json.dumps(summaries))


if __name__ == "__main__":
    main()
