"""Readable style-specific reviews, including failures and unconfirmed proposals."""
from ..storage.io import atomic_bytes


def cell(value):
    return str(value).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def write_reviews(directory, output):
    for style, rows in output["styles"].items():
        parts = [f"# Vietnamese translation — {style}\n",
            "Source speaker/addressee metadata is authoritative. Profiles/relationship graphs are not enabled.\n",
            "Manual entries are locked. Glossary proposals below are unconfirmed.\n",
            "| ID / page | Source | Vietnamese | Speaker → listeners | Status / review |",
            "| --- | --- | --- | --- | --- |"]
        for row in rows:
            source = row["source"]
            status = row["status"]
            if row.get("needs_review"):
                status += ": " + row.get("review_reason", "")
            if row.get("error"):
                status += ": " + row["error"]
            parts.append("| " + " | ".join(cell(x) for x in [row["id"] + f" / P{source.get('page', '?')}",
                source["text"], row.get("translation", ""),
                f"{source.get('speaker_id')} → {source.get('addressee_ids', [])}", status]) + " |")
        proposals = [(row["id"], item) for row in rows for item in row.get("glossary_proposals", [])]
        if proposals:
            parts.extend(["\n## Unconfirmed glossary proposals\n", "| ID | Source term | Proposed Vietnamese |",
                          "| --- | --- | --- |"])
            parts.extend("| " + " | ".join(cell(x) for x in [identity, item["source"], item["vi"]]) + " |"
                         for identity, item in proposals)
        atomic_bytes(directory / f"review_{style}.md", ("\n".join(parts) + "\n").encode("utf-8"))
