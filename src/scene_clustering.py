"""Adjacent agglomerative scene proposals. NumPy core; optional model backends.

No model/threshold is certified for manga. Similarity scores are not probabilities.
"""
from __future__ import annotations

import copy
import hashlib
import heapq
import json
import math
import time
from pathlib import Path

import numpy as np

from dialogue_data import validate_normalized


MODEL_SPECS = {
    "mpnet": {"model": "sentence-transformers/all-mpnet-base-v2", "prompt": ""},
    "embeddinggemma": {"model": "google/embeddinggemma-300M", "prompt": "task: clustering | query: "},
    "qwen3": {"model": "Qwen/Qwen3-Embedding-0.6B", "prompt":
              "Instruct: Represent this manga dialogue passage for clustering by narrative context and events.\nQuery: "},
}


def unit_vectors(vectors, count: int | None = None) -> np.ndarray:
    values = np.asarray(vectors, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] == 0 or (count is not None and len(values) != count):
        raise ValueError("Embedding matrix must align with utterances and have nonzero dimension")
    if not np.isfinite(values).all():
        raise ValueError("Embeddings contain nonfinite values")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if not np.isfinite(norms).all() or (norms <= 1e-12).any():
        raise ValueError("Zero or numerically overflowing embedding vectors are not meaningful")
    return values / norms


def embed_dialogue(document: dict, *, backend: str, cache_dir: Path | None = None,
                   revision: str | None = None, device: str = "cpu", batch_size: int = 16,
                   local_only: bool = False, model_cache_dir: Path | None = None) -> tuple[np.ndarray, dict]:
    validate_normalized(document)
    if backend not in MODEL_SPECS or batch_size <= 0:
        raise ValueError("Invalid embedding backend or batch size")
    rows = document["utterances"]
    if not rows:
        raise ValueError("Cannot embed an empty dialogue")
    spec = MODEL_SPECS[backend]
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError("Install requirements-scenes.txt in a separate scene environment; see docs/pipeline_usage.md") from exc
    started = time.perf_counter()
    try:
        model = SentenceTransformer(spec["model"], revision=revision, device=device,
                                    local_files_only=local_only,
                                    cache_folder=str(model_cache_dir) if model_cache_dir else None)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot load {spec['model']}: {exc}. EmbeddingGemma requires accepted HF terms/authentication.") from exc
    first = model[0]
    load_seconds = time.perf_counter() - started
    config = getattr(getattr(first, "auto_model", None), "config", None)
    resolved = getattr(config, "_commit_hash", None) or revision
    texts = [row["text"] for row in rows]
    prompt = spec["prompt"]
    # Prevent tokenizer truncation from quietly hiding the end of an utterance.
    limit = model.max_seq_length
    for row, text in zip(rows, texts):
        tokens = model.tokenizer(prompt + text, truncation=False, add_special_tokens=True)["input_ids"]
        if len(tokens) > limit:
            raise ValueError(f"Utterance {row['id']} exceeds {backend}'s {limit}-token input; split/review the source")
    identity = {"backend": backend, "model": spec["model"], "revision": resolved,
                "prompt": prompt, "preprocessing": "normalized_text_only_v1",
                "max_seq_length": limit, "texts": texts}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    target = cache_dir / f"{key}.npz" if cache_dir and resolved else None
    cached = False
    encode_seconds = None
    if target and target.exists():
        with np.load(target, allow_pickle=False) as stored:
            vectors = unit_vectors(stored["vectors"], len(rows))
        cached = True
    else:
        encode_started = time.perf_counter()
        encoded = model.encode(texts, prompt=prompt, normalize_embeddings=True,
                               batch_size=batch_size, show_progress_bar=False)
        encode_seconds = time.perf_counter() - encode_started
        vectors = unit_vectors(encoded, len(rows))
        if target:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                np.savez_compressed(handle, vectors=vectors)
    metadata = {k: v for k, v in identity.items() if k != "texts"}
    metadata.update({"cache_key": key, "cache_hit": cached,
                     "load_seconds": load_seconds, "encode_seconds": encode_seconds,
                     "elapsed_seconds": time.perf_counter() - started,
                     "cache_disabled_reason": None if resolved else "model_revision_unresolved"})
    return vectors, metadata


