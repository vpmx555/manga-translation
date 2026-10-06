"""Read-only adapter for legacy and current exports; no metadata inference."""
import copy
import re


def source_order(row):
    source = row["source"]
    index = source.get("text_index")
    if type(index) is not int:
        # Old translation_only rows lost text_index but MAGI IDs retain it.
        match = re.search(r":t(\d+)(?::u)?$", row["id"])
        index = int(match[1]) if match else source.get("reading_order")
    return (source.get("page") or 0, index if type(index) is int else 0, row["id"])


def targets(document):
    if not isinstance(document, dict) or document.get("kind") != "structured_dialogue":
        raise ValueError("Translation requires a structured_dialogue export")
    rows = []
    for bucket in ("utterances", "translation_only"):
        values = document.get(bucket, [])
        if not isinstance(values, list):
            raise ValueError("Invalid exported text array")
        for row in values:
            if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                raise ValueError("Exported text requires a string")
            identity = row.get("id") if bucket == "utterances" else row.get("source_text_id")
            if not isinstance(identity, str) or not identity:
                raise ValueError("Exported text requires a stable source ID")
            rows.append({"id": identity, "bucket": bucket, "source": copy.deepcopy(row)})
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate exported text IDs")
    rows.sort(key=source_order)
    return rows


def view(row):
    source = row["source"]
    return {"id": row["id"], "bucket": row["bucket"], "text": source["text"],
            **{key: copy.deepcopy(source[key]) for key in ("page", "reading_order", "content_type",
               "speaker_id", "speaker_name", "addressee_ids", "addressee_type", "mentions",
               "content_review_status", "normalization_reasons") if key in source}}
