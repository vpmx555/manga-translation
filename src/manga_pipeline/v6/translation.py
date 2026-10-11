"""Whole-utterance dual translation; effects are exported without image edits."""
import copy
import re
from pathlib import Path
from ..progress import NullProgress
from ..storage.io import atomic_json, fingerprint, read_json
from ..translation import contract
from ..translation.stage import glossary_for, overrides_for
from .semantic import object_schema


def wire_schema(ids):
    row = contract.schema([ids[0]])["properties"]["translations"]["items"]["anyOf"][0]
    row = object_schema({k:v for k,v in row["properties"].items() if k != "id"})
    return object_schema({"translations":object_schema({identity:row for identity in ids})})


def ordered_response(value, ids):
    if not isinstance(value, dict):
        raise ValueError("Translation response must be an object")
    payload = value.get("translations")
    if isinstance(payload, dict):
        if set(payload) != set(ids):
            raise ValueError("Translation target coverage changed")
        payload = [{**payload[identity], "id":identity} for identity in ids]
    if not isinstance(payload, list) or len(payload) != len(ids) or {r.get("id") for r in payload} != set(ids):
        raise ValueError("Translation target coverage changed")
    indexed = {r["id"]:r for r in payload}
    value["translations"] = [indexed[identity] for identity in ids]


def resources(store, rows):
    path = store.directory / "snapshots/translation-v6.json"
    if path.exists():
        return read_json(path)
    cfg = store.manifest["config"]["translation"]
    bank = Path(store.manifest["bank_path"]).parent
    result = {"glossary": glossary_for(cfg["glossary_path"] or bank / "translation_glossary.json"),
              "overrides": overrides_for(cfg["overrides_path"] or store.directory / "translation_overrides.json", {r["id"] for r in rows})}
    atomic_json(path, result)
    return result


def translate(store, provider, progress=None):
    document = copy.deepcopy(store.read("export"))
    rows = sorted([*document["utterances"], *document["effects"]], key=lambda r: r["reading_order"])
    cfg = store.manifest["config"]["translation"]
    frozen = resources(store, rows)
    progress = progress or NullProgress()
    progress.reset(total=len(rows)*len(contract.STYLES), unit="translation")
    failures = []
    for row in rows:
        row["translations"] = {}
    for style in contract.STYLES:
        for start in range(0, len(rows), cfg["batch_size"]):
            batch = rows[start:start+cfg["batch_size"]]
            ids = [r["id"] for r in batch]
            context = rows[max(0,start-cfg["context_radius"]):start+len(batch)+cfg["context_radius"]]
            fields = ("id", "text", "content_type", "speech_style", "speaker_person_id", "speaker_name", "addressee_ids", "mentions")
            request = {"policy": "group-translation-v6.2", "style": style,
                "targets": [{k: r.get(k) for k in fields} for r in batch],
                "context": [{k: r.get(k) for k in fields} for r in context],
                "glossary": frozen["glossary"], "instructions": cfg["instructions"],
                "manual": {r["id"]: frozen["overrides"][style][r["id"]] for r in batch if r["id"] in frozen["overrides"][style]}}
            key = f"{style}:batch:{start}:wire-v2"
            try:
                def check(value):
                    ordered_response(value,ids)
                    contract.readable(value)
                    if [r.get("id") for r in value["translations"]] != ids:
                        raise ValueError("Translation coverage/order changed")
                    for item, source in zip(value["translations"], batch):
                        errors = contract.issues(item, source, frozen["glossary"])
                        if errors:
                            raise ValueError(", ".join(errors))
                cached = store.target("translate", key, request)
                if cached is None:
                    manual = request["manual"]
                    pending = [r for r in batch if r["id"] not in manual]
                    generated = {}
                    if pending:
                        wire = {**request, "targets": [r for r in request["targets"] if r["id"] not in manual]}
                        pending_ids = [r["id"] for r in pending]
                        def pending_check(value):
                            ordered_response(value,pending_ids)
                            contract.readable(value)
                            if [r.get("id") for r in value["translations"]] != pending_ids:
                                raise ValueError("Translation coverage/order changed")
                            for item, source in zip(value["translations"], pending):
                                errors = contract.issues(item, source, frozen["glossary"])
                                if errors:
                                    raise ValueError(", ".join(errors))
                        response = provider.infer("translate_vi_group_v6_"+style,
                            contract.prompt(style, cfg["instructions"], "semantic-v6")+
                            " Each group target is one complete sentence across contours. Effects are context-only for image rendering.",
                            wire, wire_schema(pending_ids), pending_check)
                        generated = {r["id"]: r for r in response["translations"]}
                    cached = {"translations": [generated[r["id"]] if r["id"] not in manual else
                        {"id": r["id"], "translation": manual[r["id"]], "needs_review": False,
                         "review_reason": "", "glossary_proposals": []} for r in batch]}
                    check(cached)
                    store.commit_target("translate", key, request, result=cached)
                check(cached)
                for row, item in zip(batch, cached["translations"]):
                    row["translations"][style] = copy.deepcopy(item)
                    progress.advance()
            except Exception as exc:
                store.commit_target("translate", key, request, error=str(exc))
                failures.append({"style": style, "target_ids": ids, "error": str(exc)})
                for _ in batch:
                    progress.advance(failed=True)
    document.update(policy="group-translation-v6.2", failures=failures, translation_resources=frozen)
    atomic_json(store.artifact_path("translate").parent / "effects-translated.json", {"effects": document["effects"]})
    return document


