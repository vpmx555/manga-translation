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
