"""Source-addressed name edits. No model response can replace a whole document."""
import copy
import re

from ..storage.io import fingerprint
from . import persons
from .semantic_contract import object_schema

POLICY = "source-name-patches-v1"
PROMPT = """
Also return name_normalization: patches, groups, suspicions.
The names array must still contain exact ORIGINAL source substrings; all proposed
corrections belong exclusively in name_normalization.patches.
Select names by utterance_id, the exact ORIGINAL name string and occurrence_index
(zero-based among appearances of that same name in the row; normally 0).
Do NOT calculate character offsets: code resolves the name to its original span.
A patch contains utterance_id,original,occurrence_index,replacement,status,evidence,
evidence_refs. Never return rewritten sentences, targets or documents. Preserve
capitalization style, attached honorifics and short/full forms; do not expand a
surname into a full name. Example SASA- MORI - CHAN -> SASAMORI-CHAN.
Use status=confirmed only with evidence: spacing/hyphen repair, a clear occurrence
elsewhere in the chapter, or a supplied registry name. Frequency alone is not
proof. Registry is advisory and may be wrong. Otherwise status=suspect, leave the
source unchanged, and explain the uncertainty. evidence_refs contains supplied
utterance IDs or registry person IDs, not invented IDs.
groups are PROPOSED aliases, not person identities: group_id,canonical_name,
members (utterance_id,name,occurrence_index),evidence,evidence_refs. Group short/full names,
honorific variants and OCR errors only when context supports the SAME person.
Shared surnames alone do not prove identity. Never merge person or MAGI IDs.
Assign EVERY detected name occurrence to exactly one group, including names in
publication/cast text and uncertain names. Use a singleton if evidence cannot
link it; do not omit grouping when names are present. Reuse the same group_id for
surname, given name, full name, reversed full-name order, honorific variants and
broken OCR spacing when chapter context identifies the SAME individual. Prefer
the most complete actual source/registry name as canonical_name. Grouping is not
a claim that the person is visible or identified by MAGI. Do not force a fixed
number of groups; keep genuinely different or uncertain people separate.
Keep uncertain names in suspicions (utterance_id,name,occurrence_index,reason). Analyze will
confirm whether each mention is a person's name and its owner; it will NOT edit
spelling or reorganize groups. Only confirmed individual mentions are persisted.
"""


def schema(ids):
    location = {"utterance_id": {"type": "string", "enum": ids},
                "occurrence_index": {"type": "integer", "minimum": 0}}
    string = {"type": "string", "minLength": 1}
    evidence = {"evidence": string, "evidence_refs": {"type": "array", "minItems": 1,
                                                        "items": string}}
    return object_schema({
        "patches": {"type": "array", "items": object_schema({**location,
            "original": string, "replacement": string,
            "status": {"type": "string", "enum": ["confirmed", "suspect"]}, **evidence})},
        "groups": {"type": "array", "items": object_schema({"group_id": string,
            "canonical_name": string, "members": {"type": "array", "minItems": 1,
                "items": object_schema({**location, "name": string})}, **evidence})},
        "suspicions": {"type": "array", "items": object_schema({**location, "name": string, "reason": string})}})


def registry_context(state):
    return [{"person_id": pid, "primary_name": person.get("primary_name"),
             "aliases": [f["surface"] for f in person.get("name_forms", [])]}
            for pid, person in sorted(state.get("persons", {}).items())
            if person.get("primary_name") or person.get("name_forms")]


def location(item, texts, mentions):
    if not isinstance(item, dict):
        raise ValueError("Invalid name location")
    uid, start, end = item.get("utterance_id"), item.get("start"), item.get("end")
    if uid not in texts:
        raise ValueError("Unknown name source utterance")
    if type(start) is int and type(end) is int and (uid, start, end) in mentions:
        return uid, start, end
    surface = item.get('original', item.get('name'))
    candidates = sorted((u, a, b) for u, a, b in mentions if u == uid)
    if isinstance(surface, str) and surface.strip():
        exact = [m for m in candidates if texts[uid][m[1]:m[2]].casefold() == surface.casefold()]
        candidates = exact or [m for m in candidates if compact(texts[uid][m[1]:m[2]]) == compact(surface)]
    ordinal = item.get('occurrence_index')
    if ordinal is not None:
        if type(ordinal) is not int or not 0 <= ordinal < len(candidates):
            raise ValueError('Unknown name occurrence index')
        return candidates[ordinal]
    if len(candidates) != 1:
        raise ValueError("Location must select one complete detected name in original text; ambiguous or missing name")
    return candidates[0]


def resolved_location(item, texts, mentions, repairs):
    uid, start, end = location(item, texts, mentions)
    if item.get('start') != start or item.get('end') != end:
        repairs.append({'kind': 'source_span_resolved', 'utterance_id': uid,
                        'original_selector': copy.deepcopy(item), 'start': start, 'end': end})
    return {**copy.deepcopy(item), 'utterance_id': uid, 'start': start, 'end': end}