def protected_terms(row, translation, glossary):
    terms = re.findall(r"-(?:san|kun|chan|sama|sensei|senpai|kohai|dono|tan)\b", row["text"], re.I)
    terms.extend(re.findall(r"\b\d+(?:[.,]\d+)*\b", translation))
    for entry in glossary:
        if entry["source"].casefold() in row["text"].casefold():
            terms.append(entry["vi"])
    for mention in row.get("mentions", []):
        # The confirmed English surface remains available in source_boxes/scan;
        # protect any established romanized name occurring in the full translation.
        for component in mention.get("components", []):
            value = component["value"]
            if value.casefold() in translation.casefold():
                terms.append(value)
    return sorted(set(terms), key=str.casefold)


def shorten(store, provider, row, style, previous, budget, attempt):
    glossary = store.read("translate")["translation_resources"]["glossary"]
    terms = protected_terms(row, previous, glossary)
    request = {"policy": "overflow-v6.1", "id": row["id"], "style": style,
        "source": row["text"], "previous_translation": previous, "layout_budget": budget,
        "protected_terms": terms, "analysis": {k: row.get(k) for k in
            ("content_type", "speech_style", "speaker_person_id", "addressee_ids")}, "attempt": attempt}
    schema = object_schema({"translation": {"type": "string"}, "meaning_preserved": {"type": "boolean"},
                            "review_reason": {"type": "string"}})
    def check(value):
        if not isinstance(value, dict) or not value.get("meaning_preserved"):
            raise ValueError("Shortening did not preserve source meaning")
        text = value.get("translation", "")
        if not text.strip() or len(text) >= len(previous) or any(term.casefold() not in text.casefold() for term in terms):
            raise ValueError("Shortening dropped protected terms or did not shorten")
        negative = r"(?<!\w)(?:không|chẳng|chưa|đừng|chớ)(?!\w)"
        if re.search(negative, previous, re.I) and not re.search(negative, text, re.I):
            raise ValueError("Shortening dropped explicit negation")
    key = f"shorten:{row['id']}:{style}:{attempt}"
    cached = store.target("typeset", key, request)
    if cached is not None and cached.get("attempt_status") == "rejected":
        raise ValueError(cached["error"])
    if cached is None:
        # One invocation per fit attempt; internal JSON retries cannot multiply M10.
        try:
            cached = provider.infer_once("translate_vi_shorten_v6", "Shorten the complete Vietnamese utterance to fit the supplied measured budget. "
                "Preserve all source meaning, names, honorifics, negation, numbers, speaker/addressee and emotion. "
                "Never cut a contour's fragment independently. If this is impossible, meaning_preserved=false. "
                "Flag uncertainty in review_reason. JSON only.", request, schema, check)
            check(cached)
        except Exception as exc:
            store.commit_target("typeset", key, request, result={"attempt_status": "rejected", "error": str(exc)})
            raise
        store.commit_target("typeset", key, request, result=cached)
    check(cached)
    return cached
