"""Semantic person registry. Never write, merge or rename the MAGI bank."""
import copy
import re
import unicodedata
from pathlib import Path
from ..bank.names import known_names
from ..storage.io import atomic_json, file_hash, fingerprint, read_json
from ..storage.locking import exclusive_lock

SUFFIX = re.compile(r"^(.+?)\s*[-–—]\s*(san|kun|chan|sama|sensei|senpai|kohai|dono|tan)$", re.I)


def name_form(surface):
    value = re.sub(r"\s+", " ", unicodedata.normalize("NFC", surface)).strip().casefold()
    match = SUFFIX.fullmatch(value)
    base, suffix = (match[1].strip(), match[2]) if match else (value, None)
    if not base or base in {"other", "others", "unknown", "san", "kun", "chan"}:
        raise ValueError("Sentinel/suffix cannot become a person name")
    return {"surface": base+"-"+suffix if suffix else base, "base_name": base,
            "honorific": suffix, "kind": "address_form" if suffix else "name"}


def bank_snapshot(directory):
    root = Path(directory).resolve()
    if root.is_file():
        root = root.parent
    return {p.relative_to(root).as_posix(): file_hash(p) for p in sorted(root.rglob("*"))
            if p.is_file() and not p.name.endswith(".lock") and p.name != ".lock"}


def registry_path(store):
    # A sibling directory, never a child of the source bank being protected.
    bank = Path(store.manifest["bank_path"])
    root = bank.parent if bank.is_file() else bank
    return root.parent / "_persons_v6" / store.manifest["source"]["story"] / "registry.json"


def canonical(state, person_id):
    visited = set()
    while person_id in state.get("redirects", {}):
        if person_id in visited:
            raise ValueError("Person redirect cycle")
        visited.add(person_id)
        person_id = state["redirects"][person_id]
    if person_id is not None and person_id not in state["persons"]:
        raise ValueError("Unknown person reference")
    return person_id


def add_name(state, person_id, surface, *, source, components=()):
    person_id = canonical(state, person_id)
    form = name_form(surface)
    person = state["persons"][person_id]
    forms = person.setdefault("name_forms", [])
    existing = next((f for f in forms if f["surface"] == form["surface"]), None)
    if existing is None:
        forms.append({**form, "status": "confirmed", "source_refs": [source]})
    elif source not in existing["source_refs"]:
        existing["source_refs"].append(source)
    keys = {form["surface"], form["base_name"]}
    reviews = []
    for component in components:
        try:
            if component.get("kind") not in {"given_name", "middle_name", "surname", "nickname", "full_name"}:
                raise ValueError("Unknown name-component kind")
            value = name_form(component["value"])["base_name"]
        except (ValueError, KeyError, TypeError) as exc:
            issue = {"component": copy.deepcopy(component), "source_ref": source,
                     "status": "needs_review", "reason": str(exc)}
            reviews.append(issue)
            saved = person.setdefault("name_component_reviews", [])
            if issue not in saved:
                saved.append(issue)
            continue
        record = {"kind": component["kind"], "value": value, "source_ref": source}
        if record not in person.setdefault("components", []):
            person["components"].append(record)
        keys.add(value)
    for key in keys:
        entries = state["name_index"].setdefault(key, [])
        if person_id not in entries:
            entries.append(person_id)
    person["primary_name"] = person.get("primary_name") or form["base_name"]
    return reviews


