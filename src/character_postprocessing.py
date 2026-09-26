"""Extension points for optional character-match post-processing.

The default pipeline intentionally depends only on MAGI embeddings.  A future
CAFormer integration can implement :class:`AmbiguousMatchPostprocessor` and is
called only when the MAGI match is below the configured confidence/margin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

import numpy as np


@dataclass(frozen=True)
class MatchCandidate:
    character_id: int
    display_name: str
    magi_score: float
    crop_paths: tuple[str, ...]


@dataclass(frozen=True)
class AmbiguousMatch:
    crops: tuple[np.ndarray, ...]
    magi_embedding: np.ndarray
    candidates: tuple[MatchCandidate, ...]
    page_key: str


@dataclass(frozen=True)
class PostprocessDecision:
    """A conservative decision returned by an auxiliary model.

    ``character_id=None`` preserves ``<Other>``.  ``confidence`` is local to
    the plugin and is recorded for auditing; it is not mixed with MAGI scores.
    """

    character_id: int | None
    confidence: float
    reason: str
    metadata: Mapping[str, object] | None = None


class AmbiguousMatchPostprocessor(Protocol):
    name: str
    version: str

    def resolve(self, match: AmbiguousMatch) -> PostprocessDecision | None:
        """Resolve or veto an ambiguous MAGI match.

        Implementations should only select IDs present in ``match.candidates``.
        Returning ``None`` delegates to the default ``<Other>`` behavior.
        """


class NoOpPostprocessor:
    name = "none"
    version = "1"

    def resolve(self, match: AmbiguousMatch) -> PostprocessDecision | None:
        return None


def postprocessor_manifest(
    postprocessors: Sequence[AmbiguousMatchPostprocessor],
) -> list[dict[str, str]]:
    return [
        {"name": processor.name, "version": processor.version}
        for processor in postprocessors
    ]
