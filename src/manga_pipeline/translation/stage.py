"""Immutable translation revisions, replayable responses and a mutable manual layer."""
import copy
from pathlib import Path

from ..progress import NullProgress
from ..storage.io import atomic_json, file_hash, fingerprint, read_json
from ..storage.locking import exclusive_lock
from . import contract

DEFAULT_CONFIG = {"policy": contract.POLICY, "batch_size": 8, "context_radius": 2,
                  "num_predict": 1536, "glossary_path": None, "overrides_path": None, "instructions": ""}


def validate_config(config):
    if not isinstance(config, dict) or set(config) != set(DEFAULT_CONFIG) or config["policy"] != contract.POLICY:
        raise ValueError("Unsupported translation configuration")
    for key, maximum in (("batch_size", 8), ("context_radius", 10), ("num_predict", 16384)):
        if type(config[key]) is not int or not (0 if key == "context_radius" else 1) <= config[key] <= maximum:
            raise ValueError("Invalid translation " + key)
    if not isinstance(config["instructions"], str):
        raise ValueError("Translation instructions must be a string")
    for key in ("glossary_path", "overrides_path"):
        if config[key] is not None and not isinstance(config[key], str):
            raise ValueError("Translation paths must be strings or null")


def glossary_for(path):
    if path is None or not Path(path).is_file():
        return []
    data = read_json(path)
    if not isinstance(data, dict) or set(data) - {"entries"} or not isinstance(data.get("entries"), list):
        raise ValueError("Glossary requires entries array")
    result, seen = [], set()
    for entry in data["entries"]:
        if (not isinstance(entry, dict) or type(entry.get("confirmed")) is not bool
                or any(not isinstance(entry.get(k), str) or not entry[k].strip() for k in ("source", "vi"))):
            raise ValueError("Invalid glossary entry")
        if not entry["confirmed"]:
            continue
        if entry["source"].casefold() in seen:
            raise ValueError("Duplicate confirmed glossary source term")
        seen.add(entry["source"].casefold())
        result.append({"source": entry["source"], "vi": entry["vi"]})
    return result


def overrides_for(path, ids):
    if path is None or not Path(path).is_file():
        return {style: {} for style in contract.STYLES}
    data = read_json(path)
    if not isinstance(data, dict) or set(data) - set(contract.STYLES):
        raise ValueError("Unknown manual translation style")
    for style, values in data.items():
        if (not isinstance(values, dict) or set(values) - ids
                or any(not isinstance(x, str) or not x.strip() for x in values.values())):
            raise ValueError("Manual translations require known IDs and nonempty strings")
    return {style: data.get(style, {}) for style in contract.STYLES}
