"""Deterministic source-order merge of independent results and naming evidence."""
import copy

from . import persons


def resolve_speaker(item, state):
    """Apply inference after analyze; source anchors remain immutable provenance."""
    if item.get('semantic_resolution') != 'resolved':
        return item
    inferred = copy.deepcopy(item.get('inferred_speaker') or {
        k: item.get(k) for k in ('speaker_person_id', 'speaker_cluster_ref', 'speaker_kind')})
    item['inferred_speaker'] = inferred
    pid, ref = inferred['speaker_person_id'], inferred['speaker_cluster_ref']
    conflicts = []
    anchor = item.get('speaker_anchor', {})
    if anchor.get('person_id') is not None and pid != anchor['person_id']:
        conflicts.append('source_anchor_disagrees')
    if anchor.get('conflicting_person_ids'):
        conflicts.append('source_anchors_disagree')
    if ref and state['cluster_to_person'].get(ref) is not None:
        if persons.canonical(state, state['cluster_to_person'][ref]) != pid:
            conflicts.append('cluster_reference_disagrees')
            ref = None
    item.update(speaker_person_id=pid, speaker_cluster_ref=ref, speaker_kind=inferred['speaker_kind'])
    item['speaker_resolution'] = {'policy': 'inference-first-v1', 'selected_source': 'inference',
                                  'source_anchor_person_id': anchor.get('person_id'),
                                  'conflicts': conflicts}
    return item


def merge(rows, completed, initial, candidates, raw):
    state = copy.deepcopy(initial)
    records = {r["utterance_id"]: r for batch in completed for r in batch}
    output, events = [], {}
    for source in rows:
        if source["id"] not in records:
            continue
        item = copy.deepcopy(records[source["id"]])
        resolve_speaker(item, initial)
        for mention in item["mentions"]:
            candidate = candidates[mention["candidate_id"]]
            form = persons.name_form(candidate["name"])
            mention.update(name_key=form["surface"], base_name=form["base_name"], honorific=form["honorific"],
                           raw_surface=candidate.get("raw_surface", candidate["name"]),
                           start=candidate["start"], end=candidate["end"])
            for field in ("original_start", "original_end", "corrected_surface", "alias_groups", "name_suspect"):
                if field in candidate:
                    mention[field] = copy.deepcopy(candidate[field])
            pid = mention.get("target_person_id")
            if mention["confirmed_person_name"] and pid is not None:
                persons.add_name(state, pid, candidate["name"], source=candidate["id"], components=mention["components"])
                ref = mention.get("recipient_cluster_ref")
                if item["content_type"] == "dialogue" and mention["mention_type"] == "direct_address" and ref:
                    observations = initial["observations"][ref]
                    event = persons.calling_event(raw=raw, row=source, mention=candidate,
                        target_person_id=pid, recipient_cluster_ref=ref,
                        caller_person_id=item["speaker_person_id"], visual_support=False,
                        evidence=mention["evidence"], recipient_observations=observations)
                    event["recipient_appearance_key"] = observations[0]["appearance_key"]
                    events[event["source_occurrence_key"]] = event
        output.append(item)
    return output, state, list(events.values())
