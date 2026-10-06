"""Optional extension boundary; no character profiles or graph are loaded by default."""
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class ContextQuery:
    story_id: str
    chapter_id: str
    source_hash: str
    targets: tuple[Mapping[str, Any], ...]
    character_ids: tuple[int, ...]


class TranslationContextProvider(Protocol):
    def get_context(self, query: ContextQuery) -> Mapping[str, Any]:
        """Return facts keyed by target ID, e.g. character nodes and relationship/event edges.

        Target IDs/source_position (page and original text index) bound story time. Future graph
        adapters must not expose future events as established facts. Nodes use
        stable character IDs; others/narrator/groups are not character nodes.
        The caller snapshots this response before inference and hashes it.
        """
        ...
