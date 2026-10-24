"""Artifact verification shared by v6 batches and downloaded copies."""
THRESHOLDS = {"text_detection_threshold": 0.45, "text_character_matching_threshold": 0.25,
              "text_classification_threshold": -0.5, "character_detection_threshold": 0.35,
              "character_character_matching_threshold": 0.75}


def verify_v6(store, entry, *, allow_partial=False):
    if store.manifest.get("pipeline_version") != 6:
        raise RuntimeError("Expected a pipeline v6 run")
    config = store.manifest["config"]
    from manga_pipeline.pipeline import load_config
    from manga_pipeline.v6.policy import require_config
    from manga_pipeline.storage.io import read_json, fingerprint
    load_config(store.directory / 'snapshots/config.json')
    require_config(config)
    if fingerprint(read_json(store.directory / 'snapshots/config.json')) != fingerprint(config):
        raise RuntimeError('Run configuration differs from its frozen snapshot')
    source = store.manifest["source"]
    if source["story"] != entry["story_key"] or source["chapter"] != entry["chapter"]:
        raise RuntimeError("Run belongs to another chapter")
    if len(source["images"]) != entry["pages"]:
        raise RuntimeError("Source page count mismatch")
    artifacts, incomplete = {}, []
    until = entry.get("until")
    if until not in {None, 'scan', "analyze", 'build_identity', 'relationship'}:
        raise RuntimeError("Unsupported expected stage limit")
    required_steps = store.steps[:store.steps.index(until) + 1] if until else store.steps
    with store.verification_pass():
        for step in store.steps:
            if store.manifest["steps"].get(step, {}).get("status") == "completed":
                if not store.completed(step):
                    raise RuntimeError("Invalid committed checkpoint: " + step)
                artifacts[step] = store.read(step)
            else:
                if step in required_steps:
                    incomplete.append(step)
                item = store.manifest["steps"].get(step, {})
                if item.get("artifact_hash"):
                    store._check_artifact(step, item)
        if incomplete and not allow_partial:
            raise RuntimeError("Incomplete checkpoints: " + ", ".join(incomplete))
        if until and any(step in artifacts for step in store.steps[store.steps.index(until) + 1:]):
            raise RuntimeError("Pipeline ran beyond the requested stage limit")
    raw = artifacts.get("extract")
    if raw and (raw.get("panel_source") != "koharu" or len(raw["pages"]) != entry["pages"]):
        raise RuntimeError("Koharu provenance/page count mismatch")
    analyzed = artifacts.get("analyze", {})
    unresolved = [r for r in analyzed.get("utterances", []) if r.get("semantic_resolution") == "unresolved"]
    if analyzed:
        from manga_pipeline.v6.verification import verify
        from manga_pipeline.storage.io import read_json
        frozen = read_json(store.directory / "snapshots/persons-v6.json")
        plan = read_json(store.artifact_path("analyze").parent / "plan.json")
        verify(artifacts, config, frozen, read_json(store.manifest["bank_path"]), plan)
    geometry = artifacts.get("bubble_geometry", {})
    if config.get("device") == "cuda" and any(p.get("geometry_compute_backend", {}).get("device") != "cuda" for p in geometry.get("pages", [])):
        raise RuntimeError("Geometry lacks CUDA provenance")
    exported, translated = artifacts.get("export"), artifacts.get("translate")
    count, effects = 0, 0
    if exported:
        fields = ("content_type", "speech_style", "speech_style_evidence", "speech_style_source",
                  "speech_style_status", "source_boxes", "speaker_person_id")
        rows = [*exported["utterances"], *exported.get("effects", [])]
        if analyzed.get("policy") == "independent-batches-v6.1":
            essential_metadata = {r["utterance_id"]: r for r in analyzed["utterances"]}
            for row in rows:
                original = essential_metadata[row["id"]]
                for field in ("analysis_branch", "scene_id", "essential_index", "speaker_anchor", "batch_id"):
                    if row.get(field) != original.get(field):
                        raise RuntimeError("Export changed independent-batch provenance: " + field)
        if len({row["id"] for row in rows}) != len(rows):
            raise RuntimeError("Duplicate exported utterance/effect IDs")
        if any(any(field not in row for field in fields) for row in rows):
            raise RuntimeError("Export lost v6 semantic/provenance fields")
        count, effects = len(rows), len(exported.get("effects", []))
        if translated:
            output = [*translated["utterances"], *translated.get("effects", [])]
            if {row["id"] for row in output} != {row["id"] for row in rows} or len(output) != len(rows):
                raise RuntimeError("Translation ID coverage mismatch")
            if translated.get("failures") or any(
                    not row.get("translations", {}).get(style, {}).get("translation", "").strip()
                    for row in output for style in ("natural",)):
                raise RuntimeError("Translation coverage incomplete")
    identity = artifacts.get('build_identity') or artifacts.get("identity")
    if identity and identity.get("source_cluster_bank_mutated") is not False:
        raise RuntimeError("Identity lacks the MAGI bank immutability receipt")
    if identity and identity.get('policy') == 'identity-relationship-v2':
        binding = identity.get('name_binding')
        if config.get('v6', {}).get('identity_name_binding_policy'):
            if (not binding or binding.get('policy') != config['v6']['identity_name_binding_policy']
                    or binding.get('snapshot_hash') != fingerprint({k: v for k, v in binding.items() if k != 'snapshot_hash'})
                    or identity.get('binding_snapshot_hash') != binding['snapshot_hash']):
                raise RuntimeError('Frozen name-binding snapshot changed')
            if exported and exported.get('binding_snapshot_hash') != binding['snapshot_hash']:
                raise RuntimeError('Export consumed a different name-binding snapshot')
        if fingerprint(identity['profiles']) != identity.get('profile_snapshot_hash'):
            raise RuntimeError('Frozen character profile snapshot changed')
        graph = artifacts.get('relationship')
        if graph:
            if (graph.get('policy') != 'directed-relationships-v2'
                    or graph.get('profile_snapshot_hash') != identity['profile_snapshot_hash']
                    or graph['snapshot_hash'] != fingerprint({k: v for k, v in graph.items() if k != 'snapshot_hash'})):
                raise RuntimeError('Frozen directed relationship snapshot changed')
            directions = [(e['from_id'], e['to_id']) for e in graph['address_table']]
            if len(set(directions)) != len(directions):
                raise RuntimeError('Duplicate directed address edges')
            if exported and exported.get('relationship_snapshot_hash') != graph['snapshot_hash']:
                raise RuntimeError('Export consumed a different relationship snapshot')
        from manga_pipeline.storage.io import file_hash
        for item in [identity, graph or {}]:
            for crop in item.get('reference_crops', []):
                path = (store.directory / crop['file']).resolve()
                if not path.is_relative_to(store.directory) or file_hash(path) != crop['retained_sha256']:
                    raise RuntimeError('Frozen profile/relationship crop changed')
    typeset = artifacts.get("typeset")
    if typeset:
        expected = {(p["id"], style) for p in raw["pages"] for style in ("natural",)}
        actual = [(p["page_id"], p["style"]) for p in typeset["pages"]]
        if set(actual) != expected or len(actual) != len(expected) or typeset.get("effects_image_edits"):
            raise RuntimeError("Typeset page/style coverage or effects policy mismatch")
    if not allow_partial and not (store.directory / "review.md").is_file():
        raise RuntimeError("Run review is missing")
    return {"run": store.directory.name, "pipeline_version": 6, "pages": entry["pages"],
            "utterances": count - effects, "effects": effects, "translations_per_style": count if translated else 0,
            "incomplete_steps": incomplete, "coverage_complete": not incomplete,
            "semantic_unresolved": len(unresolved), "semantic_coverage_complete": not incomplete and not unresolved,
            "semantic_validation_issues": len(analyzed.get("semantic_validation_issues", [])),
            "typeset_needs_review": len((typeset or {}).get("needs_review", [])),
            "identity_needs_review": len((identity or {}).get('needs_review', [])),
            "name_binding_summary": ((identity or {}).get('name_binding') or {}).get('summary'),
            "render_kinds": sorted({p["render_kind"] for p in (typeset or {}).get("pages", [])})}
