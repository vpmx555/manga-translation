"""Small schema: one branch per target, no semantic combination grammar."""
from ..names.stages import object_schema
from ..policies import DEFAULT_SPEAKER_POLICY

POLICY = "essential-dialogue-v3"
CONTENT = {"dialogue", "narration", "thought", "unknown"}
ADDRESS = {"single", "group", "audience", "self", "unknown"}
ROLES = {"introduction", "direct_address", "reference", "unknown"}
SPEAKERS = ("others", "narrator", "groups")
LISTENERS = ("public_audience", "self", "unknown")

PROMPT = (
    "Analyze every target in source order, using only its context_ids, supplied stable IDs and prior memory. "
    "Return every target/name candidate exactly once. Source MAGI speaker IDs are fallible hints, not proof. "
    "No scenes, reading-order changes, images or reasoning output. "
    "Choose the PRIMARY function: narration reports a story or presents/describes people or events; "
    "it can be a caption or an actual character's presentation. Not every informative sentence is narration. "
    "Dialogue primarily interacts: greeting, calling, asking, answering, requesting or responding. "
    "Thought is internal speech, directed to self; unknown if the function is unclear. "
    "Content type does not determine speaker or recipients. A spoken presentation can have a character "
    "speaker and single/group/audience recipients. An unvoiced identity caption normally has narrator; "
    "the MAGI-associated character may be its introduced subject, not its teller. "
    "speaker_id: supplied stable ID when supported, narrator for an external narrative voice, "
    "others for an unidentified speaker. others is not one persistent person across turns. "
    "groups means multiple people jointly utter this target, such as a chorus or simultaneous response; "
    "it does not require enumerating their IDs and is not one persistent group across turns. "
    "Use groups only with evidence of joint speech; plural wording or a group of listeners alone is insufficient. "
    "A single person speaking for a group still has that person's ID, or others if unidentified. "
    "groups is a speaker label only, never a listener ID or name target ID. "
    "addressee_type: single=one person, group=multiple story people, audience=readers/viewers, "
    "self=oneself, unknown=unclear recipient scope. A clear collective address is group or audience, "
    "never single merely because its members are unidentified. Candidate IDs are possibilities, not recipients. "
    "Group enumeration is optional: keep only members with evidence, even one known ID, or [unknown]. "
    "Do not add IDs or unknown just to reach a minimum group size. Do not inherit a recipient type blindly "
    "from memory; re-evaluate this target's meaning. Use turn-taking, names and confirmed prior links. "
    "audience uses [public_audience], unknown uses [unknown], self uses [speaker_id] if stable or [self]. "
    "single uses one other stable ID or [unknown]. Never invent a stable ID. "
    "For each scanned candidate choose introduction (presenting who someone is, including self-introduction "
    "and identity captions), direct_address (calling that person as recipient), reference (discussing them), "
    "or unknown (non-person/unclear). Do not add unscanned names. "
    "A direct_address to a single known recipient MUST conclude name_target_id equal that recipient ID. "
    "For a group, a known direct name target must be one of its known recipients. A uniquely mapped name "
    "cannot be reassigned. Do not infer aliases from shared surnames or spelling similarity. "
    "The person introduced and speaker are separate. Introduction identity hints may be null; "
    "a separate panel linker confirms the subject. New reference names are not identity assignments. "
    "If previous_results and validation_errors are supplied, repair only fields listed as editable. "
    "Preserve all other conclusions, including collective scope; do not turn group into single to fix IDs. "
    "Evidence: a short relevant cue, at most 120 characters. JSON only."
)


def speakers(speaker_policy=None):
    # Missing policy is the saved v3 contract, including unfinished batches.
    if speaker_policy is None:
        return SPEAKERS
    if speaker_policy == DEFAULT_SPEAKER_POLICY:
        return ("others", "narrator")
    raise ValueError("Unsupported speaker policy")


def prompt(speaker_policy=None):
    speakers(speaker_policy)
    if speaker_policy is None:
        return PROMPT
    begin = PROMPT.index("groups means")
    end = PROMPT.index("addressee_type:", begin)
    return (PROMPT[:begin] +
            "Use narrator only when content_type is narration. Narration may also have a supplied "
            "stable ID or others as speaker. If joint speech cannot be attributed to a supported "
            "individual, use others; do not assign it to an arbitrary character. " + PROMPT[end:])


def schema(targets, speaker_policy=None):
    speaker_labels = speakers(speaker_policy)
    branches = []
    for target in targets:
        ids = [c["id"] for c in target["identity_candidates"]]
        candidates = [c["id"] for c in target["candidates"]]
        mentions = {"type": "array", "minItems": len(candidates), "maxItems": len(candidates)}
        if candidates:
            mentions["items"] = object_schema({
                "candidate_id": {"enum": candidates}, "mention_type": {"enum": sorted(ROLES)},
                "name_target_id": {"enum": [*ids, None]}})
        else:
            mentions["items"] = {"type": "object"}
        branches.append(object_schema({
            "utterance_id": {"enum": [target["utterance_id"]]},
            "content_type": {"enum": sorted(CONTENT)},
            "speaker_id": {"enum": [*ids, *speaker_labels]},
            "addressee_type": {"enum": sorted(ADDRESS)},
            "addressee_ids": {"type": "array", "minItems": 1, "uniqueItems": True,
                              "items": {"enum": [*ids, *LISTENERS]}},
            "mentions": mentions, "evidence": {"type": "string", "maxLength": 120}}))
    return object_schema({"utterances": {"type": "array", "minItems": len(targets),
        "maxItems": len(targets), "items": {"anyOf": branches}}})


def readable(value):
    """Accept mixed batches so valid targets survive an invalid sibling."""
    if not isinstance(value, dict) or not isinstance(value.get("utterances"), list):
        raise ValueError("Expected an utterances array")
