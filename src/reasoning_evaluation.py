"""Independent annotation templates and masked pilot metrics, never auto-gold."""
from __future__ import annotations

import copy
from itertools import combinations

from dialogue_data import document_fingerprint, validate_normalized
from reasoning_schemas import id_key


def review_template(document: dict) -> dict:
    validate_normalized(document)
    rows = document["utterances"]
    return {"schema_version": 1, "kind": "reasoning_reference", "confirmed": False, "reviewer": None,
            "source_fingerprint": document_fingerprint(document), "document_id": document["document_id"],
            "boundaries": [{"left_id": left["id"], "right_id": right["id"], "boundary": None, "reason": ""}
                           for left, right in zip(rows, rows[1:])],
            "utterances": [{"id": row["id"], "text": row["text"], "page": row.get("page"),
                            "speaker_reviewed": False, "speaker_id": None,
                            "name_reviewed": False, "speaker_name": None,
                            "addressee_reviewed": False, "addressee_type": None, "addressee_ids": [],
                            "order_reviewed": False, "corrected_order": None, "reason": ""} for row in rows],
            "instructions": ["Annotate from original pages/text independently, not from model proposals.",
                             "Null boundary means ambiguous; it is excluded from boundary scoring.",
                             "Reviewed speaker_id null means genuinely unknown, not an unannotated row.",
                             "Use source bank IDs; mark each reviewed field explicitly.",
                             "Set confirmed=true and reviewer yourself only after independent review."]}


def validate_reference(document: dict, reference: dict) -> None:
    validate_normalized(document)
    if (reference.get("kind") != "reasoning_reference" or reference.get("schema_version") != 1 or
            reference.get("confirmed") is not True or not isinstance(reference.get("reviewer"), str) or
            not reference["reviewer"].strip()):
        raise ValueError("Reference must be independently confirmed by a named reviewer")
    if reference.get("source_fingerprint") != document_fingerprint(document):
        raise ValueError("Reference fingerprint differs from the exact normalized input")
    expected = [(a["id"], b["id"]) for a, b in zip(document["utterances"], document["utterances"][1:])]
    actual = [(row["left_id"], row["right_id"]) for row in reference.get("boundaries", [])]
    if expected != actual or any(type(row["boundary"]) not in (bool, type(None)) for row in reference["boundaries"]):
        raise ValueError("Reference must cover the original boundary gaps in order")
    identities = [row["id"] for row in reference.get("utterances", [])]
    if identities != [row["id"] for row in document["utterances"]]:
        raise ValueError("Reference must cover each source utterance once in source order")
    for row in reference["utterances"]:
        for field in ("speaker_reviewed", "name_reviewed", "addressee_reviewed", "order_reviewed"):
            if type(row.get(field)) is not bool:
                raise ValueError("Reviewed flags must be explicit booleans")
        id_key(row.get("speaker_id"))
        if row.get("speaker_name") is not None and not isinstance(row["speaker_name"], str):
            raise ValueError("Invalid reference speaker_name")
        kind, ids = row.get("addressee_type"), row.get("addressee_ids")
        if not isinstance(ids, list) or any(x is None for x in ids) or len({id_key(x) for x in ids}) != len(ids):
            raise ValueError("Invalid reference addressee IDs")
        if row["addressee_reviewed"]:
            if kind not in ("individual", "group", "self", "unspecified", "not_applicable"):
                raise ValueError("Invalid reference addressee type")
            if ((kind in ("individual", "self") and len(ids) != 1) or
                    (kind == "group" and len(ids) < 2) or (kind in ("unspecified", "not_applicable") and ids)):
                raise ValueError("Reference addressee cardinality does not match type")
        order = row.get("corrected_order")
        if order is not None and (type(order) is not int or not 0 <= order < len(identities)):
            raise ValueError("Reference order must be a valid global zero-based position")
    annotated = [r["corrected_order"] for r in reference["utterances"] if r["order_reviewed"] and r["corrected_order"] is not None]
    if len(set(annotated)) != len(annotated):
        raise ValueError("Reference order positions must be unique")


def _prf(tp, fp, fn):
    return {"tp": tp, "fp": fp, "fn": fn, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}


