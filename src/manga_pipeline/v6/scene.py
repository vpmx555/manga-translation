"""Compact whole-chapter request; source ranges are constructed by code."""
from ..runtime.llm_budget import BudgetExceeded, OutputTruncated
from .semantic_contract import object_schema

PROMPT = """Read ALL supplied essential dialogue and captions in source order.
Return scene starts in source order, including the first target. A scene follows
conversation continuity: a page change, aside or minor topic shift is not a new
scene. Each summary must be English, one or two sentences, at most 512 characters.
Summaries provide context only, never speaker or identity evidence. JSON only."""


def validate(value, rows):
    entries = value.get("scenes") if isinstance(value, dict) else None
    ids = [r["id"] for r in rows]
    if not isinstance(entries, list) or not entries:
        raise ValueError("Scene plan is empty")
    starts = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("start_id") not in ids:
            raise ValueError("Unknown scene start")
        summary = entry.get("summary")
        import re
        letters = [c for c in summary if c.isalpha()] if isinstance(summary, str) else []
        # English prose can contain accented character names. Reject predominantly
        # non-ASCII prose without treating one such name as a language violation.
        non_ascii = sum(not c.isascii() for c in letters)
        sentences = re.sub(r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St)\.", "Title",
                           summary if isinstance(summary, str) else "", flags=re.I)
        if (not isinstance(summary, str) or not summary.strip() or len(summary) > 512
                or not re.search(r"[A-Za-z]", summary)
                or non_ascii > .20 * len(letters)
                or not 1 <= len(re.split(r"(?<=[.!?])\s+(?=[A-Za-z])", sentences.strip())) <= 2):
            raise ValueError("Scene summary must be English, 1–2 sentences, <=512 characters")
        starts.append(ids.index(entry["start_id"]))
    if starts[0] != 0 or starts != sorted(set(starts)):
        raise ValueError("Scene starts must cover source order without duplicates")
    return [{"scene_id": f"scene:{i+1}", "summary": entry["summary"],
             "target_ids": ids[start:(starts[i+1] if i+1 < len(starts) else len(ids))]}
            for i, (start, entry) in enumerate(zip(starts, entries))]


