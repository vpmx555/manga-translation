"""V6 orchestration: visual evidence and text mapping have separate checkpoints."""
import copy
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from ..storage.io import atomic_bytes, atomic_json, file_hash, fingerprint, read_json
from ..visual.geometry import read_mask, write_png
from ..visual.stages import verify_files
from . import normalize, semantic, visual


def geometry(store, progress=None):
    raw = copy.deepcopy(store.read("extract"))
    layouts = store.read("layout")["pages"]
    cfg = store.manifest["config"]["v6"]
    if len(layouts) != len(raw["pages"]):
        raise ValueError("Layout/extraction page counts differ")
    failures = []
    if progress:
        progress.reset(total=len(layouts), unit="page")
    for page, layout in zip(raw["pages"], layouts):
        source = layout["source"]
        key = "visual:"+page["id"]
        # No OCR strings, OCR boxes or dialogue scores enter this checkpoint.
        request = {"policy": "visual-groups-v6.1", "image_sha256": source["sha256"],
            "candidates": layout["bubbles"], "panels": page["panels"],
            "tail_anchors": page.get("tails", []), "config": cfg}
        try:
            if file_hash(source["path"]) != source["sha256"]:
                raise ValueError("Source image changed")
            cached = store.target("bubble_geometry", key, request)
            if cached is None:
                with Image.open(source["path"]) as image:
                    pixels = np.asarray(image.convert("RGB"))
                masks = [read_mask(store.directory, b, (page["width"], page["height"])) for b in layout["bubbles"]]
                directory = store.artifact_path("bubble_geometry").parent / "masks" / page["id"].replace(":", "-")
                groups, files = visual.build_groups(store.directory, directory, page, layout["bubbles"], masks, pixels, cfg)
                overlay = Image.fromarray(pixels)
                draw = ImageDraw.Draw(overlay)
                for group in groups:
                    for part in group["parts"]:
                        for ring in part["body_rings"]:
                            draw.line([tuple(p) for p in ring["points"]], fill=(0, 160, 255), width=2)
                        draw.text(tuple(part["bbox"][:2]), part["id"].split(":")[-1], fill=(255, 0, 0))
                path = directory.parent.parent / "overlays" / (page["id"].replace(":", "-")+".png")
                write_png(path, overlay)
                files[path.relative_to(store.directory).as_posix()] = file_hash(path)
                cached = {"groups": groups, "files": files}
                store.commit_target("bubble_geometry", key, request, result=cached)
            verify_files(store.directory, cached)
            page["bubble_groups"], page["files"] = cached["groups"], cached["files"]
            masks = {}
            for group in page["bubble_groups"]:
                masks[group["id"]] = read_mask(store.directory, group["refined_mask"], (page["width"], page["height"]))
                for part in group["parts"]:
                    masks[part["id"]] = read_mask(store.directory, part["body_mask"], (page["width"], page["height"]))
            texts = [t for t in raw["texts"] if t["page_id"] == page["id"]]
            # M5a is independently keyed by positional inputs; M5b never sees them.
            mapping_request = {"policy": "position-mapping-v6.1", "groups": fingerprint(cached),
                "boxes": [{"id": t["id"], "bbox": t["bbox"]} for t in texts],
                "coverage": store.manifest["config"]["layout"]["mapping_coverage"],
                "margin": store.manifest["config"]["layout"]["mapping_margin"],
                "effects_candidates": layout.get("effects_candidates", [])}
            mapping_key = "mapping:"+page["id"]+":"+fingerprint(mapping_request)
            mapped = store.target("bubble_geometry", mapping_key, mapping_request)
            if mapped is None:
                mapped = {}
                for text in texts:
                    item = visual.map_text(text["bbox"], page["bubble_groups"], masks,
                        coverage=mapping_request["coverage"], margin=mapping_request["margin"])
                    x1, y1, x2, y2 = text["bbox"]
                    area = max(1, (x2-x1)*(y2-y1))
                    effects = []
                    for effect in layout.get("effects_candidates", []):
                        a, b, c, d = effect["bbox"]
                        overlap = max(0, min(x2,c)-max(x1,a))*max(0, min(y2,d)-max(y1,b))/area
                        if overlap >= .25:
                            effects.append({"bbox": effect["bbox"], "score": effect["confidence"], "coverage": overlap})
                    item["role_evidence"] = {"koharu_class_1": effects, "final_role": False}
                    mapped[text["id"]] = item
                store.commit_target("bubble_geometry", mapping_key, mapping_request, result=mapped)
            for text in texts:
                text.update(copy.deepcopy(mapped[text["id"]]))
        except Exception as exc:
            store.commit_target("bubble_geometry", key, request, error=str(exc))
            failures.append({"page_id": page["id"], "error": str(exc)})
        finally:
            if progress:
                progress.advance()
    raw.update(pipeline_version=6, geometry_policy="group-geometry-v6.1", failures=failures)
    return raw


