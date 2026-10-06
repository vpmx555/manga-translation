"""Text-only speaker/addressee semantics for essential-v2."""
import copy

POLICY = "essential-dialogue-v2"
ADDRESS_TYPES = {"single", "group", "audience", "self", "unknown"}
SPEAKER_SPECIAL = ("others", "narrator")
LISTENER_SPECIAL = ("public_audience", "self", "unknown")

PROMPT = (
    "Analyze EACH target in the supplied source order, using only supplied text and stable ID candidates. "
    "If validation_errors are supplied, correct them. Return every target and its name candidates exactly once. "
    "Each target may use only its context_ids for text context; the shared turns table serves other targets too. "
    "MAGI source_speaker_id is a hint, not proof; correct it when conversational or name evidence contradicts it. "
    "speaker_id: choose a stable ID from that target's identity_candidates; otherwise others. "
    "Use narrator ONLY for narration whose teller cannot be identified as a supplied character. "
    "Preserve a known character teller's ID. others is unresolved, not one persistent person across turns. "
    "Every target needs a nonempty addressee_ids list. Choose stable listener IDs only from its candidates. "
    "Classify content by context: dialogue is speech to someone, thought is internal monologue, "
    "narration is the story's narrative prose; use unknown if unclear. Past tense, first person, "
    "or five coherent sentences alone do not prove narration. Retelling events TO another character is dialogue. "
    "Narration ALWAYS has addressee_type audience and addressee_ids [public_audience], "
    "whether speaker_id is a character or narrator. Thought ALWAYS has type self: "
    "listeners [speaker_id] for a known character, or [self] when speaker_id is others. "
    "For other content choose the intended recipient type separately from recipient identity: "
    "single = one person, [one stable ID] or [unknown] if their identity is unclear; "
    "group = multiple story people, [two or more stable IDs], [known IDs plus unknown], "
    "or [unknown] if the group members are unidentified; "
    "audience = readers/viewers outside the story, ONLY [public_audience]; "
    "self = speaking to oneself, ONLY [speaker_id] if known or [self] if speaker is others; "
    "unknown = recipient type is unclear, ONLY [unknown]. Never use unknown type with a known listener ID. "
    "A crowd INSIDE the story is group, never audience. unknown in a group means remaining unidentified members. "
    "Use group only with evidence of plural recipients. Include a known member only with evidence they are addressed. "
    "Do not fill the group with all candidates. Examples: 'You two!' can be group [unknown]; "
    "'Hey, you!' can be single [unknown]; 'Attention, passengers!' is group, not audience. "
    "Candidates and remembered participants are possibilities, not confirmed listeners. "
    "Even with only two candidate IDs, do not automatically choose the one other than the speaker. "
    "Use turn-taking, direct names and contextual evidence; otherwise leave recipient IDs unknown. "
    "The speaker is not their own listener except explicit self-address. Never invent IDs or use images. "
    "Classify a name's linguistic role separately from knowing its ID. "
    "An unmapped name can still be direct_address; leave name_target_id null only if its stable ID is unclear. "
    "Vocatives can occur at the END of a line, e.g. '...you, Madol-san'. Honorific -san does not invalidate a name. "
    "A direct_address name identifies a listener; third_person mentions do not. "
    "NER candidates can be mistakes: for a non-person-name use mention_type unknown and name_target_id null. "
    "Use mapped names/aliases and contextual continuations. For a direct_address name in dialogue, "
    "you may conclude name_target_id is one supplied stable listener ID, including for a new name; otherwise null. "
    "Never map names to special tokens, reassign an already mapped name, or invent an alias from a shared surname. "
    "Keep names as written. Do not infer scenes or change reading order. "
    "evidence: at most twelve words supporting the choices, not the entire utterance. Return JSON only."
)