def adjacent_clusters(vectors, *, distance_threshold: float = 0.45,
                      cohesion_threshold: float = 0.8) -> list[tuple[int, int]]:
    """Average linkage over ALL cross-cluster pairs, constrained to a chain.

    Also constrain the maximum within-cluster cosine distance to prevent chaining.
    Both thresholds are uncalibrated starting values, not universal scene rules.
    Returns half-open [start,end) ranges covering the input exactly once.
    """
    if not 0 <= distance_threshold <= 2 or not 0 <= cohesion_threshold <= 2:
        raise ValueError("Cosine distance thresholds must be in [0,2]")
    values = unit_vectors(vectors)
    n = len(values)
    if n == 0:
        return []
    distances = np.clip(1 - values @ values.T, 0, 2)
    np.fill_diagonal(distances, 0)
    prefix = np.pad(distances.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    nodes = {i: {"start": i, "end": i + 1, "left": i - 1 if i else None,
                 "right": i + 1 if i + 1 < n else None, "max_distance": 0.0, "active": True}
             for i in range(n)}
    heap = []

    def push(left_id, right_id):
        if left_id is None or right_id is None:
            return
        left, right = nodes[left_id], nodes[right_id]
        a, b, c, d = left["start"], left["end"], right["start"], right["end"]
        total = prefix[b, d] - prefix[a, d] - prefix[b, c] + prefix[a, c]
        distance = float(total / ((b - a) * (d - c)))
        heapq.heappush(heap, (distance, a, left_id, right_id))

    for i in range(n - 1):
        push(i, i + 1)
    next_id = n
    while heap:
        distance, _, left_id, right_id = heapq.heappop(heap)
        left, right = nodes[left_id], nodes[right_id]
        if not left["active"] or not right["active"] or left["right"] != right_id:
            continue
        if distance > distance_threshold:
            break
        max_distance = max(left["max_distance"], right["max_distance"],
                           float(distances[left["start"]:left["end"], right["start"]:right["end"]].max()))
        if max_distance > cohesion_threshold:
            continue
        before, after = left["left"], right["right"]
        left["active"] = right["active"] = False
        nodes[next_id] = {"start": left["start"], "end": right["end"], "left": before,
                          "right": after, "max_distance": max_distance, "active": True}
        if before is not None:
            nodes[before]["right"] = next_id
        if after is not None:
            nodes[after]["left"] = next_id
        push(before, next_id)
        push(next_id, after)
        next_id += 1
    return sorted((node["start"], node["end"]) for node in nodes.values() if node["active"])


def cluster_document(document: dict, vectors, *, distance_threshold: float = 0.45,
                     cohesion_threshold: float = 0.8, boundary_window: int = 2,
                     ambiguity_margin: float = 0.08, embedding_metadata: dict | None = None) -> dict:
    validate_normalized(document)
    if boundary_window < 1 or not math.isfinite(ambiguity_margin) or ambiguity_margin < 0:
        raise ValueError("Invalid boundary window/margin")
    rows = document["utterances"]
    values = unit_vectors(vectors, len(rows))
    ranges = adjacent_clusters(values, distance_threshold=distance_threshold,
                               cohesion_threshold=cohesion_threshold)
    output = copy.deepcopy(document)
    cuts = {start for start, _ in ranges if start > 0}
    for number, (start, end) in enumerate(ranges, start=1):
        scene_id = f"{document['document_id']}:scene{number}"
        for order, row in enumerate(output["utterances"][start:end], start=1):
            row.update({"scene_id": scene_id, "scene_status": "proposed", "order_in_scene": order})
    boundaries = []
    for index in range(1, len(rows)):
        left = values[max(0, index - boundary_window):index].mean(0)
        right = values[index:min(len(rows), index + boundary_window)].mean(0)
        norm = np.linalg.norm(left) * np.linalg.norm(right)
        distance = float(np.clip(1 - left @ right / norm, 0, 2)) if norm > 1e-12 else None
        short = min(len(rows[index - 1]["text"].split()), len(rows[index]["text"].split())) < 3
        ambiguous = distance is None or short or abs(distance - distance_threshold) <= ambiguity_margin
        boundaries.append({"before_utterance_id": rows[index]["id"], "position": index,
                           "proposed_boundary": index in cuts, "window_cosine_distance": distance,
                           "review_status": "needs_review" if ambiguous else "proposed",
                           "reasons": ["short_dialogue"] if short else (["weak_boundary_evidence"] if ambiguous else []),
                           "score_is_probability": False})
    output["scene_boundaries"] = boundaries
    output["scene_config"] = {"algorithm": "adjacent_average_linkage_with_complete_cohesion_guard_v1",
                              "distance_threshold": distance_threshold, "cohesion_threshold": cohesion_threshold,
                              "boundary_window": boundary_window, "ambiguity_margin": ambiguity_margin,
                              "embedding": embedding_metadata, "calibration_status": "uncalibrated",
                              "translation_context_policy": "preserve_nearby_turns_across_uncertain_boundaries"}
    return output