def compact(value):
    return re.sub(r"[\s\-–—]", "", value).casefold()


def supported(item, texts, registry):
    refs = item.get("evidence_refs")
    if (not isinstance(item.get("evidence"), str) or not item["evidence"].strip()
            or not isinstance(refs, list) or not refs
            or any(not isinstance(ref, str) or ref not in texts and ref not in registry for ref in refs)):
        raise ValueError("Missing or unknown name evidence")


def process(value, rows, names, state):
    if not isinstance(value, dict) or any(not isinstance(value.get(k), list)
                                          for k in ("patches", "groups", "suspicions")):
        raise ValueError("Missing name normalization contract")
    texts = {r["id"]: r["text"] for r in rows}
    mentions = {(uid, m["start"], m["end"]) for uid, spans in names.items() for m in spans}
    registry = {r["person_id"]: r for r in registry_context(state)}
    result = {"policy": POLICY, "source_hash": fingerprint(rows), "patches": [],
              "groups": [], "suspicions": [], "rejected": [], "repairs": [],
              "input_contract": "source-name-selectors-v2"}
    for proposal in value["patches"]:
        try:
            proposal = resolved_location(proposal, texts, mentions, result['repairs'])
            uid, start, end = proposal['utterance_id'], proposal['start'], proposal['end']
            original, replacement = proposal.get("original"), proposal.get("replacement")
            if original != texts[uid][start:end]:
                if not isinstance(original, str) or compact(original) != compact(texts[uid][start:end]):
                    raise ValueError("Patch original does not match source")
                original = texts[uid][start:end]
                proposal['original'] = original
            if (not isinstance(replacement, str) or not replacement.strip()
                    or any(not (c.isalnum() or c.isspace() or c in "-–—'’." ) for c in replacement)):
                raise ValueError("Replacement must contain only a name")
            before, after = persons.name_form(original), persons.name_form(replacement)
            if before["honorific"] != after["honorific"] and compact(original) != compact(replacement):
                raise ValueError("Patch cannot add, remove or change honorific")
            def words(base):
                return re.sub(r"\s*[-–—]\s*", "", base).split()
            if len(words(after["base_name"])) > len(words(before["base_name"])):
                raise ValueError("Patch cannot expand a short name into a full name")
            if not .5 * len(compact(original)) <= len(compact(replacement)) <= 1.5 * len(compact(original)) + 2:
                raise ValueError("Patch expands or removes name content")
            if original.isupper():
                replacement = replacement.upper()
            elif original.islower():
                replacement = replacement.lower()
            supported(proposal, texts, registry)
            status = proposal.get("status")
            if status not in {"confirmed", "suspect"}:
                raise ValueError("Invalid patch status")
            if status == "confirmed" and compact(original) != compact(replacement):
                # A spelling change needs an actual supporting form, not merely
                # an explanation or the damaged occurrence itself.
                target = after["base_name"]
                sources = []
                for ref in proposal["evidence_refs"]:
                    if ref in registry:
                        sources.extend([registry[ref]["primary_name"] or "", *registry[ref]["aliases"]])
                    else:
                        sources.append(texts[ref][:start] + texts[ref][end:] if ref == uid else texts[ref])
                if not any(re.search(r"(?<!\w)" + re.escape(target) + r"(?!\w)", s, re.I) for s in sources):
                    raise ValueError("Spelling change lacks a clear supporting source form")
            item = {**copy.deepcopy(proposal), "replacement": replacement,
                    "status": "applied" if status == "confirmed" else "suspect"}
            result["patches"].append(item)
        except (ValueError, TypeError, KeyError) as exc:
            result["rejected"].append({"kind": "patch", "proposal": proposal, "reason": str(exc)})
    # Reject *all* conflicting edits, independent of their response order.
    patches = result["patches"]
    for item in patches:
        if any(other is not item and other["utterance_id"] == item["utterance_id"]
               and max(other["start"], item["start"]) < min(other["end"], item["end"])
               for other in patches):
            item.update(status="rejected", rejection_reason="Overlapping name patches")
    group_ids = set()
    for proposal in value["groups"]:
        try:
            if not isinstance(proposal, dict):
                raise ValueError("Invalid alias group")
            supported(proposal, texts, registry)
            gid = proposal.get("group_id")
            if not isinstance(gid, str) or not gid.strip() or gid in group_ids:
                raise ValueError("Invalid or duplicate alias group ID")
            persons.name_form(proposal["canonical_name"])
            resolved = [resolved_location(m, texts, mentions, result['repairs']) for m in proposal['members']]
            members = [(m['utterance_id'], m['start'], m['end']) for m in resolved]
            if not members or len(set(members)) != len(members):
                raise ValueError("Empty or duplicate alias group members")
            surfaces = []
            for uid, start, end in members:
                surface = texts[uid][start:end]
                for p in result["patches"]:
                    if (p["status"] == "applied" and p["utterance_id"] == uid
                            and p["start"] == start and p["end"] == end):
                        surface = p["replacement"]
                surfaces.append(surface)
            forms = {persons.name_form(s)["base_name"] for s in surfaces}
            for ref in proposal["evidence_refs"]:
                if ref in registry:
                    forms.update(persons.name_form(s)["base_name"] for s in
                                 [registry[ref]["primary_name"], *registry[ref]["aliases"]] if s)
            if persons.name_form(proposal["canonical_name"])["base_name"] not in forms:
                raise ValueError("Canonical group name lacks a source or registry form")
            group_ids.add(gid)
            result["groups"].append({**copy.deepcopy(proposal), "members": resolved, "aliases": sorted(set(surfaces)),
                                     "status": "proposed"})
        except (ValueError, TypeError, KeyError) as exc:
            result["rejected"].append({"kind": "group", "proposal": proposal, "reason": str(exc)})
    # Ambiguous membership cannot become an implicit identity constraint.
    counts = {}
    for group in result["groups"]:
        for member in group["members"]:
            key = (member["utterance_id"], member["start"], member["end"])
            counts[key] = counts.get(key, 0) + 1
    for group in result["groups"]:
        if any(counts[(m["utterance_id"], m["start"], m["end"])] > 1 for m in group["members"]):
            group["status"] = "suspect"
    for proposal in value["suspicions"]:
        try:
            proposal = resolved_location(proposal, texts, mentions, result['repairs'])
            if not isinstance(proposal.get("reason"), str) or not proposal["reason"].strip():
                raise ValueError("Missing suspicion reason")
            result["suspicions"].append(copy.deepcopy(proposal))
        except (ValueError, TypeError, KeyError) as exc:
            result["rejected"].append({"kind": "suspicion", "proposal": proposal, "reason": str(exc)})
    covered = {(m['utterance_id'], m['start'], m['end']) for g in result['groups'] for m in g['members']}
    result['grouping_coverage'] = {'detected': len(mentions), 'grouped': len(covered),
        'ungrouped': [{'utterance_id': u, 'start': a, 'end': b, 'name': texts[u][a:b]}
                      for u, a, b in sorted(mentions - covered)],
        'complete': covered == mentions}
    return result


