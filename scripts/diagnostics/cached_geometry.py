"""Recompute geometry with current defaults, retaining verified detection/OCR."""
import copy
import shutil
from pathlib import Path

from manga_pipeline.storage.io import atomic_json, file_hash, fingerprint, read_json
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.v6.config import DEFAULT_CONFIG, validate_config
from manga_pipeline.v6.normalize import normalize
from manga_pipeline.v6.stages import geometry, write_review


def rebuild(source, directory):
    directory = Path(directory).resolve()
    layout, extract = source.read("layout"), source.read("extract")
    config = copy.deepcopy(source.manifest["config"])
    config["v6"] = {**DEFAULT_CONFIG, **config["v6"]}
    for key in ("min_body_area", "body_margin_px", "body_margin_ratio", "font_path"):
        config["v6"][key] = DEFAULT_CONFIG[key]
    validate_config(config["v6"])
    request = {"source_run": str(source.directory), "config": config,
        "layout_sha256": file_hash(source.artifact_path("layout")),
        "extract_sha256": file_hash(source.artifact_path("extract"))}
    provenance = directory / "cached-inputs.json"
    if directory.exists():
        if not provenance.is_file() or fingerprint(read_json(provenance)) != fingerprint(request):
            raise ValueError("Derived diagnostic inputs changed; use a new output directory")
        store = RunStore(directory)
    else:
        store = RunStore.create(directory, source=copy.deepcopy(source.manifest["source"]),
            config=config, bank_path=directory / "unused-diagnostic-bank" / "bank.json")
        atomic_json(provenance, request)
    for step, artifact in (("layout", layout), ("extract", extract)):
        if store.completed(step):
            continue
        store.begin(step)
        if step == "layout":
            for page in layout["pages"]:
                for relative, expected in page.get("files", {}).items():
                    target = (directory / relative).resolve()
                    original = (source.directory / relative).resolve()
                    if not target.is_relative_to(directory) or not original.is_relative_to(source.directory):
                        raise ValueError("Artifact path leaves the run directory")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, target)
                    if file_hash(target) != expected:
                        raise ValueError(f"Copied detection artifact changed: {relative}")
        store.finish(step, artifact)
    for step in ("bubble_geometry", "normalize"):
        if store.completed(step):
            continue
        store.begin(step)
        output = geometry(store) if step == "bubble_geometry" else normalize(store.read("bubble_geometry"))
        failures = len(output.get("failures", []))
        store.finish(step, output, failures=failures)
        write_review(store, step)
        if failures:
            raise ValueError(f"Derived {step} failed: {output['failures']}")
        print(f"recomputed {step} with current defaults", flush=True)
    return store
