"""Versioned semantic speech style, distinct from uncertain visual geometry."""
LEGACY_POLICY = "semantic-speech-v1"
POLICY = "semantic-speech-v2-conservative-shout"
POLICIES = (LEGACY_POLICY, POLICY)
LABELS = ("normal", "shout", "whisper", "thought", "caption", "unknown")
FIELDS = ("speech_style", "speech_style_evidence")
EXPORT_FIELDS = (*FIELDS, "speech_style_source", "speech_style_status")
VISUAL_FIELDS = ("bubble_id", "bubble_bbox", "bubble_mapping", "bubble_geometry", "source_boxes")
LEGACY_PROMPT = (
    " Classify speech_style separately from content_type and speaker identity: normal, shout, "
    "whisper, thought, caption, or unknown. Use text and context; bubble_geometry and source_boxes "
    "are fallible visual evidence, not semantic labels. Do not infer emotion or loudness from "
    "punctuation or spiky shape alone. Conflicting or insufficient evidence means unknown. "
    "speech_style_evidence must be a short relevant cue (at most 120 characters); explain "
    "uncertainty for unknown. Do not change speaker/addressee facts to justify a style."
)
CONSERVATIVE_SHOUT_PROMPT = (
    " Treat shout as an exceptional raised-volume delivery, not a label for emphasis or emotion. "
    "For ordinary spoken dialogue, prefer normal unless wording, context or a supplied image "
    "clearly establishes yelling or calling loudly. A spiky bubble, shout_hint, uppercase text, "
    "exclamation marks, anger, surprise, urgency or a short interjection does not establish "
    "shouting, even when several of these weak cues occur together. Require a clear delivery "
    "cue, such as an explicit description of yelling, a response about the speaker's loudness, "
    "or unambiguous depicted shouting when an image was actually supplied. When choosing shout, "
    "the evidence must identify that delivery cue rather than merely cite punctuation or shape. "
    "If only shouting versus ordinary speaking is uncertain, choose normal; keep thought, "
    "caption and whisper when independently supported, and unknown when the delivery itself "
    "cannot be determined. Do not enforce a fixed shout quota or change identities to justify style."
)
PROMPT = LEGACY_PROMPT + CONSERVATIVE_SHOUT_PROMPT


def prompt_for(policy):
    if policy is None:
        return ""
    if policy == LEGACY_POLICY:
        return LEGACY_PROMPT
    if policy == POLICY:
        return PROMPT
    raise ValueError("Unsupported speech style policy")


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
