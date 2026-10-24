"""Reference-limited batch contract. Rejected semantics are never repaired."""
import copy

from .semantic_contract import contract, validate_target
from ..analysis.speech import CONSERVATIVE_SHOUT_PROMPT

PROMPT = """Analyze only the supplied batch. The scene summary is background, never evidence.
No image is supplied: visual_support must be unknown. Do not invent references.
Choose only supplied person and cluster references; others is ONE unknown sentinel.
Different person IDs do not prove different people; identical names do not prove identity.
Source anchors are advisory evidence, including anchors whose metadata says locked.
Infer the speaker from this batch; post-analysis merge prefers valid inference over
the source anchor. Return your inferred person and its matching cluster, or null
when unknown. Evidence IDs must belong to this batch and should include the target
or supporting utterances whenever you infer a speaker. Anchor disagreement does
not require copying the source speaker.
Keep caption/dialogue routing independent from content type and speech style.
Confirm person names only with semantic evidence, preserving every owned mention key.
Mentions may include scan spelling corrections and proposed alias_groups. These
are advisory, not known ownership or identity constraints. Confirm/reject each
mention and infer its owner independently. Do not edit spelling or reorganize
alias groups. name_suspect flags uncertain scan evidence, not an automatic rejection.
An unconfirmed mention has empty components and null person/recipient references.
Known-name targets must match the supplied candidate ownership. A reference is not
automatically a direct address. Infer listeners independently; no automatic alternation.
Thought uses self. Caption recipients use audience unless thought uses self.
Caption continuity evidence must be within the supplied inclusive distance limit.
Consider a caption's original MAGI speaker as evidence, correcting noise with
continuity evidence within this batch; never use majority vote or copy a previous
caption. An aside does not replace the main narrator within the batch.
No narrator or thread state exists outside this batch. JSON only.
""" + CONSERVATIVE_SHOUT_PROMPT


def schema(rows, known, clusters, mentions):
    result = contract([r["id"] for r in rows], known, list(clusters),
                      {r["id"]: [k for k, m in mentions.items() if m["utterance_id"] == r["id"]] for r in rows})
    for row in rows:
        target = result["properties"]["utterances"]["properties"][row["id"]]
        target["properties"]["content_type"]["enum"].append("thought")
        target["properties"].update({
            "speaker_kind": {"type": "string", "enum": ["person", "narrator", "others"]},
            "addressee_type": {"type": "string", "enum": ["individual", "group", "self", "audience", "unknown", "not_applicable"]},
            "evidence_refs": {"type": "array", "items": {"type": "string", "enum": [r["id"] for r in rows]}}})
        target["required"] = list(target["properties"])
    # Distinct ID pairs are not semantic identity evidence in this policy.
    result["properties"]["distinct_person_pairs"]["maxItems"] = 0
    return result


def validate(item, row, job, state, caption_distance=7):
    if not isinstance(item, dict):
        raise ValueError("Missing target")
    label_reviews = []
    if item.get('content_type') == 'dialogue' or row.get('content_type') == 'dialogue':
        item = copy.deepcopy(item)
        mentions = item.get('mentions', [])
        for mention in (mentions.values() if isinstance(mentions, dict) else mentions):
            if isinstance(mention, dict) and mention.get('mention_type') == 'narrator_introduction':
                mention['mention_type'] = 'unknown'
                label_reviews.append('Dialogue cannot be narrator_introduction; name/owner retained for review')
    thought = item.get("content_type") == "thought"
    wire = {**item, "content_type": "dialogue"} if thought else item
    result = validate_target(wire, row["id"], job["mentions"], job["known"], job["clusters"], state)
    if thought:
        result["content_type"] = "thought"
    anchor = row["speaker_anchor"]
    warnings = list(label_reviews)
    if anchor['locked'] and result['speaker_person_id'] != anchor['person_id']:
        warnings.append('Inferred speaker differs from source anchor; resolve after analyze')
    pid, ref = result["speaker_person_id"], result["speaker_cluster_ref"]
    if ref and state["cluster_to_person"].get(ref) is not None:
        from .persons import canonical
        if canonical(state, state["cluster_to_person"][ref]) != pid:
            warnings.append('Inferred speaker and cluster disagree; resolve after analyze')
    if result.get("speaker_kind") not in {"person", "narrator", "others"} or (result["speaker_kind"] == "person") != (pid is not None):
        raise ValueError("Invalid speaker kind/reference")
    positions = {r["id"]: r["essential_index"] for r in job["rows"]}
    evidence = result.get("evidence_refs")
    if not isinstance(evidence, list) or any(i not in positions for i in evidence):
        raise ValueError("Evidence outside batch")
    if pid is not None and not anchor["locked"] and not evidence:
        warnings.append('Inferred speaker has textual evidence but no batch evidence IDs')
    if row["analysis_branch"] == "caption" and any(abs(positions[i]-row["essential_index"])+1 > caption_distance for i in evidence):
        raise ValueError("Caption evidence exceeds inclusive distance")
    if result.get("addressee_type") not in {"individual", "group", "self", "audience", "unknown", "not_applicable"}:
        raise ValueError("Invalid addressee type")
    self_address = thought or result["speech_style"] == "thought"
    if self_address and (result["addressee_type"] != "self" or result["addressee_person_ids"] != ([pid] if pid else [])):
        raise ValueError("Thought recipient must be self")
    if row["analysis_branch"] == "caption" and not self_address and (result["addressee_type"] != "audience" or result["addressee_person_ids"]):
        raise ValueError("Caption recipient must be audience")
    from .persons import name_form, canonical
    for mention in result["mentions"]:
        if mention["confirmed_person_name"] and not mention["evidence"].strip():
            raise ValueError("Confirmed name needs semantic evidence")
        if mention["visual_support"] != "unknown":
            raise ValueError("Text-only request cannot invent visual support")
        candidate = job["mentions"][mention["candidate_id"]]
        form = name_form(candidate["name"])
        owners = {canonical(state, p) for k in (form["surface"], form["base_name"])
                  for p in state["name_index"].get(k, [])}
        if owners and mention.get("target_person_id") is not None and mention["target_person_id"] not in owners:
            raise ValueError("Known-name ownership changed")
    result.update({k: copy.deepcopy(row[k]) for k in ("scene_id", "analysis_branch", "essential_index", "speaker_anchor", "original_speaker", "routing_reason")})
    result.update(batch_id=job["batch_id"], anchor_exception=job["anchor_exception"],
                  semantic_warnings=warnings, speaker_merge_policy='inference-first-v1',
                  semantic_resolution="resolved", semantic_response_used=True, semantic_needs_review=bool(label_reviews),
                  repair_count=0, addressee_source="default_rule" if row["analysis_branch"] == "caption" else "inferred")
    return result
