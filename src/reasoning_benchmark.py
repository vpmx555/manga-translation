"""Sequential, resumable pilot jobs with development/test isolation."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from dialogue_data import document_fingerprint, read_json
from reasoning_client import OllamaReasoner, atomic_json, load_config
from reasoning_evaluation import evaluate, validate_reference
from reasoning_pipeline import load_chapter, run_reasoning


VARIANTS = {"predicted": ("predicted", True), "no_scene": ("none", True),
            "no_order_fix": ("predicted", False), "gold_scene": ("gold", True)}


def benchmark(manifest_path: Path, work_dir: Path, *, run_fn=run_reasoning, client_factory=OllamaReasoner) -> dict:
    manifest = read_json(manifest_path)
    base = manifest_path.resolve().parent
    def resolve(value):
        path = Path(value)
        return path if path.is_absolute() else base / path
    if not isinstance(manifest, dict) or not manifest.get("test") or not manifest.get("models"):
        raise ValueError("Benchmark needs test chapters and explicit model configurations")
    variants = manifest.get("variants", list(VARIANTS))
    if not variants or len(set(variants)) != len(variants) or set(variants) - VARIANTS.keys():
        raise ValueError("Choose distinct supported benchmark variants")
    models = []
    names = set()
    for model in manifest["models"]:
        name = model["name"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in names:
            raise ValueError("Model configuration names must be distinct safe filename components")
        names.add(name)
        config = load_config(model.get("profile", "cpu"), resolve(model["config"]) if model.get("config") else None,
                             model=model.get("model"))
        models.append((name, config))
    cases, seen_documents, seen_paths, seen_content = [], set(), set(), set()
    for split in ("development", "test"):
        for spec in manifest.get(split, []):
            source_path, reference_path = resolve(spec["normalized"]), resolve(spec["reference"])
            chapter = load_chapter(source_path, raw_path=resolve(spec["raw"]) if spec.get("raw") else None,
                                   bank_path=resolve(spec["bank"]) if spec.get("bank") else None,
                                   images_dir=resolve(spec["images"]) if spec.get("images") else None,
                                   text_only=manifest.get("text_only", False))
            reference = read_json(reference_path)
            validate_reference(chapter.document, reference)
            identifier = chapter.document["document_id"]
            content = document_fingerprint({"rows": [{"text": r["text"], "page": r.get("page"),
                                                      "bbox": r.get("bbox")} for r in chapter.rows]})
            if identifier in seen_documents or source_path.resolve() in seen_paths or content in seen_content:
                raise ValueError("Duplicate chapter/content across benchmark entries; development/test must be disjoint")
            seen_documents.add(identifier)
            seen_paths.add(source_path.resolve())
            seen_content.add(content)
            if "gold_scene" in variants and any(x["boundary"] is None for x in reference["boundaries"]):
                raise ValueError("Gold-scene benchmark variant requires fully reviewed boundary labels")
            cases.append((split, chapter, reference))
    run_id = hashlib.sha256(json.dumps({"manifest": manifest, "inputs": [c.source_fingerprint for _, c, _ in cases]},
                                       sort_keys=True).encode()).hexdigest()
    directory = work_dir / "benchmarks" / run_id
    summary = {"schema_version": 1, "kind": "reasoning_benchmark", "status": "running", "run_id": run_id,
               "cases": [], "notes": ["No automatic winner or production threshold is selected.",
                                       "Gold speaker/addressee labels are only used for scoring.",
                                       "Report per-chapter results and compare identical prompt/resource settings where possible."]}
    for name, config in models:
        for split, chapter, reference in cases:
            for variant in variants:
                case = {"configuration": name, "split": split, "document_id": chapter.document["document_id"],
                        "variant": variant}
                file_id = hashlib.sha256(chapter.document["document_id"].encode()).hexdigest()[:16]
                output_path = directory / name / f"{file_id}.{variant}.json"
                source, ordering = VARIANTS[variant]
                try:
                    client = client_factory(config, work_dir / "cache")
                    prediction = run_fn(chapter, client, correct_order=ordering, scene_source=source,
                                        reference=reference if source == "gold" else None,
                                        checkpoint_dir=work_dir / "checkpoints")
                    if output_path.exists():
                        previous = read_json(output_path)
                        if previous.get("source_fingerprint") != prediction["source_fingerprint"]:
                            raise ValueError("Benchmark output collides with a different source")
                    atomic_json(output_path, prediction)
                    case.update(status="complete", output=str(output_path), metrics=evaluate(chapter.document, prediction, reference))
                except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                    case.update(status="failed", error=str(exc))
                summary["cases"].append(case)
                atomic_json(directory / "summary.checkpoint.json", summary)
    summary["status"] = "complete" if all(c["status"] == "complete" for c in summary["cases"]) else "incomplete"
    atomic_json(directory / "summary.checkpoint.json", summary)
    return summary
