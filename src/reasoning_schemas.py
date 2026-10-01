"""Small, explicit JSON schemas shared by Ollama and local validation."""
from __future__ import annotations

import math


VERSION = "reasoning-v1"
ID = {"type": ["string", "integer", "null"]}
TEXT_OR_NULL = {"type": ["string", "null"], "maxLength": 200}
STATUS = {"type": "string", "enum": ["predicted", "unknown", "needs_review"]}
COMMON = {
    "id": {"type": "string"},
    "evidence_refs": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
    "reason": {"type": "string", "maxLength": 600},
}


def obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def rows(properties: dict) -> dict:
    return obj({"rows": {"type": "array", "items": obj({**COMMON, **properties})}})


SCHEMAS = {
    "visual": rows({"setting": TEXT_OR_NULL, "time_cue": TEXT_OR_NULL,
                    "event": TEXT_OR_NULL, "status": STATUS}),
    "boundaries": rows({"decision": {"type": "string", "enum":
                                     ["boundary", "no_boundary", "uncertain"]}}),
    "speaker": rows({"speaker_cluster_id": TEXT_OR_NULL, "speaker_id": ID, "status": STATUS}),
    "identity": rows({"speaker_id": ID, "speaker_name": TEXT_OR_NULL, "status": STATUS,
                      "name_status": STATUS}),
    "addressee": rows({"addressee_type": {"type": "string", "enum":
                      ["individual", "group", "self", "unspecified", "not_applicable"]},
                      "addressee_ids": {"type": "array", "items": {"type": ["string", "integer"]},
                                        "uniqueItems": True}, "status": STATUS}),
    "ordering": rows({"ordered_ids": {"type": "array", "items": {"type": "string"},
                                      "uniqueItems": True}, "status": STATUS}),
}


def validate(value, schema: dict, location: str = "$" ) -> None:
    """Validate the deliberately restricted schema vocabulary used above."""
    kinds = schema.get("type", [])
    kinds = [kinds] if isinstance(kinds, str) else kinds
    checks = {"null": value is None, "boolean": type(value) is bool,
              "integer": type(value) is int,
              "number": type(value) in (int, float) and math.isfinite(value),
              "string": isinstance(value, str), "array": isinstance(value, list),
              "object": isinstance(value, dict)}
    if kinds and not any(checks.get(kind, False) for kind in kinds):
        raise ValueError(f"{location}: expected {kinds}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{location}: unsupported value")
    if isinstance(value, str) and len(value) > schema.get("maxLength", len(value)):
        raise ValueError(f"{location}: string exceeds limit")
    if isinstance(value, dict):
        missing = set(schema.get("required", [])) - value.keys()
        extra = value.keys() - schema.get("properties", {}).keys()
        if missing or (extra and schema.get("additionalProperties") is False):
            raise ValueError(f"{location}: missing {sorted(missing)}, extra {sorted(extra)}")
        for key, item in value.items():
            if key in schema.get("properties", {}):
                validate(item, schema["properties"][key], f"{location}.{key}")
    if isinstance(value, list):
        if schema.get("uniqueItems"):
            import json
            if len({json.dumps(x, sort_keys=True) for x in value}) != len(value):
                raise ValueError(f"{location}: duplicate array items")
        for index, item in enumerate(value):
            validate(item, schema.get("items", {}), f"{location}[{index}]")


def id_key(value) -> str:
    """Keep integer bank IDs distinct from IDs supplied as strings."""
    import json
    if value is not None and (type(value) not in (str, int) or value == ""):
        raise ValueError("Identity must be a nonempty string, integer, or null")
    return json.dumps(value, ensure_ascii=False)


def validate_rows(result: dict, targets: list[str], allowed_refs: set[str]) -> dict[str, dict]:
    mapped = {row["id"]: row for row in result["rows"]}
    if len(mapped) != len(result["rows"]) or set(mapped) != set(targets):
        raise ValueError("Response must cover exactly the requested target IDs once")
    for row in mapped.values():
        if set(row["evidence_refs"]) - allowed_refs:
            raise ValueError(f"{row['id']}: evidence not supplied in this request")
        if (row.get("status") == "predicted" or row.get("decision") in ("boundary", "no_boundary")) and not row["evidence_refs"]:
            raise ValueError(f"{row['id']}: a prediction requires source evidence")
    return mapped
