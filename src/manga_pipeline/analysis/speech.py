"""Versioned semantic speech style, distinct from uncertain visual geometry."""
POLICY = "semantic-speech-v1"
LABELS = ("normal", "shout", "whisper", "thought", "caption", "unknown")
FIELDS = ("speech_style", "speech_style_evidence")
EXPORT_FIELDS = (*FIELDS, "speech_style_source", "speech_style_status")
VISUAL_FIELDS = ("bubble_id", "bubble_bbox", "bubble_mapping", "bubble_geometry", "source_boxes")
PROMPT = (
    " Classify speech_style separately from content_type and speaker identity: normal, shout, "
    "whisper, thought, caption, or unknown. Use text and context; bubble_geometry and source_boxes "
    "are fallible visual evidence, not semantic labels. Do not infer emotion or loudness from "
    "punctuation or spiky shape alone. Conflicting or insufficient evidence means unknown. "
    "speech_style_evidence must be a short relevant cue (at most 120 characters); explain "
    "uncertainty for unknown. Do not change speaker/addressee facts to justify a style."
)


def check(item):
    issues = []
    if not isinstance(item.get("speech_style"), str) or item["speech_style"] not in LABELS:
        issues.append({"code": "invalid_speech_style", "fields": ["speech_style", "speech_style_evidence"]})
    evidence = item.get("speech_style_evidence")
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 120:
        issues.append({"code": "invalid_speech_style_evidence", "fields": ["speech_style_evidence"]})
    return issues


def conclude(item, source, issues=()):
    item = dict(item)
    invalid = check(item) or any(set(e.get("fields", ())) & set(FIELDS) for e in issues)
    if invalid:
        item.update(speech_style="unknown", speech_style_evidence="Unresolved after semantic repair")
    item.update(speech_style_source="fallback" if invalid else source,
                speech_style_status="needs_review" if item["speech_style"] == "unknown" else "provisional")
    return item
