"""V6 orchestration: visual evidence and text mapping have separate checkpoints."""
import copy
import re
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from ..storage.io import atomic_bytes, atomic_json, file_hash, fingerprint, read_json
from ..visual.geometry import read_mask, write_png
from ..visual.stages import verify_files
from . import normalize, semantic, visual
from .geometry_backend import GeometryBackend


def geometry(store, progress=None):
    raw = copy.deepcopy(store.read("extract"))
    layouts = store.read("layout")["pages"]
    cfg = store.manifest["config"]["v6"]
    if len(layouts) != len(raw["pages"]):
        raise ValueError("Layout/extraction page counts differ")
    failures = []
    backend = GeometryBackend(store.manifest["config"].get("device") or "cpu")
    if progress:
        progress.reset(total=len(layouts), unit="page")
    for page, layout in zip(raw["pages"], layouts):
        source = layout["source"]
        key = "visual:"+page["id"]
        # No OCR strings, OCR boxes or dialogue scores enter this checkpoint.
        request = {"policy": "visual-groups-v6.2", "compute_backend": backend.receipt,
            "image_sha256": source["sha256"],
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
                groups, files = visual.build_groups(store.directory, directory, page, layout["bubbles"], masks, pixels, cfg,
                                                    backend=backend)
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
                cached = {"groups": groups, "files": files, "compute_backend": backend.receipt}
                store.commit_target("bubble_geometry", key, request, result=cached)
            verify_files(store.directory, cached)
            page["bubble_groups"], page["files"] = cached["groups"], cached["files"]
            page["geometry_compute_backend"] = cached["compute_backend"]
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
        bank = read_json(store.manifest["bank_path"])
        return normalize.normalize(store.read("bubble_geometry"), store.manifest["config"]["v6"], bank)
    if step == "scan":
        try:
            joint = store.manifest["config"]["ner"]["backend"] == "llm-scene"
            return semantic.scan(store, store.read("normalize"), store.read("extract"),
                                 None if joint else pipeline.detector(), pipeline.progress,
                                 provider=pipeline.provider() if joint else None)
        finally:
            if pipeline._detector is not None and hasattr(pipeline._detector, "close"):
                pipeline._detector.close()
                pipeline._detector = None
    if step == "analyze":
        return semantic.analyze(store, store.read("extract"), store.read("normalize"), store.read("scan"), pipeline.provider(), pipeline.progress)
    if step == "identity":
        return semantic.identity(store, store.read("analyze"))
    if step == 'build_identity':
        from .identity_v2 import build
        return build(store, pipeline.provider())
    if step == 'relationship':
        from .relationship_v2 import build
        return build(store, pipeline.provider())
    if step == "export":
        identity_step = 'build_identity' if 'build_identity' in store.steps else 'identity'
        result = semantic.export(store.read("normalize"), store.read("analyze"), store.read(identity_step))
        if identity_step == 'build_identity':
            graph = store.read('relationship')
            result['profile_snapshot_hash'] = store.read(identity_step)['profile_snapshot_hash']
            result['relationship_snapshot_hash'] = graph['snapshot_hash']
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
        if name == "translate" and not data and (path.parent / "partial.json").exists():
            data = read_json(path.parent / "partial.json")
        if name == "translate" and not data and (path.parent / "relationships.json").exists():
            data = {"relationship_graph": read_json(path.parent / "relationships.json")}
        lines = [f"# Pipeline v6 — {name}", "", f"Status: `{item.get('status', 'pending')}`", "",
            f"Policy: `{data.get('policy', data.get('geometry_policy', 'pipeline-v6.1'))}`", "",
            f"Pages: {len(data.get('pages', []))}; utterances: {len(data.get('utterances', []))}; effects: {len(data.get('effects', []))}.", "",
            "[Machine-readable artifact](./"+path.name+")", ""]
        for failure in data.get("failures", []):
            lines.append("- Failure: "+str(failure))
        for failure in data.get('inference_failures', []):
            lines.append('- **Inference failed / no retry**: ' + str(failure))
        for pid, profile in data.get('profiles', {}).items():
            state = data.get('person_state', {})
            label = state.get('persons', {}).get(pid, {}).get('primary_name') or pid
            mids = data.get('eligible_identities', {}).get(pid, {}).get('magi_ids', [])
            lines.extend([f"## Profile MAGI {'/'.join(mids)} · {label}", '', f"Version: `{profile['version']}`", '',
                          str(profile.get('attributes', {})), ''])
        if name == 'relationship':
            from .relationship_v2 import mermaid
            lines.extend(['## Directed address graph', '', mermaid(data), ''])
            for edge in data.get('address_table', []):
                identities = data.get('identities', {})
                source = identities.get(edge['from_id'], {})
                target = identities.get(edge['to_id'], {})
                lines.append(f"- MAGI {'/'.join(source.get('magi_ids', []))} → MAGI {'/'.join(target.get('magi_ids', []))}: "
                             f"{edge['self']} / {edge['address']}; {edge['status']}; {edge['basis']}; {edge['reason']}; {edge['evidence_refs']}")
            for relation in data.get('social_relations', []):
                lines.append('- Social relation: ' + str(relation))
        for crop in data.get('reference_crops', []):
            import os
            link = Path(os.path.relpath(store.directory / crop['file'], path.parent)).as_posix()
            label = (f"Panel {crop['page_id']}:{crop['panel_index']}" if crop.get('kind') == 'panel'
                     else f"MAGI {crop['magi_id']} crop d={crop['distance']:.6f}")
            lines.append(f"- {label}: ![evidence]({link})")
        for issue in data.get("ocr_review", []):
            lines.append("- **OCR needs review**: "+str(issue))
        binding = data.get('name_binding')
        if binding:
            lines.extend(['## Name ownership bootstrap', '', str(binding['summary']), '',
                          'Review status: ' + binding['review_status'], ''])
            for claim in binding['accepted']:
                occurrence = claim['occurrence']
                lines.append(f"- {occurrence['surface']} → MAGI {claim['owner_magi_id']}: "
                             f"{claim['evidence_kind']}; {claim['reason']}; {claim['evidence_refs']}")
            for issue in binding['needs_review']:
                lines.append('- **Name binding needs review**: ' + str(issue))
        graph = data.get("relationship_graph") or {}
        for node in graph.get("view", {}).get("nodes", []):
            lines.append(f"- Relationship node MAGI {node['magi_id']}: identity `{node['person_id']}`")
        for edge in graph.get("view", {}).get("edges", []):
            lines.append("- Relationship edge: "+str(edge))
        for item in graph.get("address_table", []):
            if 'from_id' in item:
                identities = graph.get('identities', {})
                source = '/'.join(identities.get(item['from_id'], {}).get('magi_ids', []))
                target = '/'.join(identities.get(item['to_id'], {}).get('magi_ids', []))
                lines.append(f"- Address MAGI {source} → MAGI {target}: "
                             f"{item['self']} / {item['address']}; {item['status']}; "
                             f"basis={item['basis']}; refs={item['evidence_refs']}; {item['reason']}")
            else:
                lines.append(f"- Address `{item['a']}` → `{item['b']}`: {item['a_to_b']}; "
                             f"reverse={item['b_to_a']}; basis={item['basis']}; refs={item['evidence_refs']}; {item['reason']}")
        for crop in graph.get("reference_crops", []):
            import os
            link = Path(os.path.relpath(store.directory / crop["file"], path.parent)).as_posix()
            lines.append(f"- Crop MAGI {crop['magi_id']}: distance={crop['distance']:.6f}; "
                         f"source={crop['path']}; sha256={crop['sha256']}; ![reference crop]({link})")
        for issue in [*graph.get("crop_issues", []), *graph.get("failures", [])]:
            lines.append("- **Relationship needs review**: "+str(issue))
        for scene in data.get("scenes", []):
            lines.append(f"- Scene `{scene['scene_id']}`: {scene['summary']}")
        normalization = data.get("name_normalization") or {}
        for patch in normalization.get("patches", []):
            if normalization.get('policy') == 'canonical-scan-v3':
                lines.append(f"- OCR `{patch['utterance_id']}`: {patch['before']!r} → {patch['after']!r}; "
                             f"rule={patch['rule_index']}; matches={patch['matches']}")
                continue
            lines.append(f"- Name patch `{patch['utterance_id']}` [{patch['start']}:{patch['end']}]: "
                         f"{patch['original']!r} → {patch['replacement']!r}; **{patch['status']}**; "
                         f"{patch['evidence']}; refs={patch['evidence_refs']}")
        for group in normalization.get("groups", []):
            lines.append(f"- Alias group `{group['group_id']}`: {group['canonical_name']!r}; "
                         f"{group['status']}; members={group['members']}; {group['evidence']}")
        for suspicion in normalization.get("suspicions", []):
            lines.append(f"- **Name suspect**: {suspicion}")
        for warning in normalization.get('warnings', []):
            lines.append(f"- **Scan needs review**: {warning}")
        for rejected in normalization.get("rejected", []):
            lines.append(f"- **Name proposal rejected**: {rejected}")
        for candidate in data.get("candidates", []):
            if candidate.get("name_suspect"):
                lines.append(f"- **Name suspect** `{candidate['utterance_id']}`: {candidate['raw_surface']!r}")
        for batch in data.get("batches", []):
            lines.append(f"- Batch `{batch['batch_id']}`: scene `{batch['request']['scene_id']}`, "
                         f"{batch['request']['analysis_branch']}; targets {[r['id'] for r in batch['rows']]}; "
                         f"cluster pool {list(batch['clusters'])}; anchor exception: {batch['anchor_exception']}")
            for row in batch["rows"]:
                lines.append(f"  - Anchor `{row['id']}`: {row['speaker_anchor']}")
        if data.get("unresolved_targets"):
            lines.append("- Unresolved (source only): " + str(data["unresolved_targets"]))
        for issue in data.get("semantic_validation_issues", []):
            lines.append("- Semantic validation: " + str(issue))
        for row in data.get("utterances", []):
            lines.extend([f"- `{row.get('id', row.get('utterance_id'))}`: {row.get('text', '')}",
                "  "+str(row.get("translations", row.get("speech_style", "")))])
            for mention in row.get("mentions", []):
                lines.append(f"  - Name {mention.get('raw_surface', '')!r} → {mention.get('name_key', '')!r}: "
                             f"confirmed={mention.get('confirmed_person_name')}; "
                             f"person={mention.get('target_person_id')}; "
                             f"suspect={mention.get('name_suspect', False)}; {mention.get('evidence', '')}")
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
        rendered = '\n'.join(lines) + '\n'
        if name in {'build_identity', 'relationship'}:
            from .identity_v2 import membership
            state = data.get('person_state', {})
            identities = data.get('identities', {})
            labels = {pid: 'MAGI ' + '/'.join(item.get('magi_ids', [])) for pid, item in identities.items()}
            if state:
                members, _ = membership(state)
                labels.update({pid: 'MAGI ' + '/'.join(sorted(mids)) for pid, mids in members.items()})
            for pid in sorted(labels, key=len, reverse=True):
                rendered = re.sub(r'(?<!\w)' + re.escape(pid) + r'(?!\w)', lambda m: labels[pid], rendered)
        atomic_bytes(path.parent / "review.md", rendered.encode("utf-8"))
    overview = ["# Pipeline v6 run", "", "Original MAGI cluster bank and semantic person registry are separate.", ""]
    for name in store.steps:
        overview.append(f"- [{name}]({store.directories[name]}/review.md): {store.manifest['steps'].get(name, {}).get('status', 'pending')}")
    atomic_bytes(store.directory / "review.md", ("\n".join(overview)+"\n").encode("utf-8"))
