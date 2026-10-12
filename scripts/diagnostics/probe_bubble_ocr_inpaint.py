"""Run v6 visual/OCR stages, then inpaint only OCR mapped to bubble bodies.

No scan, analyze, identity, export, translation or typesetting is executed.
The production inpaint mask planner and AOT adapter are reused directly so
no semantic labels need to be fabricated for this diagnostic.
"""
from __future__ import annotations

import argparse
import copy
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

from manga_pipeline.pipeline import Pipeline, create_run, load_config
from manga_pipeline.storage.io import atomic_json, file_hash, fingerprint, read_json
from manga_pipeline.storage.runs import RunStore


def run_inpaint_probe(store, *, output=None, device=None, plan_only=False, reused_upstream=False):
    import numpy as np
    from PIL import Image
    from manga_pipeline.visual.geometry import write_png
    from manga_pipeline.visual.inpaint import plan_masks
    from manga_pipeline.visual.stages import verify_files

    raw = store.read("bubble_geometry")
    config = copy.deepcopy(store.manifest["config"]["inpaint"])
    config["enabled"] = True
    if device is not None:
        config["device"] = device
    output = Path(output) if output is not None else store.directory / "bubble-ocr-inpaint-probe"
    output.mkdir(parents=True, exist_ok=True)
    backend = None
    pages, failures = [], []
    for original in raw["pages"]:
        started = monotonic()
        page = copy.deepcopy(original)
        page["bubbles"] = [{**part["body_mask"], "id": part["id"]}
            for group in page.get("bubble_groups", []) for part in group["parts"]]
        texts = copy.deepcopy([row for row in raw["texts"] if row["page_id"] == page["id"]])
        selected = {row["id"] for row in texts
            if row.get("mapping_status") == "matched" and row.get("part_ids")
            and row.get("text", "").strip()}
        for row in texts:
            row["bubble_id"] = row["part_ids"][0] if row.get("part_ids") else None
        source = store.manifest["source"]["images"][page["page"] - 1]
        directory = output / "pages" / f"page-{page['page']}"
        record_path = directory / "result.json"
        request = {"policy": "bubble-mapped-ocr-inpaint-probe-v1", "page": page,
            "texts": texts, "selected": sorted(selected), "source": source, "config": config,
            "plan_only": plan_only}
        request_sha256 = fingerprint(request)
        try:
            if file_hash(source["path"]) != source["sha256"]:
                raise ValueError("Source image changed")
            if record_path.is_file():
                cached = read_json(record_path)
                if cached.get("request_sha256") == request_sha256:
                    verify_files(output, cached)
                    pages.append(cached)
                    print(f"page {page['page']}: reused verified outputs", flush=True)
                    continue
            with Image.open(source["path"]) as source_image:
                image = source_image.convert("RGB")
            white, aot, decisions = plan_masks(
                image, page, texts, selected, store.directory, config)
            white_image = Image.fromarray(white.astype(np.uint8) * 255)
            aot_image = Image.fromarray(aot.astype(np.uint8) * 255)
            combined = white | aot
            erase_mask = Image.fromarray(combined.astype(np.uint8) * 255)
            erased = image.copy()
            erased.paste((255, 255, 255), (0, 0, image.width, image.height), erase_mask)
            clean = image.copy()
            clean.paste((255, 255, 255), (0, 0, image.width, image.height), white_image)
            calls = 0
            if aot.any() and not plan_only:
                if backend is None:
                    from manga_pipeline.visual.aot import AOT
                    backend = AOT(config)
                prediction = backend.predict(clean, aot_image)
                if prediction.size != image.size:
                    raise ValueError("AOT output dimensions differ from input")
                clean.paste(prediction, mask=aot_image)
                calls = 1
            changed = np.any(np.asarray(clean) != np.asarray(image), axis=2)
            outside_changed = int((changed & ~combined).sum())
            if outside_changed:
                raise ValueError(f"Modified {outside_changed} pixels outside the erase mask")
            comparison = Image.new("RGB", (image.width * 2, image.height), "white")
            comparison.paste(image, (0, 0))
            comparison.paste(clean, (image.width, 0))
            files = {}
            images = [("original.png", image), ("erased.png", erased),
                ("erase-mask.png", erase_mask), ("white-mask.png", white_image),
                ("aot-mask.png", aot_image)]
            if not plan_only:
                images.extend([("clean.png", clean), ("before-after.png", comparison)])
            for name, value in images:
                target = directory / name
                write_png(target, value)
                files[target.relative_to(output).as_posix()] = file_hash(target)
            record = {"request_sha256": request_sha256, "page_id": page["id"],
                "page": page["page"], "source": source, "files": files,
                "selected_text_ids": sorted(selected), "ocr_texts": texts,
                "bubble_groups": len(page.get("bubble_groups", [])),
                "white_pixels": int(white.sum()), "aot_pixels": int(aot.sum()),
                "outside_mask_changed_pixels": outside_changed, "aot_calls": calls,
                "model_asset_sha256": getattr(backend, "asset_hashes", {}).get("model.safetensors") if calls else None,
                "decisions": decisions, "runtime_seconds": round(monotonic() - started, 6)}
            atomic_json(record_path, record)
            pages.append(record)
            print(f"page {page['page']}: OCR={len(texts)}, selected={len(selected)}, "
                f"white={record['white_pixels']}, AOT={record['aot_pixels']}", flush=True)
        except Exception as exc:
            failures.append({"page": page["page"], "error": str(exc)})
            print(f"page {page['page']}: failed: {exc}", flush=True)
    result = {"policy": "bubble-mapped-ocr-inpaint-probe-v1",
        "status": "failed" if failures else ("planned" if plan_only else "completed"),
        "run_dir": str(store.directory), "pipeline_status": "partial_by_design",
        "output_dir": str(output), "plan_only": plan_only,
        "executed_pipeline_steps": [] if reused_upstream else ["layout", "extract", "bubble_geometry", "normalize"],
        "upstream_source": "verified_cached_artifacts" if reused_upstream else "executed_in_this_probe",
        "recomputed_steps": ["bubble_geometry", "normalize"] if (store.directory / "cached-inputs.json").is_file() else [],
        "selection_policy": "nonempty OCR matched to a bubble body; no semantic classification",
        "config": config, "expected_pages": len(raw["pages"]), "completed_pages": len(pages),
        "selected_texts": sum(len(page["selected_text_ids"]) for page in pages),
        "pages": pages, "failures": failures}
    atomic_json(output / "result.json", result)
    review = ["# Bubble / OCR / inpaint probe", "",
        f"Status: **{result['status']}**; pages: {len(pages)}/{len(raw['pages'])}.", "",
        "Only nonempty OCR matched to bubble bodies is selected. No semantic labels are inferred.",
        "Scan, analyze, identity, export, translate and typeset were not executed by this probe.",
        f"Upstream source: {result['upstream_source']}.",
        "The run manifest remains partial by design; diagnostic completion is recorded in result.json.", "",
        "Each page retains the original, erased preview and masks; completed inpaint adds clean and comparison images.",
        "In before-after.png, the original is on the left and the inpaint result on the right.", "",
        "| Page | Groups | OCR | Selected | White pixels | AOT pixels | Outside-mask edits |",
        "| --- | --- | --- | --- | --- | --- | --- |"]
    for page in pages:
        review.append(f"| {page['page']} | {page['bubble_groups']} | {len(page['ocr_texts'])} | "
            f"{len(page['selected_text_ids'])} | {page['white_pixels']} | {page['aot_pixels']} | "
            f"{page['outside_mask_changed_pixels']} |")
    if failures:
        review.extend(["", "## Failures", ""])
        review.extend(f"- Page {item['page']}: {item['error']}" for item in failures)
    (output / "review.md").write_text("\n".join(review) + "\n", encoding="utf-8")
    print(f"result: {output / 'result.json'}", flush=True)
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--run-dir", type=Path, help="Resume a run made by this probe")
    source.add_argument("--source-run", type=Path, help="Read verified cached stages without resuming the source run")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--story", default="after-school-we-do")
    parser.add_argument("--chapter", default="chapter-001")
    parser.add_argument("--output-root", type=Path, default=PROJECT / "outputs" / "diagnostics")
    parser.add_argument("--probe-output", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--plan-only", action="store_true", help="Write erase masks without executing AOT")
    parser.add_argument("--recorded-config", action="store_true", help="Keep old cached geometry/settings for a baseline comparison")
    args = parser.parse_args()
    if args.source_run:
        if args.config:
            parser.error("--source-run uses its recorded configuration; omit --config")
        store = RunStore(args.source_run)
        if store.manifest["pipeline_version"] != 6:
            parser.error("--source-run requires a v6 run")
        for step in ("layout", "extract"):
            if not store.completed(step):
                raise ValueError(f"Cached prerequisite is incomplete: {step}")
        import torch
        if args.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable; choose --device cpu for cached inpaint only")
        if args.device == "cpu":
            torch.set_num_threads(min(8, torch.get_num_threads()))
        output = args.probe_output or args.output_root / "after-school-bubble-ocr-inpaint" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if not args.recorded_config:
            from cached_geometry import rebuild
            store = rebuild(store, output / "geometry-run")
        print(f"source_run: {store.directory}; detection/OCR reused, not rerun", flush=True)
        return run_inpaint_probe(store, output=output, device=args.device,
            plan_only=args.plan_only, reused_upstream=True)
    if args.device != "cuda":
        parser.error("Fresh visual/OCR runs require CUDA; CPU is supported only with --source-run")
    if args.run_dir:
        if args.config:
            parser.error("Resume uses the recorded config; omit --config")
        store = RunStore(args.run_dir)
    else:
        config = load_config(args.config, device="cuda")
        config["v6"]["enabled"] = True
        config["layout"].update(enabled=True, device="cuda", bubble_threshold=0.45)
        config["inpaint"].update(enabled=True, device="cuda")
        config["translation"]["enabled"] = False
        config["visualizations"] = True
        store = create_run(args.input, args.story, args.chapter, args.output_root,
            args.output_root / "probe-banks", config)
    if store.manifest["pipeline_version"] != 6 or store.manifest["config"]["device"] != "cuda":
        parser.error("This diagnostic requires a v6 CUDA run; CPU fallback is disabled")
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; this diagnostic will not run on CPU")
    print(f"run_dir: {store.directory}", flush=True)
    if not Pipeline(store).run(until="normalize"):
        raise RuntimeError("Visual/OCR stages did not complete; inpaint was not started")
    return run_inpaint_probe(RunStore(store.directory), output=args.probe_output,
        device=args.device, plan_only=args.plan_only)


if __name__ == "__main__":
    raise SystemExit(main())
