"""One analyze request decides role, style, names and group speaker together."""
import base64
import copy
import io
import re
from pathlib import Path
from PIL import Image, ImageDraw
from ..progress import NullProgress
from ..storage.io import atomic_json, fingerprint, read_json
from . import persons

PROMPT = """Analyze English manga into a strict JSON contract. Each target is one complete
utterance across its bubble parts, not separate speakers. Use text/context plus fallible
visual-only hints to decide speech_style. A hint can be wrong. Do not choose a speaker
just because it has the highest MAGI association. Null means Other, not a person name.
Only reference supplied person/cluster/mention IDs. Decide sfx/action_state separately
from dialogue; Klink/Clench can be effects or real names depending on context.
Confirm a name mention only when it really refers to a person. For direct_address,
identify the recipient cluster if visible, otherwise null. A mention is not automatically
a calling event. Same-name collisions need context. Chibi, transformation, older age or
different artwork can be the same person: repeated clear calls outrank visual similarity.
Do not invent image support when no image was supplied. Report distinct_person_pairs only\nfor explicit semantic evidence of different people, never mere visual dissimilarity. Name components must come from
the supplied name span or existing known names. Preserve raw English text (including
split words); do not add an OCR repair stage. Return every target exactly once, in order.
Evidence should be concise. Do not create people from Other, suffixes or sound words.
"""


def snapshot(store, raw):
    path = store.directory / "snapshots/persons-v6.json"
    if path.exists():
        return read_json(path)
    source_bank = Path(store.manifest["bank_path"])
    registry = persons.registry_path(store)
    before = read_json(registry) if registry.exists() else None
    state = persons.bootstrap(raw, read_json(source_bank), before)
    state["base_registry_hash"] = fingerprint(before)
    result = {"state": state, "source_bank_snapshot": persons.bank_snapshot(source_bank)}
    atomic_json(path, result)
    return result


def scan(store, document, raw, detector, progress=None):
    state = snapshot(store, raw)["state"]
    progress = progress or NullProgress()
    rows, output, failures = document["utterances"], [], []
    progress.reset(total=len(rows), unit="utterance")
    for row in rows:
        request = {"policy": "name-index-v6.1", "row": row, "name_index": state["name_index"],
                   "ner": store.manifest["config"].get("ner")}
        try:
            cached = store.target("scan", row["id"], request)
            if cached is None:
                spans = detector.detect(row["text"])
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
            "person_snapshot_hash": fingerprint(snapshot(store, raw))}


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def contract(target_ids, person_ids, cluster_refs, mention_ids):
    nullable_person = {"type": ["string", "null"], "enum": [*person_ids, None]}
    nullable_cluster = {"type": ["string", "null"], "enum": [*cluster_refs, None]}
    component = object_schema({"kind": {"type": "string", "enum": ["given_name", "middle_name", "surname", "nickname", "full_name"]},
                               "value": {"type": "string"}})
    mention = object_schema({"candidate_id": {"type": "string", "enum": mention_ids or ["<no-candidates>"]},
        "confirmed_person_name": {"type": "boolean"},
        "mention_type": {"type": "string", "enum": ["direct_address", "introduction", "reference", "unknown"]},
        "target_person_id": nullable_person, "recipient_cluster_ref": nullable_cluster,
        "visual_support": {"type": "string", "enum": ["compatible", "different_appearance", "unknown"]},
        "components": {"type": "array", "items": component}, "evidence": {"type": "string"}})
    row = object_schema({"utterance_id": {"type": "string", "enum": target_ids},
        "content_type": {"type": "string", "enum": ["dialogue", "narration", "sfx", "action_state", "ui", "watermark", "unknown"]},
        "speech_style": {"type": "string", "enum": ["normal", "whisper", "shout", "thought", "caption", "unknown"]},
        "speaker_person_id": nullable_person, "speaker_cluster_ref": nullable_cluster,
        "addressee_person_ids": {"type": "array", "items": {"type": "string", "enum": person_ids or ["<no-person>"]}, "maxItems": len(person_ids)},
        "mentions": {"type": "array", "items": mention}, "evidence": {"type": "string"}})
    distinct = object_schema({"person_ids": {"type": "array", "items": {"type": "string", "enum": person_ids or ["<no-person>"]},
                                             "minItems": 2, "maxItems": 2}, "evidence": {"type": "string"}})
    # Wire keys constrain each target exactly once; normalize back to the public array.
    row = object_schema({k:v for k,v in row["properties"].items() if k != "utterance_id"})
    return object_schema({"utterances": object_schema({identity: row for identity in target_ids}),
                          "distinct_person_pairs": {"type": "array", "items": distinct}})


