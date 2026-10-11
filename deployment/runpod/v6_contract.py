"""Artifact verification shared by v6 batches and downloaded copies."""
THRESHOLDS = {"text_detection_threshold": 0.45, "text_character_matching_threshold": 0.25,
              "text_classification_threshold": -0.5, "character_detection_threshold": 0.35,
              "character_character_matching_threshold": 0.75}


def verify_v6(store, entry, *, allow_partial=False):
    if store.manifest.get("pipeline_version") != 6:
        raise RuntimeError("Expected a pipeline v6 run")
    config = store.manifest["config"]
    if (not config.get("v6", {}).get("enabled") or not config.get("layout", {}).get("enabled")
            or config["layout"]["bubble_threshold"] != 0.45
            or any(config.get(key) != value for key, value in THRESHOLDS.items())
            or config.get("ocr", {}).get("backend") != "hayai"):
        raise RuntimeError("Run differs from the confirmed v6 configuration")
    source = store.manifest["source"]
    if source["story"] != entry["story_key"] or source["chapter"] != entry["chapter"]:
        raise RuntimeError("Run belongs to another chapter")
    if len(source["images"]) != entry["pages"]:
        raise RuntimeError("Source page count mismatch")
    artifacts, incomplete = {}, []
    with store.verification_pass():
        for step in store.steps:
            if store.manifest["steps"].get(step, {}).get("status") == "completed":
                if not store.completed(step):
                    raise RuntimeError("Invalid committed checkpoint: " + step)
                artifacts[step] = store.read(step)
            else:
                incomplete.append(step)
                item = store.manifest["steps"].get(step, {})
                if item.get("artifact_hash"):
                    store._check_artifact(step, item)
        if incomplete and not allow_partial:
            raise RuntimeError("Incomplete checkpoints: " + ", ".join(incomplete))
    raw = artifacts.get("extract")
    if raw and (raw.get("panel_source") != "koharu" or len(raw["pages"]) != entry["pages"]):
        raise RuntimeError("Koharu provenance/page count mismatch")
    exported, translated = artifacts.get("export"), artifacts.get("translate")
    count, effects = 0, 0
    if exported:
        fields = ("content_type", "speech_style", "speech_style_evidence", "speech_style_source",
                  "speech_style_status", "source_boxes", "speaker_person_id")
        rows = [*exported["utterances"], *exported.get("effects", [])]
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
                    for row in output for style in ("natural", "localized")):
                raise RuntimeError("Translation coverage incomplete")
    identity = artifacts.get("identity")
    if identity and identity.get("source_cluster_bank_mutated") is not False:
        raise RuntimeError("Identity lacks the MAGI bank immutability receipt")
    typeset = artifacts.get("typeset")
    if typeset:
        expected = {(p["id"], style) for p in raw["pages"] for style in ("natural", "localized")}
        actual = [(p["page_id"], p["style"]) for p in typeset["pages"]]
        if set(actual) != expected or len(actual) != len(expected) or typeset.get("effects_image_edits"):
            raise RuntimeError("Typeset page/style coverage or effects policy mismatch")
    if not allow_partial and not (store.directory / "review.md").is_file():
        raise RuntimeError("Run review is missing")
    return {"run": store.directory.name, "pipeline_version": 6, "pages": entry["pages"],
            "utterances": count - effects, "effects": effects, "translations_per_style": count if translated else 0,
            "incomplete_steps": incomplete, "coverage_complete": not incomplete,
            "typeset_needs_review": len((typeset or {}).get("needs_review", [])),
            "render_kinds": sorted({p["render_kind"] for p in (typeset or {}).get("pages", [])})}