def plan(rows, calls, name_rows=None, normalization_state=None):
    if not rows:
        if name_rows is None or not name_rows:
            if normalization_state is not None:
                from .name_normalization import process
                return [], {}, process({"patches": [], "groups": [], "suspicions": []}, [], {}, normalization_state)
            return [] if name_rows is None else ([], {})
    schema = object_schema({"scenes": {"type": "array", "minItems": 1,
        "items": object_schema({"start_id": {"type": "string", "enum": [r["id"] for r in rows]},
                               "summary": {"type": "string", "maxLength": 512}})}})
    request = {"targets": [{"id": r["id"], "text": r["text"]} for r in rows]}
    prompt = PROMPT
    if name_rows is not None:
        schema["properties"]["names"] = {"type": "array", "items": object_schema({
            "utterance_id": {"type": "string", "enum": [r["id"] for r in name_rows]},
            "name": {"type": "string", "minLength": 1}})}
        schema["required"].append("names")
        if not rows:
            schema["properties"]["scenes"] = {"type": "array", "maxItems": 0}
        request["name_targets"] = [{"id": r["id"], "text": r["text"]} for r in name_rows]
        prompt += ("\nIn the SAME response extract every explicit person name from name_targets. "
                   "Return names as utterance_id and exact case-sensitive source substring, including "
                   "attached honorifics. In names never correct OCR, invent names, infer a speaker name, or "
                   "return pronouns/titles alone. Return each distinct surface once per utterance; "
                   "names may be empty. Scene starts use targets only; if targets is empty return scenes=[].")
        if normalization_state is not None:
            from . import name_normalization
            schema["properties"]["name_normalization"] = name_normalization.schema([r["id"] for r in name_rows])
            schema["required"].append("name_normalization")
            request["name_registry"] = name_normalization.registry_context(normalization_state)
            prompt += name_normalization.PROMPT
    from . import scene_wire
    source_rows = name_rows if name_rows is not None else rows
    request, schema, ledger = scene_wire.encode(request, schema, source_rows)
    prompt = prompt.replace('name_targets', 'targets')
    prompt = prompt.replace('including the first target', 'including the first scene_target=true target')
    prompt += ('\nWire contract scene-name-wire-v2: targets contains each original text exactly once. '
               'Only rows with scene_target=true may start scenes; scan names in ALL rows. '
               'Use supplied uN references for utterances and pN references for registry people, '
                'including evidence_refs. Never return full source IDs. Name selectors address '
                'the unchanged original text; code resolves character spans. Do not rewrite text. If there are no scene_target=true '
               'rows return scenes=[].')
    if hasattr(calls, 'store'):
        from ..storage.io import atomic_json, read_json
        path = calls.store.artifact_path('scan').parent / 'scene-wire.json'
        if path.exists() and read_json(path) != ledger:
            raise ValueError('Frozen scene wire mapping changed')
        atomic_json(path, ledger)
    # Retry accounting belongs to the journal, including across crashes.
    feedback = ""
    for attempt in range(3):
        reserve = 8192
        try:
            value = calls.call("scene:compact-v3", "analyze_scene_v6", prompt + feedback, request, schema,
                               reserve=reserve, attempt=attempt, oom_retry=False)
            value = scene_wire.decode(value, ledger)
            if not rows and (not isinstance(value, dict) or value.get("scenes") != []):
                raise ValueError("Scenes must be empty without essential targets")
            scenes = validate(value, rows) if rows else []
            if name_rows is None:
                return scenes
            names = validate_names(value, name_rows)
            if normalization_state is not None:
                normalized = name_normalization.process(value.get("name_normalization"), name_rows, names,
                                                       normalization_state)
                coverage = normalized['grouping_coverage']
                if not coverage['complete']:
                    missing = coverage['ungrouped'][:8]
                    raise ValueError('Alias grouping incomplete: every detected name needs a group member; '
                                     + repr(missing))
                return scenes, names, normalized
            return scenes, names
        except (OutputTruncated, ValueError) as exc:
            if isinstance(exc, BudgetExceeded) and not isinstance(exc, OutputTruncated):
                raise
            if attempt == 2:
                raise
            # Keep source validation strict. A rejected response must receive
            # actionable feedback rather than repeating the identical prompt.
            reason = str(exc)
            for wire, source in {**ledger['rows'], **ledger['persons']}.items():
                reason = reason.replace(source, wire)
            feedback = ("\nPrevious response was rejected: " + reason[:1200] +
                        ". Recompute the complete response from ORIGINAL targets. "
                        "Every names entry must occur verbatim in its referenced row. "
                        "Do not move a name to another row or silently correct it; "
                        "corrections belong only in source-addressed patches. "
                        "Verify original names, occurrence_index, alias-group coverage and references before returning JSON.")
    raise AssertionError("unreachable")


def validate_names(value, rows):
    import re
    by_id = {r["id"]: r["text"] for r in rows}
    result = {identity: [] for identity in by_id}
    entries = value.get("names") if isinstance(value, dict) else None
    if not isinstance(entries, list):
        raise ValueError("Missing scene name scan")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid scene name mention")
        identity, name = entry.get("utterance_id"), entry.get("name")
        if identity not in by_id or not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid scene name mention")
        matches = list(re.finditer(r"(?<!\w)" + re.escape(name) + r"(?!\w)", by_id[identity]))
        if not matches:
            raise ValueError(f"LLM name {name!r} at {identity} is not an exact source substring of {by_id[identity]!r}")
        for match in matches:
            key = (identity, match.start(), match.end())
            if key not in seen:
                seen.add(key)
                result[identity].append({"name": name, "start": match.start(),
                                         "end": match.end(), "source": "llm_scene"})
    return result
