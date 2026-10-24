"""Natural translation entry point and measured typesetting shortening."""
import re
from pathlib import Path
from ..storage.io import atomic_json, read_json
from ..translation import contract
from ..translation.stage import glossary_for, overrides_for
from .semantic import object_schema
from ..runtime.resources import BudgetExceeded, ResourceExhausted

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


def known_address_history(prior, batch):
    """Historical reader helper; parallel translation never consumes this history."""
    people = {r["speaker_person_id"] for r in batch if r.get("speaker_person_id") is not None}
    people.update(p for r in batch for p in r.get("addressee_ids", []) if p is not None)
    return [r for r in prior if r.get("speaker_person_id") is not None and r["speaker_person_id"] in people
            and r.get("semantic_resolution") != "unresolved"][-12:]


def translate(store, provider, progress=None):
    from .translation_parallel import translate as parallel_translate
    return parallel_translate(store, provider, progress)


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
    request = {"policy": "overflow-v6.2-address-consistency", "id": row["id"], "style": style,
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
                "Keep the Vietnamese self-reference and address terms from previous_translation unchanged. "
                "Never cut a contour's fragment independently. If this is impossible, meaning_preserved=false. "
                "Flag uncertainty in review_reason. JSON only.", request, schema, check)
            check(cached)
        except (BudgetExceeded, ResourceExhausted):
            raise
        except Exception as exc:
            store.commit_target("typeset", key, request, result={"attempt_status": "rejected", "error": str(exc)})
            raise
        store.commit_target("typeset", key, request, result=cached)
    check(cached)
    return cached
