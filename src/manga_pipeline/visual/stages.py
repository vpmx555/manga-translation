"""Per-page checkpoints for layout and deterministic bubble geometry."""
import copy
from pathlib import Path
from time import monotonic
import numpy as np
from PIL import Image
from .geometry import read_mask, write_png
from ..storage.io import file_hash


def verify_files(root, result):
    for relative, digest in result.get("files", {}).items():
        path = (Path(root) / relative).resolve()
        if Path(relative).is_absolute() or not path.is_relative_to(Path(root).resolve()):
            raise ValueError("Visual artifact path escapes run")
        if not path.is_file() or file_hash(path) != digest:
            raise ValueError(f"Visual artifact missing/changed: {path}")


def layout(store, backend=None, progress=None):
    config = store.manifest["config"]["layout"]
    pages, failures = [], []
    sources = store.manifest["source"]["images"]
    if progress is not None:
        progress.reset(total=len(sources), unit="page")
    for index, source in enumerate(sources, 1):
        policy = "koharu-layout-v6.1" if store.manifest["pipeline_version"] == 6 else "koharu-layout-v1"
        request = {"source": source, "config": config, "policy": policy}
        key = f"page-{index}"
        started = monotonic()
        try:
            if file_hash(source["path"]) != source["sha256"]:
                raise ValueError(f"Source image changed: {source['path']}")
            cached = store.target("layout", key, request)
            if cached is not None:
                verify_files(store.directory, cached)
                pages.append(cached)
                continue
            if backend is None:
                from .koharu import Koharu
                backend = Koharu(config)
            with Image.open(source["path"]) as im:
                image = im.convert("RGB")
            detected = backend.detect(image)
            if not isinstance(detected.get("panels"), list) or not isinstance(detected.get("bubbles"), list):
                raise ValueError("Invalid Koharu layout result")
            directory = store.artifact_path("layout").parent
            page = {"page": index, "source": source, "width": image.width, "height": image.height,
                    "model_asset_sha256": getattr(backend, "asset_hashes", {}).get("model.safetensors"),
                    "panels": copy.deepcopy(detected["panels"]), "bubbles": [], "files": {}}
            page["effects_candidates"] = copy.deepcopy(detected.get("onomatopoeia", []))
            candidates = detected.get("bubble_candidates", detected["bubbles"]) if store.manifest["pipeline_version"] == 6 else detected["bubbles"]
            for number, bubble in enumerate(candidates, 1):
                mask = np.asarray(bubble["mask"], dtype=bool)
                if mask.shape != (image.height, image.width) or not mask.any():
                    raise ValueError("Invalid bubble mask")
                target = directory / "masks" / f"page-{index}-bubble-{number}.png"
                write_png(target, Image.fromarray(mask.astype(np.uint8)*255))
                rel = target.relative_to(store.directory).as_posix()
                digest = file_hash(target)
                record = {k:copy.deepcopy(v) for k,v in bubble.items() if k != "mask"}
                record.update(id=f"p{index}:b{number}", mask_path=rel, mask_sha256=digest)
                page["bubbles"].append(record)
                page["files"][rel] = digest
            from .review import layout_overlay
            overlay = directory / "overlays" / f"page-{index}.png"
            write_png(overlay, layout_overlay(image, page))
            page["files"][overlay.relative_to(store.directory).as_posix()] = file_hash(overlay)
            page["runtime_seconds"] = round(monotonic()-started, 6)
            store.commit_target("layout", key, request, result=page)
            pages.append(page)
        except Exception as exc:
            store.commit_target("layout", key, request, error=str(exc))
            failures.append({"page": index, "source": source["path"], "error": str(exc)})
        finally:
            if progress is not None:
                progress.advance()
    return {"policy": policy, "model": config["model"], "revision": config["revision"],
            "config": copy.deepcopy(config),
            "coordinate_space": "original_page_pixels", "pages": pages, "failures": failures}


def bubble_geometry(store, progress=None):
    from .bubbles import classify_bubble, match_bubble
    raw = copy.deepcopy(store.read("extract"))
    layouts = store.read("layout")["pages"]
    config = store.manifest["config"]["layout"]
    if len(layouts) != len(raw["pages"]):
        raise ValueError("Layout/extraction page counts differ")
    failures, unmatched, unknown = [], 0, 0
    if progress is not None:
        progress.reset(total=len(layouts), unit="page")
    for page, layout_page in zip(raw["pages"], layouts):
        key = f"page-{page['page']}"
        source_texts = [r for r in raw["texts"] if r["page_id"] == page["id"]]
        request = {"page": page, "layout": layout_page, "texts": source_texts,
                   "policy": "bubble-geometry-v1", "config": config}
        try:
            if page["page"] != layout_page["page"] or page["panels"] != [p["bbox"] for p in layout_page["panels"]]:
                raise ValueError("MAGI did not use the committed Koharu panels")
            if (page["width"],page["height"]) != (layout_page["width"],layout_page["height"]):
                raise ValueError("Layout/extraction dimensions differ")
            verify_files(store.directory, layout_page)
            cached = store.target("bubble_geometry", key, request)
            if cached is None:
                source = layout_page["source"]
                if file_hash(source["path"]) != source["sha256"]:
                    raise ValueError("Source image changed")
                with Image.open(source["path"]) as im:
                    pixels = np.asarray(im.convert("RGB"))
                bubbles = copy.deepcopy(layout_page["bubbles"])
                masks = [read_mask(store.directory,b,(page["width"],page["height"])) for b in bubbles]
                for bubble in bubbles:
                    bubble["geometry"] = classify_bubble(bubble.get("contour", []), pixels)
                texts = copy.deepcopy(source_texts)
                for text in texts:
                    text.update(match_bubble(text["bbox"], bubbles, masks, config))
                cached = {"bubbles": bubbles, "texts": texts}
                store.commit_target("bubble_geometry", key, request, result=cached)
            page["bubbles"] = cached["bubbles"]
            page["layout_source"] = "koharu"
            for before, after in zip(source_texts, cached["texts"]):
                before.update(after)
                unmatched += before["bubble_id"] is None
                unknown += before["bubble_geometry"]["bubble_style"] == "unknown"
        except Exception as exc:
            store.commit_target("bubble_geometry", key, request, error=str(exc))
            failures.append({"page": page["page"], "error": str(exc)})
        finally:
            if progress is not None:
                progress.advance()
    raw.update(geometry_policy="bubble-geometry-v1", failures=failures,
               geometry_statistics={"unmatched_texts": unmatched, "unknown_styles": unknown})
    return raw
