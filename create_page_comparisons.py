"""Join original and translated pages from a downloaded v6 batch. Requires Pillow."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

from PIL import Image, ImageDraw


DEFAULT_BATCH = Path(__file__).resolve().parent / "outputs/runpod-v6-nine-stories-idfix-20261013"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", nargs="?", type=Path, default=DEFAULT_BATCH)
    args = parser.parse_args()
    batch = args.batch.resolve()
    plans = list(batch.glob("durable/*/ingress/plan.json"))
    if len(plans) != 1:
        raise ValueError(f"Expected one ingress/plan.json, found {len(plans)}")
    chapters = load_json(plans[0])["chapters"]
    tasks = []
    # Validate every pair before creating output; manifest order defines page numbers.
    for chapter in chapters:
        story = chapter.get("story_key", chapter["story"])
        chapter_name = chapter["chapter"]
        chapter_dir = batch / "live/outputs" / story / chapter_name
        runs = sorted(p for p in chapter_dir.iterdir() if (p / "11_typeset/result.json").is_file())
        if not runs:
            raise FileNotFoundError(f"No typeset run in {chapter_dir}")
        run = runs[-1]
        manifest = load_json(run / "manifest.json")
        if manifest.get("status") != "completed":
            raise ValueError(f"Run is not completed: {run}")
        sources = manifest["source"]["images"]
        if len(sources) != chapter["pages"]:
            raise ValueError(f"Page count mismatch: {run}")
        for number, source in enumerate(sources, 1):
            name = PurePosixPath(source["path"].replace("\\", "/")).name
            original = Path(chapter["source_folder"]) / name
            if not original.is_file():
                raise FileNotFoundError(original)
            if hashlib.sha256(original.read_bytes()).hexdigest() != source["sha256"]:
                raise ValueError(f"Original differs from pipeline input: {original}")
            for style in ("natural", "localized"):
                translated = run / "11_typeset/pages" / f"page-{number}" / style / "rendered.png"
                if not translated.is_file():
                    raise FileNotFoundError(translated)
                destination = chapter_dir.parent / "comparisons" / chapter_name / run.name / style / f"page-{number:03d}.png"
                tasks.append((original, translated, destination, number, style))
    records = []
    counts = {}
    for original, translated, destination, number, style in tasks:
        with Image.open(original) as source_image, Image.open(translated) as translated_image:
            left = source_image.convert("RGB")
            right = translated_image.convert("RGB")
            header, gap = 36, 12
            canvas = Image.new("RGB", (left.width + gap + right.width, max(left.height, right.height) + header), "white")
            draw = ImageDraw.Draw(canvas)
            draw.text((12, 10), f"ORIGINAL | page {number}", fill="black")
            draw.text((left.width + gap + 12, 10), f"{style.upper()} | page {number}", fill="black")
            draw.rectangle((left.width, header, left.width + gap - 1, canvas.height), fill="#dddddd")
            canvas.paste(left, (0, header))
            canvas.paste(right, (left.width + gap, header))
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".tmp")
            canvas.save(temporary, format="PNG", compress_level=3)
            temporary.replace(destination)
        records.append({"original": str(original), "translated": str(translated), "comparison": str(destination)})
        key = str(destination.parents[2])
        counts[key] = counts.get(key, 0) + 1
        if len(records) % 50 == 0:
            print(f"Created {len(records)}/{len(tasks)} comparisons", flush=True)
    report = batch / "comparison-report.json"
    report.write_text(json.dumps({"count": len(records), "chapters": counts, "pairs": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    for folder, count in counts.items():
        print(f"{count} images: {folder}", flush=True)
    print(f"Done: {len(records)} comparisons. Report: {report}", flush=True)


if __name__ == "__main__":
    main()