def response_items(base, targets):
    """Constrain complete role combinations, including each target's stable pool.

    Full object alternatives avoid relying on conditional-schema keywords in the
    model's JSON grammar. Validation remains authoritative for evidence/coverage.
    """
    branches = []
    for target in targets:
        allowed = sorted(c["id"] for c in target["identity_candidates"])
        template = copy.deepcopy(base)
        props = template["properties"]
        props["utterance_id"] = {"const": target["utterance_id"]}
        candidates = [c["id"] for c in target["candidates"]]
        props["mentions"].update(minItems=len(candidates), maxItems=len(candidates))
        if candidates:
            props["mentions"]["items"]["properties"]["candidate_id"] = {"enum": candidates}
        props["mentions"]["items"]["properties"]["name_target_id"] = {"enum": [*allowed, None]}

        def add(content, speaker, listeners, address):
            variant = copy.deepcopy(template)
            fields = variant["properties"]
            fields["content_type"] = {"enum": content}
            fields["speaker_id"] = speaker
            fields["addressee_ids"] = listeners
            fields["addressee_type"] = {"const": address}
            if address in {"audience", "unknown"}:
                fields["mentions"]["items"]["properties"]["name_target_id"] = {"type": "null"}
            branches.append(variant)

        dialogue = ["dialogue", "unknown"]
        add(["narration"], {"enum": [*allowed, "narrator"]}, {"const": ["public_audience"]}, "audience")
        add(dialogue, {"enum": [*allowed, "others"]}, {"const": ["public_audience"]}, "audience")
        add(dialogue, {"enum": [*allowed, "others"]}, {"const": ["unknown"]}, "unknown")
        for speaker in [*allowed, "others"]:
            others = [identity for identity in allowed if identity != speaker]
            add(["thought", *dialogue], {"const": speaker},
                {"const": [speaker] if type(speaker) is int else ["self"]}, "self")
            add(dialogue, {"const": speaker},
                {"type": "array", "items": {"enum": [*others, "unknown"]},
                 "minItems": 1, "maxItems": 1}, "single")
            if not others:
                group = {"const": ["unknown"]}
            elif len(others) == 1:
                group = {"enum": [["unknown"], [others[0], "unknown"], ["unknown", others[0]]]}
            else:
                group = {"anyOf": [{"const": ["unknown"]},
                                   {"type": "array", "items": {"enum": [*others, "unknown"]},
                                    "minItems": 2, "maxItems": len(others) + 1, "uniqueItems": True}]}
            add(dialogue, {"const": speaker}, group, "group")
    return {"anyOf": branches}


def validate_identities(item, allowed):
    """Enforce semantics without inventing an identity or rewriting model decisions."""
    kind = item.get("content_type")
    address = item.get("addressee_type")
    speaker = item.get("speaker_id")
    listeners = item.get("addressee_ids")

    def eligible(identity):
        return type(identity) is int and identity in allowed

    if address not in ADDRESS_TYPES:
        raise ValueError("Use single/group/audience/self/unknown addressee_type")
    if not isinstance(listeners, list) or not listeners:
        raise ValueError("Each utterance requires a nonempty addressee_ids list")
    if any(not eligible(x) and x not in LISTENER_SPECIAL for x in listeners):
        raise ValueError("Listener outside supplied stable pool or permitted special IDs")
    if len(listeners) != len(set(listeners)):
        raise ValueError("Listener IDs must be distinct")
    if not eligible(speaker) and speaker not in SPEAKER_SPECIAL:
        raise ValueError("Speaker must be a supplied stable ID, others, or narrator")
    if kind == "narration":
        if speaker == "others" or address != "audience" or listeners != ["public_audience"]:
            raise ValueError("Narration uses a supplied character ID, or narrator when the teller ID is unknown; "
                             "never others. Set audience and [public_audience].")
    elif speaker == "narrator":
        raise ValueError("narrator is valid only for narration")
    if kind == "thought" and address != "self":
        raise ValueError("Thought requires self-address")

    if address == "single":
        if len(listeners) != 1 or (not eligible(listeners[0]) and listeners != ["unknown"]):
            raise ValueError("single requires one stable listener ID or [unknown]")
    elif address == "group":
        if any(not eligible(x) and x != "unknown" for x in listeners):
            raise ValueError("group requires stable member IDs and/or unknown, never public_audience/self")
        if len(listeners) < 2 and listeners != ["unknown"]:
            raise ValueError("group with one known ID also needs unknown for unidentified members; "
                             "use group [unknown] if no members are identified, or single for only one person")
    elif address == "audience":
        if listeners != ["public_audience"]:
            raise ValueError("audience requires [public_audience]")
    elif address == "self":
        expected = [speaker] if eligible(speaker) else ["self"]
        if speaker == "narrator" or listeners != expected:
            raise ValueError("self requires [speaker_id] for a known character, otherwise [self]")
    elif listeners != ["unknown"]:
        raise ValueError("unknown type requires [unknown]; a known recipient needs single/group/self")

    if eligible(speaker) and speaker in listeners and address != "self":
        raise ValueError("Same speaker/listener requires explicit self-address type")