def context_bounds(raw, rows):
    page = next(p for p in raw["pages"] if p["id"] == rows[0]["page_id"])
    panels = page.get("panels", [])
    indices = {i for row in rows for i in row.get("panel_indices", [row.get("panel_index")])
               if isinstance(i, int) and 0 <= i < len(panels)}
    # One neighboring panel on each side preserves conversation context.
    indices |= {j for i in list(indices) for j in (i-1,i+1) if 0 <= j < len(panels)}
    boxes = [row["bbox"] for row in rows]+[panels[i] for i in indices]
    x1 = max(0,int(min(b[0] for b in boxes))-16)
    y1 = max(0,int(min(b[1] for b in boxes))-16)
    x2 = min(page["width"],int(max(b[2] for b in boxes))+16)
    y2 = min(page["height"],int(max(b[3] for b in boxes))+16)
    return [x1,y1,x2,y2]


def context_image(raw, rows, observations, max_side, bounds=None):
    """Source crop contains every target and relevant neighboring panel."""
    if not rows:
        return []
    bounds = bounds or context_bounds(raw,rows)
    page = next(p for p in raw["pages"] if p["id"] == rows[0]["page_id"])
    with Image.open(page["image_path"]) as source:
        image = source.convert("RGB").crop(tuple(bounds))
    original_size = image.size
    image.thumbnail((max_side,max_side))
    sx,sy = image.width/original_size[0],image.height/original_size[1]
    draw = ImageDraw.Draw(image)
    # Draw labels after resizing so references remain legible on the small crop.
    for ref,items in observations.items():
        for item in items:
            if item["page_id"] != page["id"]:
                continue
            x1,y1,x2,y2 = item["bbox"]
            box = ((x1-bounds[0])*sx,(y1-bounds[1])*sy,(x2-bounds[0])*sx,(y2-bounds[1])*sy)
            draw.rectangle(box,outline=(255,0,0),width=1)
            draw.text((max(0,box[0]),max(0,box[1]-10)),str(item["character_index"]),fill=(255,0,0))
    stream = io.BytesIO()
    image.save(stream,format="PNG")
    return [base64.b64encode(stream.getvalue()).decode("ascii")]


def compact_target(row, visible):
    """LLM input view only; full OCR/masks/associations remain in artifacts."""
    sources = [{"bbox":[round(v) for v in s["bbox"]],
                **({"text":s["text"]} if len(row["source_boxes"]) > 1 else {})} for s in row["source_boxes"]]
    refs = sorted({ref for candidate in row["raw_character_candidates"] for ref,observations in visible.items()
                   if any(o["source_cluster_id"] == candidate.get("speaker_cluster_id") for o in observations)})
    hints = [{k: h.get(k) for k in ("part_id","bubble_shape","border_style","style_hints","confidence","observed_boundary_fraction")}
             for h in row["visual_evidence"]]
    effects = [max((e.get("coverage",0) for e in item.get("koharu_class_1",[])),default=0)
               for item in row["role_evidence"]]
    return {"id":row["id"],"text":row["text"],"sources":sources,"raw_candidate_cluster_refs":refs,
            "visual_hints":hints,"sfx_visual_coverage":effects,"is_essential_text":row["is_essential_text"]}