def _accuracy(correct, count):
    return {"correct": correct, "count": count, "accuracy": correct / count if count else None}


def evaluate(document: dict, prediction: dict, reference: dict) -> dict:
    validate_reference(document, reference)
    if prediction.get("source_fingerprint") != document_fingerprint(document):
        raise ValueError("Prediction fingerprint differs from evaluation source")
    supplied = prediction.get("utterances", [])
    by_id = {r["id"]: r for r in supplied}
    if len(by_id) != len(supplied) or set(by_id) != {r["id"] for r in document["utterances"]}:
        raise ValueError("Prediction coverage differs from evaluation source")
    source = {r["id"]: r for r in document["utterances"]}
    predicted_gaps = {(r["left_id"], r["right_id"]): r.get("decision", r.get("boundary"))
                      for r in prediction.get("scene_boundaries", [])}
    tp = fp = fn = unresolved = count = 0
    for gap in reference["boundaries"]:
        if gap["boundary"] is None:
            continue
        decision = predicted_gaps.get((gap["left_id"], gap["right_id"]), "no_boundary")
        positive = decision == "boundary" or decision is True
        tp += positive and gap["boundary"]
        fp += positive and not gap["boundary"]
        fn += not positive and gap["boundary"]
        unresolved += decision == "uncertain"
        count += 1
    speaker_correct = speaker_count = known = false_corrections = correction_tp = correction_fp = correction_fn = 0
    name_correct = name_count = add_correct = add_count = add_tp = add_fp = add_fn = 0
    order_correct = order_count = 0
    for label in reference["utterances"]:
        row = by_id[label["id"]]
        if label["speaker_reviewed"]:
            correct = id_key(row.get("speaker_id")) == id_key(label["speaker_id"])
            original_correct = id_key(source[label["id"]].get("speaker_id")) == id_key(label["speaker_id"])
            changed = id_key(row.get("speaker_id")) != id_key(source[label["id"]].get("speaker_id"))
            speaker_correct += correct
            speaker_count += 1
            known += row.get("speaker_id") is not None
            false_corrections += original_correct and changed
            correction_tp += not original_correct and correct
            correction_fp += changed and not correct
            correction_fn += not original_correct and not correct
        if label["name_reviewed"]:
            name_correct += row.get("speaker_name") == label["speaker_name"]
            name_count += 1
        if label["addressee_reviewed"]:
            add_correct += row.get("addressee_type") == label["addressee_type"]
            add_count += 1
            expected_ids = {id_key(x) for x in label["addressee_ids"]}
            actual_ids = {id_key(x) for x in row.get("addressee_ids", [])}
            add_tp += len(actual_ids & expected_ids)
            add_fp += len(actual_ids - expected_ids)
            add_fn += len(expected_ids - actual_ids)
    ordered = [r for r in reference["utterances"] if r["order_reviewed"] and r["corrected_order"] is not None]
    for left, right in combinations(ordered, 2):
        if source[left["id"]].get("page") != source[right["id"]].get("page"):
            continue
        a, b = by_id[left["id"]], by_id[right["id"]]
        order_correct += (a["corrected_order"] < b["corrected_order"]) == (left["corrected_order"] < right["corrected_order"])
        order_count += 1
    return {"schema_version": 1, "kind": "reasoning_evaluation", "document_id": document["document_id"],
            "boundary": {**_prf(tp, fp, fn), "scored_gaps": count, "uncertain_predictions": unresolved},
            "speaker": {**_accuracy(speaker_correct, speaker_count), "non_null_predictions": known,
                        "false_corrections": false_corrections,
                        "correction": _prf(correction_tp, correction_fp, correction_fn)},
            "speaker_name": _accuracy(name_correct, name_count),
            "addressee_type": _accuracy(add_correct, add_count), "addressee_ids": _prf(add_tp, add_fp, add_fn),
            "reading_order_pairs": _accuracy(order_correct, order_count),
            "needs_review_utterances": sum(bool(r.get("review_flags")) for r in supplied),
            "run": copy.deepcopy(prediction.get("reasoning_run", {}))}
