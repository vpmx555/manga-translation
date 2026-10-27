"""Shared JSON object schema and explicit source-only failure records."""
import copy

def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}



def unresolved(identity, candidates, errors):
    return {"utterance_id": identity, "content_type": "unknown", "speech_style": "unknown",
            "speaker_person_id": None, "speaker_cluster_ref": None, "addressee_person_ids": [],
            "mentions": [], "evidence": "Semantic response rejected; source-only unresolved fallback",
            "semantic_resolution": "unresolved", "semantic_needs_review": True,
            "semantic_errors": errors, "source_name_candidates": copy.deepcopy(candidates),
            "repair_count": 1, "semantic_response_used": False}
