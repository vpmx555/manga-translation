"""Whole-box rules only. Unknown/ambiguous strings survive with review notes."""
import copy
import re
import unicodedata
from ..storage.io import read_json

POLICY = "whole-box-rules-v1"
DEFAULT_RULES = {
    "keep": [],
    "drop": {
        "sfx": ["clench", "grit", "gritting teeth", "whoosh", "swish", "swoosh", "klink"],
        "watermark": ["allmanga"],
        "ui": ["load more comments", "sign in to comment", "log in to comment", "report this comment",
               "next chapter", "previous chapter", "sort by newest", "sort by oldest", "sort by top"],
    },
}


def key(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip().casefold().strip(" .!?…")


def rules_for(path=None):
    rules = copy.deepcopy(DEFAULT_RULES)
    if path is not None:
        supplied = read_json(path)
        if not isinstance(supplied, dict) or set(supplied) - {"keep", "drop"}:
            raise ValueError("Noise rules require keep/drop")
        if not isinstance(supplied.get("drop", {}), dict) or set(supplied.get("drop", {})) - set(rules["drop"]):
            raise ValueError("Unknown noise rule category")
        for name, values in [("keep", supplied.get("keep", [])), *supplied.get("drop", {}).items()]:
            if not isinstance(values, list) or any(not isinstance(x, str) or not x.strip() for x in values):
                raise ValueError("Noise rules must contain nonempty strings")
            destination = rules["keep"] if name == "keep" else rules["drop"][name]
            destination.extend(values)
    return rules


def decide(text, rules):
    value = key(text)
    if value in {key(x) for x in rules["keep"]}:
        return "keep", None, "noise_keep_exception"
    if not text.strip():
        return "exclude", "unknown", "empty_ocr"
    if not value:
        return "review", None, "possible_noise_requires_review"
    for kind, values in rules["drop"].items():
        if value in {key(x) for x in values}:
            return "exclude", kind, "whole_box_" + kind
    # Only entire boxes match; never cut a clause out of meaningful speech.
    if re.fullmatch(r"ha+h{2,}", value):
        return "exclude", "sfx", "whole_box_breath_sound"
    if re.fullmatch(r"(?:scanlated by|scanlation credits)\s+.+", value):
        return "exclude", "watermark", "whole_box_credit"
    if re.fullmatch(r"read more at\s+(?:(?:https?://|www\.)\S+|[a-z0-9][a-z0-9.-]+\.[a-z]{2,}(?:/\S*)?)", value):
        return "exclude", "watermark", "whole_box_credit_url"
    if re.fullmatch(r"read more at\s+.+", value):
        return "review", None, "possible_credit_requires_review"
    if (re.fullmatch(r"[\W_]+", text.strip()) or re.search(r"([a-z])\1{5,}", value)
            or re.fullmatch(r"\S*(?:https?://|www\.)\S+", value)):
        return "review", None, "possible_noise_requires_review"
    return "keep", None, None