def bootstrap(raw, source_bank, persisted=None, *, run_scope=None):
    state = copy.deepcopy(persisted) if persisted else {"schema_version": 6, "persons": {},
        "name_index": {}, "cluster_to_person": {}, "redirects": {}, "calling_events": {}, "operations": []}
    if state.get("schema_version") != 6:
        raise ValueError("Unsupported person registry schema")
    bank_contract = {key: source_bank.get(key) for key in
                     ("schema_version", "model_fingerprint", "geometry_fingerprint")}
    previous_contract = state.get("source_bank_contract")
    if previous_contract is not None and previous_contract != bank_contract:
        raise ValueError("Person registry source bank schema/model/geometry changed")
    if all(value is not None for value in bank_contract.values()):
        state["source_bank_contract"] = bank_contract
    geometry = [{k: p.get(k) for k in ("id", "characters", "character_cluster_labels")} for p in raw["pages"]]
    scope = fingerprint([raw.get("model_fingerprint"), geometry, run_scope])[:16]
    observations, legacy = copy.deepcopy(state.get("observations", {})), {}
    story = raw.get("story_id") or raw["document_id"]
    def person_for(bank_id, cluster_ref):
        key = [story, "legacy", str(bank_id)] if bank_id is not None else [story, "cluster", cluster_ref]
        return "person:"+fingerprint(key)[:20]
    for page in raw["pages"]:
        for index, (box, label) in enumerate(zip(page["characters"], page["character_cluster_labels"])):
            source_id = f"{page['id']}:c{label}"
            ref = f"{scope}:{source_id}"
            bank_id = page.get("character_ids", [None]*len(page["characters"]))[index]
            pid = person_for(bank_id, ref)
            if bank_id is not None:
                legacy[str(bank_id)] = pid
            state["persons"].setdefault(pid, {"person_id": pid, "primary_name": None,
                "name_forms": [], "components": [], "appearance_refs": [], "status": "provisional"})
            state["cluster_to_person"].setdefault(ref, pid)
            observation = {"cluster_ref": ref, "cluster_id": label, "source_cluster_id": source_id,
                           "page_id": page["id"], "character_index": index, "bbox": copy.deepcopy(box),
                           "source_bank_id": bank_id, "initial_person_id": pid,
                           "appearance_key": fingerprint([story, "source-bank", bank_id]) if bank_id is not None else ref}
            if observation not in observations.setdefault(ref, []):
                observations[ref].append(observation)
            if observation not in state["persons"][pid]["appearance_refs"]:
                state["persons"][pid]["appearance_refs"].append(observation)
    for name, bank_ids in known_names(source_bank).items():
        for bank_id in bank_ids:
            if str(bank_id) in legacy:
                try:
                    add_name(state, legacy[str(bank_id)], name, source="legacy-bank-read-only")
                except ValueError:
                    pass
    state["observations"] = observations
    state["extraction_scope"] = scope
    return state


def calling_event(*, raw, row, mention, target_person_id, recipient_cluster_ref, caller_person_id=None,
                  visual_support=False, evidence="", recipient_observations=None):
    source = next(p for p in raw["pages"] if p["id"] == row["page_id"])
    source_image_hash = file_hash(source["image_path"])
    anchors = [b.get("bbox") for b in row["source_boxes"]]
    base = name_form(mention["name"])["base_name"]
    if recipient_observations is None:
        recipient_page_id = recipient_cluster_ref.rsplit(":c", 1)[0].split(":", 1)[-1]
        local_cluster = recipient_cluster_ref.rsplit(":c", 1)[-1]
        recipient_page = next((p for p in raw["pages"] if p["id"] == recipient_page_id), None)
        if recipient_page is None:
            raise ValueError("Calling recipient page is not in the source run")
        recipient_observations = [{"page_id": recipient_page["id"], "bbox": box, "character_index": i}
            for i, (box, label) in enumerate(zip(recipient_page["characters"], recipient_page["character_cluster_labels"]))
            if str(label) == local_cluster]
    recipient_sources = []
    for observation in recipient_observations:
        page = next(p for p in raw["pages"] if p["id"] == observation["page_id"])
        recipient_sources.append({"page_id": page["id"], "image_hash": file_hash(page["image_path"]),
                                  "bbox": observation["bbox"], "character_index": observation["character_index"]})
    if not recipient_sources:
        raise ValueError("Calling recipient has no source observation")
    physical_recipient = [{k: item[k] for k in ("image_hash", "bbox")} for item in recipient_sources]
    occurrence = fingerprint([source_image_hash, anchors, mention.get("original_start", mention.get("start")),
                              mention.get("original_end", mention.get("end")),
                              base, physical_recipient, "direct_name_call"])
    return {"event_id": occurrence, "event_type": "direct_name_call", "source_occurrence_key": occurrence,
        "source_utterance_id": row["id"], "source_text_ids": row["source_text_ids"],
        "name_key": name_form(mention["name"])["surface"], "caller_person_id": caller_person_id,
        "recipient_cluster_ref": recipient_cluster_ref, "target_person_id": target_person_id,
        "recipient_observations": recipient_sources,
        "confirmation_status": "confirmed", "visual_support": bool(visual_support), "evidence": evidence}


