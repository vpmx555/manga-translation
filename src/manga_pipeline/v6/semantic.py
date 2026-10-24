"""Frozen name scanning, independent analysis dispatch, identity and export."""
import copy
import re
from pathlib import Path
from ..progress import NullProgress
from ..storage.io import atomic_json, fingerprint, read_json
from . import persons

def snapshot(store, raw):
    path = store.directory / "snapshots/persons-v6.json"
    if path.exists():
        return read_json(path)
    source_bank = Path(store.manifest["bank_path"])
    registry = persons.registry_path(store)
    before = read_json(registry) if registry.exists() else None
    state = persons.bootstrap(raw, read_json(source_bank), before,
                              run_scope=fingerprint([store.directory.name, store.manifest["source"]]))
    state["base_registry_hash"] = fingerprint(before)
    result = {"state": state, "source_bank_snapshot": persons.bank_snapshot(source_bank)}
    atomic_json(path, result)
    return result


def scan(store, document, raw, detector, progress=None, provider=None):
    if store.manifest['config'].get('v6', {}).get('scan_policy') == 'canonical-scan-v3':
        from .scan_v3 import build
        return build(store, document, raw, provider)
    state = snapshot(store, raw)["state"]
    progress = progress or NullProgress()
    rows, output, failures = document["utterances"], [], []
    scenes, llm_names, normalization = None, None, None
    if store.manifest["config"].get("ner", {}).get("backend") == "llm-scene":
        from . import scene
        from .requests import Calls
        scenes, llm_names, normalization = scene.plan(
            [r for r in rows if r.get("is_essential_text") is True], Calls(store, provider), rows,
            normalization_state=state)
        from .name_normalization import apply
        corrected = apply(document, normalization)
        corrected_by_id = {r["id"]: r for r in corrected["utterances"]}
    progress.reset(total=len(rows), unit="utterance")
    for row in rows:
        request = {"policy": "name-index-v6.1", "row": row, "name_index": state["name_index"],
                   "ner": store.manifest["config"].get("ner")}
        if llm_names is not None:
            request["llm_names"] = llm_names[row["id"]]
            request["normalization"] = normalization
        try:
            cached = store.target("scan", row["id"], request)
            if cached is None:
                spans = list(llm_names[row["id"]]) if llm_names is not None else detector.detect(row["text"])
                for name in state["name_index"]:
                    for match in re.finditer(r"(?<!\w)"+re.escape(name)+r"(?!\w)", row["text"], re.I):
                        spans.append({"name": match.group(), "start": match.start(), "end": match.end(), "source": "person_index"})
                unique = {}
                for span in spans:
                    start, end = span["start"], span["end"]
                    if not 0 <= start < end <= len(row["text"]):
                        raise ValueError("NER offsets outside source utterance")
                    suffix = re.match(r"\s*[-–—]\s*(san|kun|chan|sama|sensei|senpai|kohai|dono|tan)\b", row["text"][end:], re.I)
                    if suffix:
                        end += suffix.end()
                    surface = row["text"][start:end]
                    try:
                        form = persons.name_form(surface)
                    except ValueError:
                        continue
                    candidates = set(state["name_index"].get(form["base_name"], []))
                    candidates.update(state["name_index"].get(form["surface"], []))
                    item = {"id": fingerprint([row["id"], start, end, form["surface"]])[:20],
                        "utterance_id": row["id"], "name": form["surface"], "raw_surface": surface, "key": form["surface"],
                        "start": start, "end": end, "source": span.get("source", "ner"),
                        "candidate_person_ids": sorted({persons.canonical(state, p) for p in candidates})}
                    if normalization is not None:
                        from .name_normalization import edits, mapped_span
                        patches = edits(normalization, row["id"])
                        try:
                            mapped_start, mapped_end = mapped_span(start, end, patches)
                        except ValueError:
                            # A shorter registry match inside a corrected full name
                            # must not compete with the complete LLM occurrence.
                            continue
                        corrected_surface = corrected_by_id[row["id"]]["text"][mapped_start:mapped_end]
                        corrected_form = persons.name_form(corrected_surface)
                        item.update(name=corrected_form["surface"], key=corrected_form["surface"],
                                    original_start=start, original_end=end,
                                    start=mapped_start, end=mapped_end,
                                    corrected_surface=corrected_surface)
                        owners = {persons.canonical(state, p) for key in
                                  (corrected_form["surface"], corrected_form["base_name"])
                                  for p in state["name_index"].get(key, [])}
                        item["candidate_person_ids"] = sorted(owners)
                        item["alias_groups"] = [{k: g[k] for k in
                            ("group_id", "canonical_name", "aliases", "evidence", "status")}
                            for g in normalization["groups"] if any(
                            m["utterance_id"] == row["id"] and m["start"] == start and m["end"] == end
                            for m in g["members"])]
                        item["name_suspect"] = any(
                            p.get("utterance_id") == row["id"] and p.get("start", end) < end
                            and p.get("end", start) > start
                            for p in [*normalization["suspicions"],
                                      *[p for p in normalization["patches"] if p["status"] != "applied"]])
                        item["name_suspect"] |= any(
                            isinstance(r.get("proposal"), dict)
                            and r["proposal"].get("utterance_id") == row["id"]
                            and r["proposal"].get("start") == start
                            and r["proposal"].get("end") == end for r in normalization["rejected"])
                        item["name_suspect"] |= any(g["status"] == "suspect" for g in item["alias_groups"])
                    unique[(start, end, form["surface"])] = item
                cached = sorted(unique.values(), key=lambda x: (x["start"], x["end"]))
                store.commit_target("scan", row["id"], request, result=cached)
            output.extend(cached)
            progress.advance()
        except Exception as exc:
            store.commit_target("scan", row["id"], request, error=str(exc))
            failures.append({"utterance_id": row["id"], "error": str(exc)})
            progress.advance(failed=True)
    return {"policy": "name-index-v6.1", "candidates": output, "failures": failures,
            **({"scene_wire": read_json(store.artifact_path('scan').parent / 'scene-wire.json')}
               if (store.artifact_path('scan').parent / 'scene-wire.json').exists() else {}),
            **({"scenes": scenes, "scene_source_hash": fingerprint(document)} if scenes is not None else {}),
            **({"name_normalization": normalization} if normalization is not None else {}),
            "person_snapshot_hash": fingerprint(snapshot(store, raw))}


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def contract(target_ids, person_ids, cluster_refs, mentions_by_target):
    from .semantic_contract import contract as wire_contract
    return wire_contract(target_ids, person_ids, cluster_refs, mentions_by_target)