def analyze(store, raw, document, scanned, provider, progress=None):
    progress = progress or NullProgress()
    state = copy.deepcopy(snapshot(store, raw)["state"])
    if scanned.get("person_snapshot_hash") != fingerprint(snapshot(store, raw)):
        raise ValueError("Person input snapshot changed after scan")
    rows, results, events, failures = document["utterances"], [], [], []
    candidates = {m["id"]: m for m in scanned["candidates"]}
    batch_size = store.manifest["config"]["v6"]["analyze_batch_size"]
    progress.reset(total=len(rows), unit="utterance")
    start = 0
    while start < len(rows):
        batch = rows[start:start+batch_size]
        # Do not mix source images inside one compact visual context.
        batch = [r for r in batch if r["page_id"] == batch[0]["page_id"]]
        ids = [r["id"] for r in batch]
        mentions = {m["id"]: m for m in candidates.values() if m["utterance_id"] in ids}
        bounds = context_bounds(raw,batch)
        def in_context(observation):
            a,b,c,d = observation["bbox"]
            return observation["page_id"] == batch[0]["page_id"] and min(c,bounds[2]) > max(a,bounds[0]) and min(d,bounds[3]) > max(b,bounds[1])
        visible = {ref:[o for o in items if in_context(o)] for ref,items in state["observations"].items()
                   if any(in_context(o) for o in items)}
        wanted = {state["cluster_to_person"][ref] for ref in visible}
        wanted.update(p for m in mentions.values() for p in m["candidate_person_ids"])
        for mention in mentions.values():
            form = persons.name_form(mention["name"])
            wanted.update(state["name_index"].get(form["base_name"], []))
            wanted.update(state["name_index"].get(form["surface"], []))
        wanted.update(r["speaker_person_id"] for r in results[-10:] if r.get("speaker_person_id"))
        person_ids = sorted({persons.canonical(state, p) for p in wanted})
        schema = contract(ids, person_ids, list(visible), list(mentions))
        source_rows = {r["id"]:r for r in rows}
        request = {"policy":"unified-analyze-v6.3","targets":[compact_target(r,visible) for r in batch],
            "mentions":[{k:m[k] for k in ("id","utterance_id","name","start","end","candidate_person_ids")} for m in mentions.values()],
            "person_candidates":{p:{"name":state["persons"][p]["primary_name"],"aliases":[f["surface"] for f in state["persons"][p]["name_forms"]]} for p in person_ids},
            "visible_clusters":{ref:[{"character_index":o["character_index"],"bbox":[round(v) for v in o["bbox"]],
                "person_id":persons.canonical(state,state["cluster_to_person"][ref])} for o in items] for ref,items in visible.items()},
            "previous_turns":[{"text":source_rows[r["utterance_id"]]["text"],"role":r["content_type"],"speaker_person_id":r["speaker_person_id"]} for r in results[-3:]],
            "next_texts":[r["text"] for r in rows[start+len(batch):start+len(batch)+2]],
            "image_bbox":bounds}
        visible_persons = {persons.canonical(state,state["cluster_to_person"][ref]) for ref in visible}
        unresolved_names = False
        for mention in mentions.values():
            form = persons.name_form(mention["name"])
            matched = {persons.canonical(state,p) for key in (form["base_name"],form["surface"])
                       for p in state["name_index"].get(key,[])}
            unresolved_names |= len(matched) != 1 or not matched.issubset(visible_persons)
        ambiguous_association = any((r["group_id"] or r["is_essential_text"]) and
            len({x.get("speaker_cluster_id") for x in r["raw_character_candidates"] if x.get("speaker_cluster_id")}) != 1 for r in batch)
        needs_image = bool(visible) and (unresolved_names or ambiguous_association)
        max_side = min(store.manifest["config"]["ollama"]["image_max_side"],
                       store.manifest["config"]["v6"].get("context_image_max_side",384))
        images = context_image(raw,batch,visible,max_side,bounds) if needs_image else []
        request["source_image_supplied"] = bool(images)
        request_hash = {"request": request, "schema": schema, "images": fingerprint(images)}
        key = f"batch:{start}:wire-v3"
        old_state = state
        try:
            def check(value):
                if not isinstance(value, dict):
                    raise ValueError("Analyze response must be an object")
                payload = value.get("utterances")
                if isinstance(payload, dict):
                    if set(payload) != set(ids):
                        raise ValueError("Analyze target coverage changed: "+str(list(payload)))
                    payload = [{**payload[identity], "utterance_id":identity} for identity in ids]
                if not isinstance(payload, list):
                    raise ValueError("Analyze response has no target collection")
                actual = [r.get("utterance_id") for r in payload]
                if len(actual) != len(ids) or set(actual) != set(ids):
                    raise ValueError("Analyze target coverage changed: "+str(actual))
                indexed = {r["utterance_id"]:r for r in payload}
                value["utterances"] = [indexed[identity] for identity in ids]
                for item in value["utterances"]:
                    if item.get("content_type") not in {"dialogue", "narration", "sfx", "action_state", "ui", "watermark", "unknown"}:
                        raise ValueError("Invalid content role")
                    if item.get("speech_style") not in {"normal", "whisper", "shout", "thought", "caption", "unknown"}:
                        raise ValueError("Invalid speech style")
                    expected_mentions = {m["id"] for m in mentions.values() if m["utterance_id"] == item["utterance_id"]}
                    actual_mentions = [m.get("candidate_id") for m in item.get("mentions", [])]
                    if len(actual_mentions) != len(set(actual_mentions)) or set(actual_mentions) != expected_mentions:
                        raise ValueError("Name candidate coverage changed")
                    for field, known in (("speaker_person_id", person_ids), ("speaker_cluster_ref", visible)):
                        if item.get(field) is not None and item[field] not in known:
                            raise ValueError("Unknown semantic reference")
                    if any(p not in person_ids for p in item.get("addressee_person_ids", [])):
                        raise ValueError("Unknown addressee")
                    for mention in item.get("mentions", []):
                        if mention.get("candidate_id") not in mentions or mentions[mention["candidate_id"]]["utterance_id"] != item["utterance_id"]:
                            raise ValueError("Mention references another target")
                        if item["content_type"] in {"sfx", "action_state", "ui", "watermark"} and mention.get("confirmed_person_name"):
                            raise ValueError("An effect/action-state cannot supply a confirmed person name")
                        if mention.get("target_person_id") is not None and mention["target_person_id"] not in person_ids:
                            raise ValueError("Unknown name target")
                        if mention.get("recipient_cluster_ref") is not None and mention["recipient_cluster_ref"] not in visible:
                            raise ValueError("Unknown calling recipient")
                        pid = mention.get("target_person_id")
                        if pid is not None and mention.get("confirmed_person_name"):
                            allowed = mentions[mention["candidate_id"]]["name"].casefold()+" "+" ".join(f["surface"] for f in state["persons"][pid]["name_forms"])
                            for component in mention.get("components", []):
                                normalized = persons.name_form(component["value"])["base_name"]
                                if component.get("kind") not in {"given_name", "middle_name", "surname", "nickname", "full_name"} or not re.search(r"(?<!\w)"+re.escape(normalized)+r"(?!\w)", allowed):
                                    raise ValueError("Unsupported name component")
                for pair in value.get("distinct_person_pairs", []):
                    if len(set(pair["person_ids"])) != 2 or any(p not in person_ids for p in pair["person_ids"]) or not pair["evidence"].strip():
                        raise ValueError("Invalid semantic contradiction")
            cached = store.target("analyze", key, request_hash)
            if cached is None:
                cached = provider.infer("analyze_dialogue_v6", PROMPT, request, schema, check, images)
            check(cached)
            batch_state = copy.deepcopy(state)
            batch_results, batch_events = [], []
            state = batch_state
            for item, source in zip(cached["utterances"], batch):
                item = copy.deepcopy(item)
                if item["content_type"] != "dialogue":
                    item.update(speaker_person_id=None, speaker_cluster_ref=None, addressee_person_ids=[])
                for mention in item["mentions"]:
                    candidate = mentions[mention["candidate_id"]]
                    form = persons.name_form(candidate["name"])
                    mention.update(name_key=form["surface"], base_name=form["base_name"], honorific=form["honorific"],
                        raw_surface=candidate.get("raw_surface", candidate["name"]), start=candidate["start"], end=candidate["end"])
                    for component in mention["components"]:
                        component["value"] = persons.name_form(component["value"])["base_name"]
                    pid = mention.get("target_person_id")
                    if mention.get("confirmed_person_name") and pid is not None:
                        allowed = candidate["name"].casefold()+" "+" ".join(f["surface"] for f in state["persons"][pid]["name_forms"])
                        if any(not re.search(r"(?<!\w)"+re.escape(c["value"].casefold())+r"(?!\w)", allowed)
                               for c in mention["components"]):
                            raise ValueError("Unsupported name component")
                        persons.add_name(state, pid, candidate["name"], source=candidate["id"], components=mention["components"])
                        if item["content_type"] == "dialogue" and mention["mention_type"] == "direct_address" and mention.get("recipient_cluster_ref"):
                            recipient = mention["recipient_cluster_ref"]
                            legacy_agrees = persons.canonical(state, state["cluster_to_person"][recipient]) == persons.canonical(state, pid)
                            comparison_visible = any(persons.canonical(state, state["cluster_to_person"][ref]) == persons.canonical(state, pid)
                                for ref in visible if ref != recipient)
                            event = persons.calling_event(raw=raw, row=source, mention=candidate,
                                target_person_id=pid, recipient_cluster_ref=recipient,
                                caller_person_id=item.get("speaker_person_id"),
                                visual_support=legacy_agrees or bool(images) and comparison_visible and mention["visual_support"] == "compatible",
                                evidence=mention["evidence"])
                            event["recipient_appearance_key"] = visible[recipient][0]["appearance_key"]
                            batch_events.append(event)
                batch_results.append(item)
            state.setdefault("cannot_links", []).extend(copy.deepcopy(cached.get("distinct_person_pairs", [])))
            store.commit_target("analyze", key, request_hash, result=cached)
            results.extend(batch_results)
            events.extend(batch_events)
            for _ in batch:
                progress.advance()
        except Exception as exc:
            if "old_state" in locals():
                state = old_state
            store.commit_target("analyze", key, request_hash, error=str(exc))
            failures.append({"target_ids": ids, "error": str(exc)})
            for _ in batch:
                progress.advance(failed=True)
        start += len(batch)
    return {"policy": "unified-analyze-v6.3", "utterances": results, "calling_events": events,
            "person_state": state, "source_bank_snapshot": snapshot(store, raw)["source_bank_snapshot"], "failures": failures}


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
    classified = {r["utterance_id"]: r for r in analyzed["utterances"]}
    state = linked["person_state"]
    retained, effects = [], []
    for row in result["utterances"]:
        item = copy.deepcopy(classified[row["id"]])
        pid = item.get("speaker_person_id")
        if pid is None and item.get("speaker_cluster_ref"):
            pid = state["cluster_to_person"].get(item["speaker_cluster_ref"], pid)
        pid = persons.canonical(state, pid)
        row.update(item)
        row.update(speech_style_evidence=item["evidence"], speech_style_source="unified-analyze-v6",
                   speech_style_status="needs_review" if item["speech_style"] == "unknown" else "provisional")
        row.update(speaker_person_id=pid, speaker_id=pid,
            speaker_display_label=(state["persons"][pid].get("primary_name") or pid) if pid else "Other",
            speaker_name=state["persons"][pid].get("primary_name") if pid else None,
            addressee_ids=[persons.canonical(state, p) for p in item["addressee_person_ids"]])
        if row["content_type"] in {"sfx", "action_state"}:
            effects.append(row)
        elif row["content_type"] in {"ui", "watermark"}:
            result["excluded"].append(row)
        else:
            retained.append(row)
    result.update(kind="structured_dialogue", utterances=retained, effects=effects,
        person_registry_snapshot=state, name_links=linked["proposals"], pipeline_version=6, failures=[])
    return result
