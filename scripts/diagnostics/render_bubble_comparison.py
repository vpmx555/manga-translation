"""Render equal-scale bubble contour comparison: current left, AIP419 right."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def render(probe, reference, output):
    from PIL import Image, ImageDraw, ImageFont
    from manga_pipeline.storage.io import atomic_json, file_hash
    current = json.loads((probe / "result.json").read_text(encoding="utf-8"))
    aip = json.loads((reference / "detector-comparison.json").read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    comparisons = []
    previews = []
    for page in sorted(aip["pages"], key=lambda p:p["page"]):
        source = next(p for p in current["pages"] if p["page"] == page["page"])
        if file_hash(source["source"]) != source["source_sha256"]:
            raise ValueError("Source image changed")
        if page.get("source_sha256", source["source_sha256"]) != source["source_sha256"]:
            raise ValueError("Reference source differs")
        image = Image.open(source["source"]).convert("RGB")
        w, h = image.size
        header, gutter = max(80, w // 14), 16
        canvas = Image.new("RGB", (w * 2 + gutter, h + header), "#151a22")
        font_size = max(18, w // 48)
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", font_size)
        small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", max(14, w // 70))
        for side, records in enumerate([source["bubbles"], page["bubbles"]]):
            overlay = image.copy()
            draw = ImageDraw.Draw(overlay)
            for number, bubble in enumerate(records, 1):
                points = [tuple(p) for p in bubble.get("contour", []) or []]
                if len(points) > 1:
                    draw.line(points + [points[0]], fill="#00a6ff", width=max(3,w//400))
                box = bubble["bbox"]
                label = f"B{number} {bubble['confidence']:.3f}"
                bounds = draw.textbbox((0,0), label, font=small)
                tw, th = bounds[2]-bounds[0]+6, bounds[3]-bounds[1]+10
                x, y = max(0,min(box[0],w-tw)), max(0,box[1]-th)
                draw.rectangle((x,y,x+tw,y+th),fill="#111111")
                draw.text((x+3,y+1),label,font=small,fill="#00d5ff")
            x = side * (w + gutter)
            canvas.paste(overlay,(x,header))
            title = "CURRENT / HIEN TAI" if side == 0 else "AIP419"
            title_draw = ImageDraw.Draw(canvas)
            title_draw.text((x+20,12),f"{title} - Page {page['page']} - {len(records)} bubbles",font=font,fill="white")
            title_draw.text((x+20,12+font_size+10),"bubble=0.25 | panel=0.50 | same pinned Koharu weights",font=small,fill="#adbccd")
        name = f"page-{page['page']:03d}-current-left-aip-right.png"
        canvas.save(output / name)
        preview = canvas.copy()
        preview.thumbnail((720,1100))
        previews.append((page["page"],preview))
        comparisons.append({"page": page["page"], "current_bubbles": len(source["bubbles"]),
                            "aip419_bubbles": len(page["bubbles"]), "file": name,
                            "width": canvas.width, "height": canvas.height})
    if previews:
        columns, cell_w, cell_h = 3, 740, max(p.height for _,p in previews)+50
        sheet = Image.new("RGB",(columns*cell_w,((len(previews)+columns-1)//columns)*cell_h),"#151a22")
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf",24)
        for i,(number,preview) in enumerate(previews):
            x,y=(i%columns)*cell_w,(i//columns)*cell_h
            draw.text((x+10,y+8),f"Page {number}: current left / AIP right",font=font,fill="white")
            sheet.paste(preview,(x+10,y+42))
        sheet.save(output / "overview.jpg",quality=90)
    atomic_json(output / "summary.json",{"pages":len(comparisons),"expected_pages":len(current["pages"]),
                "current_bubbles":sum(p["current_bubbles"] for p in comparisons),
                "aip419_bubbles":sum(p["aip419_bubbles"] for p in comparisons),
                "left":"current","right":"AIP419","threshold":0.25,"comparisons":comparisons})
    index = ["# After School chapter 1: contour comparison", "",
             "Left: current pipeline. Right: AIP419. Both use bubble 0.25, panel 0.50, and the same pinned Koharu checkpoint.",
             "Images preserve original page scale; labels are local bubble IDs and detection scores. IDs are not correspondence matches.",
             "", "[Overview](overview.jpg)", "",
             "| Page | Current bubbles | AIP419 bubbles | Full-resolution comparison |", "|---|---:|---:|---|"]
    for page in comparisons:
        index.append(f"| {page['page']} | {page['current_bubbles']} | {page['aip419_bubbles']} | [Open PNG]({page['file']}) |")
    (output / "README.md").write_text("\n".join(index)+"\n",encoding="utf-8")
    print(f"Rendered {len(comparisons)} comparisons: {output}",flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--probe",type=Path,required=True)
    parser.add_argument("--reference",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    render(args.probe,args.reference,args.output)