def edits(normalization, uid):
    return sorted([p for p in normalization.get("patches", [])
                   if p["utterance_id"] == uid and p["status"] == "applied"], key=lambda p: p["start"])


def mapped_span(start, end, patches):
    def offset(pos):
        return pos + sum(len(p["replacement"]) - (p["end"]-p["start"])
                         for p in patches if p["end"] <= pos)
    # Complete name boundaries are mapped; partial spans inside an edit are invalid.
    if any(p["start"] < start < p["end"] or p["start"] < end < p["end"] for p in patches):
        raise ValueError("Name span cuts through a correction")
    return offset(start), offset(end)


def apply(document, normalization):
    result = copy.deepcopy(document)
    rows = result["utterances"]
    if normalization.get('policy') in {'canonical-scan-v3', 'canonical-scan-v4'}:
        values = normalization['canonical_rows']
        if fingerprint(values) != normalization['canonical_hash']:
            raise ValueError('Canonical OCR snapshot changed')
        current = [{'id': r['id'], 'text': r['text']} for r in rows]
        if normalization.get('source_hash') != fingerprint(rows) and fingerprint(current) != normalization['canonical_hash']:
            raise ValueError('Canonical OCR input version changed')
        canonical = {r['id']: r['text'] for r in values}
        if set(canonical) != {r['id'] for r in rows}:
            raise ValueError('Canonical OCR ID coverage changed')
        for row in rows:
            if row['text'] != canonical[row['id']]:
                row['ocr_raw_text'] = row.get('ocr_raw_text', row['text'])
            row['text'] = canonical[row['id']]
        result['canonical_ocr_version'] = normalization['canonical_hash']
        return result
    if normalization.get("source_hash") != fingerprint(document["utterances"]):
        raise ValueError("Name normalization source changed")
    for row in rows:
        patches = edits(normalization, row["id"])
        original = row["text"]
        text = original
        for patch in reversed(patches):
            if original[patch["start"]:patch["end"]] != patch["original"]:
                raise ValueError("Name patch no longer matches source")
            text = text[:patch["start"]] + patch["replacement"] + text[patch["end"]:]
        if patches:
            row.update(text=text, original_text=original, name_corrections=copy.deepcopy(patches))
    return result
