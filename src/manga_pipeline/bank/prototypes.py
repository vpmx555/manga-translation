"""Compact prototypes from at most sixteen nearest crop embeddings."""

import numpy as np


STRATEGY = "closest-2-to-16-paired-mean-v2"


def normalized(values):
    values = np.asarray(values, dtype=np.float32)
    if values.ndim != 2 or not len(values) or not np.isfinite(values).all():
        raise ValueError("Prototype embeddings must be a nonempty finite matrix")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if np.any(norms <= 1e-12):
        raise ValueError("Prototype embeddings must have nonzero norm")
    return values / norms


def closest_indices(values, count):
    """Choose the most compact nearest-neighbor group, with stable tie breaks."""
    values = normalized(values)
    count = min(len(values), max(1, int(count)))
    distances = np.linalg.norm(values[:, None, :] - values[None, :, :], axis=2)
    best = None
    for center in range(len(values)):
        group = np.argsort(distances[center], kind="stable")[:count]
        # Mean pairwise distance evaluates the whole group, not only its center.
        score = float(distances[np.ix_(group, group)].mean())
        key = (score, tuple(sorted(int(i) for i in group)))
        if best is None or key < best[0]:
            best = (key, group)
    return sorted(int(i) for i in best[1])


def consensus_mean(values):
    """Mean up to sixteen closest vectors, then L2 normalize.

    With only one or two observations there is no reliable outlier evidence;
    retain both rather than arbitrarily declaring either one low quality.
    """
    values = normalized(values)
    count = min(len(values), 16)
    indices = closest_indices(values, count)
    mean = values[indices].mean(axis=0)
    norm = float(np.linalg.norm(mean))
    if norm <= 1e-12:
        # Opposing vectors have no usable mean; choose a deterministic member.
        return values[indices[0]].copy()
    return mean / norm
