"""Two independent styles; the model writes translations, never identities."""
import re
from ..names.stages import object_schema

POLICY = "vi-dual-v1"
STYLES = ("natural", "localized")
PROMPT = (
    "Translate every supplied target from English to Vietnamese exactly once. JSON only. "
    "The exported speaker/addressee IDs are authoritative, including unknown/special values. "
    "Do not reinterpret identities, invent relationships, ages, genders or enduring character profiles. "
    "Use the source context and confirmed glossary. Preserve proper names and Japanese honorifics "
    "such as -san/-kun/-chan. Keep plot facts, numbers, meaning, intended emotion and who addresses whom. "
    "If identities conflict with wording or pronouns cannot be resolved, flag needs_review with a short reason. "
    "For unclear OCR you may propose a plausible translation, but MUST flag needs_review and explain "
    "the uncertainty; do not present a guess as established fact. Preserve meaningful short reactions. "
    "Glossary proposals are suggestions only; do not infer new character facts. "
    "When previous_results/validation_errors are supplied, repair only rejected targets. "
)
STYLE_PROMPTS = {
    "natural": "Use natural Vietnamese, faithful to meaning and the character's tone; avoid adding slang or emphasis.",
    "localized": "Use lively Vietnamese dialogue, flexible colloquial phrasing and appropriate slang. "
                 "Do not add jokes, plot details, insults or stronger emotion absent from the source.",
}


def prompt(style, instructions=""):
    return PROMPT + STYLE_PROMPTS[style] + (" User instructions: " + instructions if instructions else "")


def schema(ids):
    branches = [object_schema({"id": {"enum": [identity]}, "translation": {"type": "string"},
        "needs_review": {"type": "boolean"}, "review_reason": {"type": "string"},
        "glossary_proposals": {"type": "array", "maxItems": 3, "items": object_schema({
            "source": {"type": "string"}, "vi": {"type": "string"}})}}) for identity in ids]
    return object_schema({"translations": {"type": "array", "minItems": len(ids), "maxItems": len(ids),
                                         "items": {"anyOf": branches}}})


def readable(value):
    if not isinstance(value, dict) or not isinstance(value.get("translations"), list):
        raise ValueError("Expected translations array")


def issues(item, target, glossary):
    errors = []
    if not isinstance(item.get("translation"), str) or not item["translation"].strip():
        return ["empty_or_invalid_translation"]
    if type(item.get("needs_review")) is not bool or not isinstance(item.get("review_reason"), str):
        errors.append("invalid_review_fields")
    elif item["needs_review"] and not item["review_reason"].strip():
        errors.append("missing_review_reason")
    proposals = item.get("glossary_proposals")
    if (not isinstance(proposals, list) or len(proposals) > 3 or any(not isinstance(x, dict)
            or not isinstance(x.get("source"), str) or not isinstance(x.get("vi"), str) for x in proposals)):
        errors.append("invalid_glossary_proposals")
    for suffix in re.findall(r"-(?:san|kun|chan)\b", target["text"], re.I):
        if suffix.casefold() not in item["translation"].casefold():
            errors.append("missing_honorific:" + suffix)
    for entry in glossary:
        if re.search(r"(?<!\w)" + re.escape(entry["source"]) + r"(?!\w)", target["text"], re.I):
            if entry["vi"].casefold() not in item["translation"].casefold():
                errors.append("missing_glossary_term:" + entry["source"])
    return errors