def propose(state, events, *, minimum=2, contradictions=()):
    grouped = {}
    for event in [*state.get("calling_events", {}).values(), *events]:
        if event.get("confirmation_status") != "confirmed" or event.get("event_type") != "direct_name_call":
            continue
        cluster, pid = event.get("recipient_cluster_ref"), event.get("target_person_id")
        if not cluster or pid is None:
            continue
        pid = canonical(state, pid)
        appearance = event.get("recipient_appearance_key", cluster)
        grouped.setdefault((appearance, pid), {})[event["source_occurrence_key"]] = event
    proposals = []
    targets = {}
    for appearance, pid in grouped:
        targets.setdefault(appearance, set()).add(pid)
    for (appearance, pid), occurrences in grouped.items():
        clusters = {e["recipient_cluster_ref"] for e in occurrences.values()}
        clusters.update(ref for ref, observations in state.get("observations", {}).items()
                        if any(o.get("appearance_key") == appearance for o in observations))
        reasons = [c for c in contradictions if c.get("cluster_ref") in clusters and
                   c.get("person_id") in {None, pid}]
        conflicting = len(targets[appearance]) > 1 or bool(reasons)
        repeated = len(occurrences) >= minimum
        visual = any(e.get("visual_support") for e in occurrences.values())
        proposals.append({"cluster_ref": sorted(clusters)[0], "cluster_refs": sorted(clusters),
            "appearance_key": appearance, "person_id": pid,
            "same_person_status": "supported" if not conflicting and (repeated or visual) else "needs_review",
            "decision_basis": "repeated_confirmed_calling_support" if repeated else "single_call_with_visual_support",
            "event_refs": [e["event_id"] for e in occurrences.values()], "confirmed_event_count": len(occurrences),
            "contradictions": reasons, "ambiguous_target_persons": sorted(targets[appearance]) if conflicting else [],
            "source_cluster_bank_mutated": False})
    return proposals


def apply_links(state, proposals):
    result = copy.deepcopy(state)
    result.setdefault("cannot_links", [])
    result.setdefault("link_reviews", [])
    for proposal in proposals:
        if proposal["same_person_status"] != "supported":
            continue
        pid = canonical(result, proposal["person_id"])
        clusters = proposal.get("cluster_refs", [proposal["cluster_ref"]])
        previous = {canonical(result, result["cluster_to_person"][c]) for c in clusters if c in result["cluster_to_person"]}
        forbidden = {frozenset(canonical(result, x) for x in pair["person_ids"]) for pair in result["cannot_links"]}
        if any(old != pid and frozenset((old, pid)) in forbidden for old in previous):
            result["link_reviews"].append({**proposal, "same_person_status": "needs_review", "reason": "semantic_cannot_link"})
            continue
        for cluster in proposal.get("cluster_refs", [proposal["cluster_ref"]]):
            if cluster not in result["observations"] and cluster not in result["cluster_to_person"]:
                raise ValueError("Link refers to unknown original cluster")
            result["cluster_to_person"][cluster] = pid
            for observation in result.get("observations", {}).get(cluster, []):
                if observation not in result["persons"][pid]["appearance_refs"]:
                    result["persons"][pid]["appearance_refs"].append(observation)
        # Redirect only when all appearances of an old person have moved.
        for old in previous-{pid}:
            remaining = [c for c, target in result["cluster_to_person"].items() if canonical(result, target) == old]
            if remaining:
                continue
            result["redirects"][old] = pid
            for form in result["persons"][old].get("name_forms", []):
                for source in form.get("source_refs", ["person_redirect"]):
                    add_name(result, pid, form["surface"], source=source)
            for component in result["persons"][old].get("components", []):
                if component not in result["persons"][pid]["components"]:
                    result["persons"][pid]["components"].append(copy.deepcopy(component))
            result["persons"][old]["redirected_to"] = pid
        for name, values in result["name_index"].items():
            result["name_index"][name] = sorted({canonical(result, x) for x in values})
    return result


def commit(path, state, *, operation_id, source_bank, expected_source_snapshot):
    path = Path(path)
    with exclusive_lock(path.with_suffix(".lock")):
        before = read_json(path) if path.exists() else None
        if bank_snapshot(source_bank) != expected_source_snapshot:
            raise ValueError("Source MAGI bank changed during semantic stages")
        if before and operation_id in before.get("operations", []):
            return before
        # A parallel chapter must not silently overwrite a newer person registry.
        state = copy.deepcopy(state)
        expected = state.pop("base_registry_hash", None)
        if expected != fingerprint(before):
            raise ValueError("Person registry changed; rebase semantic analysis in a fresh run")
        result = copy.deepcopy(state)
        result.setdefault("operations", []).append(operation_id)
        journal = path.parent / "journal" / (operation_id+".json")
        atomic_json(journal, {"operation_id": operation_id, "before": before,
            "before_hash": fingerprint(before), "after_hash": fingerprint(result),
            "source_bank_snapshot": expected_source_snapshot})
        atomic_json(path, result)
        if bank_snapshot(source_bank) != expected_source_snapshot:
            raise ValueError("Source MAGI bank changed; person commit requires review")
        return result


def revert(path, operation_id):
    path = Path(path)
    with exclusive_lock(path.with_suffix(".lock")):
        current = read_json(path)
        journal = read_json(path.parent / "journal" / (operation_id+".json"))
        if fingerprint(current) != journal["after_hash"]:
            raise ValueError("Only the latest unchanged person operation can be reverted")
        before = journal["before"] or {"schema_version": 6, "persons": {}, "name_index": {},
            "cluster_to_person": {}, "redirects": {}, "calling_events": {}, "operations": []}
        atomic_json(path, before)
        return before