def analyze(store, raw, document, scanned, provider, progress=None):
    from .independent import analyze as independent_analyze
    return independent_analyze(store, raw, document, scanned, provider, progress)


def identity(store, analyzed):
    state = copy.deepcopy(analyzed["person_state"])
    for event in analyzed["calling_events"]:
        state["calling_events"][event["event_id"]] = event
    proposals = persons.propose(state, [], minimum=store.manifest["config"]["v6"]["min_confirmed_call_events"])
    state = persons.apply_links(state, proposals)
    operation = fingerprint([store.directory.name, analyzed["policy"], state])
    committed = persons.commit(persons.registry_path(store), state, operation_id=operation,
        source_bank=store.manifest["bank_path"], expected_source_snapshot=analyzed["source_bank_snapshot"])
    return {"policy": "caller-first-v6.1", "person_state": committed, "proposals": proposals,
        "operation_id": operation, "person_registry_path": str(persons.registry_path(store)),
        "source_cluster_bank_mutated": False, "source_bank_snapshot": analyzed["source_bank_snapshot"], "failures": []}


def export(document, analyzed, linked):
    result = copy.deepcopy(document)
    if analyzed.get("name_normalization") is not None:
        from .name_normalization import apply
        result = apply(document, analyzed["name_normalization"])
        result["name_normalization"] = copy.deepcopy(analyzed["name_normalization"])
    classified = {r["utterance_id"]: r for r in analyzed["utterances"]}
    state = linked["person_state"]
    retained, effects = [], []
    for row in result["utterances"]:
        item = copy.deepcopy(classified[row["id"]])
        if linked.get('name_binding') is not None:
            from .identity_binding import project
            item = project(item, linked)
        pid = item.get("speaker_person_id")
        if (pid is None or linked.get('policy') == 'identity-relationship-v2') and item.get("speaker_cluster_ref"):
            pid = state["cluster_to_person"].get(item["speaker_cluster_ref"], pid)
        pid = persons.canonical(state, pid)
        row.update(item)
        row.update(speech_style_evidence=item.get("speech_style_evidence", item["evidence"]), speech_style_source=analyzed["policy"],
                   speech_style_status="needs_review" if item["speech_style"] == "unknown" or item.get("semantic_resolution") == "unresolved" else "provisional")
        kind = item.get("speaker_kind", "person" if pid else "others")
        row.update(speaker_person_id=pid, speaker_id=pid if pid else kind if "speaker_kind" in item else None,
            speaker_display_label=(state["persons"][pid].get("primary_name") or pid) if pid else kind if "speaker_kind" in item else "Other",
            speaker_name=state["persons"][pid].get("primary_name") if pid else None,
            addressee_ids=[persons.canonical(state, p) for p in item["addressee_person_ids"]])
        if linked.get('policy') == 'identity-relationship-v2':
            for mention in row.get('mentions', []):
                if mention.get('target_person_id'):
                    mention['target_person_id'] = persons.canonical(state, mention['target_person_id'])
            for communication in row.get('communication_evidence', []):
                for key in ('from_id', 'to_id'):
                    communication[key] = persons.canonical(state, communication[key])
        if "speaker_kind" in item:
            row["voice_type"] = "narrator" if kind == "narrator" else "character" if pid else "unknown"
        if row["content_type"] in {"sfx", "action_state"}:
            effects.append(row)
        elif row["content_type"] in {"ui", "watermark"}:
            result["excluded"].append(row)
        else:
            retained.append(row)
    result.update(kind="structured_dialogue", utterances=retained, effects=effects,
        person_registry_snapshot=state, name_links=linked["proposals"], pipeline_version=6, failures=[],
        unresolved_targets=analyzed.get("unresolved_targets", []),
        semantic_validation_issues=analyzed.get("semantic_validation_issues", []),
        semantic_coverage_complete=analyzed.get("semantic_coverage_complete", True))
    if linked.get('name_binding') is not None:
        result['binding_snapshot_hash'] = linked['binding_snapshot_hash']
    for field in ("scenes", "batches", "analysis_plan_hash"):
        if field in analyzed:
            result[field] = copy.deepcopy(analyzed[field])
    return result