def dispatch(pipeline, step):
    store = pipeline.store
    if step == "bubble_geometry":
        return geometry(store, pipeline.progress)
    if step == "normalize":
        return normalize.normalize(store.read("bubble_geometry"))
    if step == "scan":
        try:
            return semantic.scan(store, store.read("normalize"), store.read("extract"), pipeline.detector(), pipeline.progress)
        finally:
            if pipeline._detector is not None and hasattr(pipeline._detector, "close"):
                pipeline._detector.close()
                pipeline._detector = None
    if step == "analyze":
        return semantic.analyze(store, store.read("extract"), store.read("normalize"), store.read("scan"), pipeline.provider(), pipeline.progress)
    if step == "identity":
        return semantic.identity(store, store.read("analyze"))
    if step == "export":
        result = semantic.export(store.read("normalize"), store.read("analyze"), store.read("identity"))
        directory = store.artifact_path(step).parent
        atomic_json(directory / "dialogue.json", result)
        atomic_json(directory / "effects.json", {"effects": result["effects"]})
        return result
    if step == "translate":
        from .translation import translate
        return translate(store, pipeline.translation_provider or pipeline.provider(), pipeline.progress)
    if step == "typeset":
        from .typeset import typeset
        return typeset(store, pipeline.translation_provider or pipeline.provider(), pipeline.progress)
    raise ValueError("Unsupported v6 step: "+step)


def write_review(store, step=None):
    """Review generation is automatic and never a stage approval gate."""
    steps = [step] if step else list(store.steps)
    for name in steps:
        item = store.manifest["steps"].get(name, {})
        path = store.artifact_path(name)
        data = read_json(path) if path.exists() else {}
        lines = [f"# Pipeline v6 — {name}", "", f"Status: `{item.get('status', 'pending')}`", "",
            f"Policy: `{data.get('policy', data.get('geometry_policy', 'pipeline-v6.1'))}`", "",
            f"Pages: {len(data.get('pages', []))}; utterances: {len(data.get('utterances', []))}; effects: {len(data.get('effects', []))}.", "",
            "[Machine-readable artifact](./"+path.name+")", ""]
        for failure in data.get("failures", []):
            lines.append("- Failure: "+str(failure))
        for row in data.get("utterances", []):
            lines.extend([f"- `{row.get('id', row.get('utterance_id'))}`: {row.get('text', '')}",
                "  "+str(row.get("translations", row.get("speech_style", "")))])
        for effect in data.get("effects", []):
            lines.append(f"- Effect `{effect.get('id')}`: {effect.get('text', '')} → {effect.get('translations', {})}")
        if data.get("source_bank_snapshot") is not None:
            lines.extend(["", "Source MAGI bank hashes are retained in the artifact; semantic person operations do not write that bank.", ""])
        for proposal in data.get("proposals", []):
            lines.append(f"- Person link `{proposal['person_id']}` ← `{proposal.get('cluster_refs', [proposal['cluster_ref']])}`: "
                f"{proposal['same_person_status']}; {proposal['decision_basis']}; {proposal['confirmed_event_count']} confirmed occurrences.")
        for event in data.get("calling_events", []):
            lines.append(f"- Calling evidence `{event['event_id']}`: `{event['name_key']}` → `{event['target_person_id']}` "
                         f"at `{event['source_utterance_id']}`; visual support: {event['visual_support']}.")
        for issue in data.get("needs_review", []):
            lines.append("- Needs review: "+str(issue))
        for page in data.get("pages", []):
            if page.get("render_kind"):
                lines.append(f"- Page {page['page']} / {page['style']}: `{page['render_kind']}`.")
        for page in data.get("pages", []):
            for relative in page.get("files", {}):
                if "/overlays/" in relative or relative.endswith("rendered.png"):
                    import os
                    link = Path(os.path.relpath(store.directory / relative, path.parent)).as_posix()
                    lines.extend(["", f"[Page {page.get('page', '')} visualization]({link})"])
        atomic_bytes(path.parent / "review.md", ("\n".join(lines)+"\n").encode("utf-8"))
    overview = ["# Pipeline v6 run", "", "Original MAGI cluster bank and semantic person registry are separate.", ""]
    for name in store.steps:
        overview.append(f"- [{name}]({store.directories[name]}/review.md): {store.manifest['steps'].get(name, {}).get('status', 'pending')}")
    atomic_bytes(store.directory / "review.md", ("\n".join(overview)+"\n").encode("utf-8"))
