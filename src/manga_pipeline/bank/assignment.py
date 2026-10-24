"""Dynamic, persistent character assignment for MAGI v2."""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from types import MethodType
from typing import Iterable, Sequence

import numpy as np
import torch
from PIL import Image

from .identity import CharacterBank
from .prototypes import closest_indices, consensus_mean
from ..extraction.config import DETECTION_DEFAULTS
from .postprocessing import (
    AmbiguousMatch,
    AmbiguousMatchPostprocessor,
    MatchCandidate,
    postprocessor_manifest,
)


@dataclass
class _Observation:
    page_index: int
    page_key: str
    cluster_label: int
    detection_indices: list[int]
    global_indices: list[int]
    embeddings: np.ndarray
    representative: np.ndarray
    crops: tuple[np.ndarray, ...]
    panel_ids: set[int | None]
    source_keys: tuple[str, ...] = ()


def _normalize_rows(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if values.size == 0:
        return values
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    return values / np.maximum(norms, 1e-12)


def _crop(image: np.ndarray, bbox: Sequence[float]) -> np.ndarray:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = (int(round(float(value))) for value in bbox[:4])
    x1, x2 = sorted((max(0, min(width, x1)), max(0, min(width, x2))))
    y1, y2 = sorted((max(0, min(height, y1)), max(0, min(height, y2))))
    if x2 <= x1 or y2 <= y1:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    return np.asarray(image[y1:y2, x1:x2]).copy()


def _panel_for_bbox(bbox: Sequence[float], panels: Sequence[Sequence[float]]) -> int | None:
    center_x = (float(bbox[0]) + float(bbox[2])) / 2.0
    center_y = (float(bbox[1]) + float(bbox[3])) / 2.0
    containing: list[tuple[float, int]] = []
    for index, panel in enumerate(panels):
        x1, y1, x2, y2 = map(float, panel[:4])
        if x1 <= center_x <= x2 and y1 <= center_y <= y2:
            containing.append((max(0.0, (x2 - x1) * (y2 - y1)), index))
    return min(containing)[1] if containing else None


def _aggregate_distance(query: np.ndarray, prototypes: np.ndarray) -> float:
    return float(np.linalg.norm(query - consensus_mean(prototypes)))


def _diverse_indices(embeddings: np.ndarray, maximum: int) -> list[int]:
    # Kept as a compatibility alias for diagnostic callers.
    return closest_indices(embeddings, maximum)


class DynamicCharacterAssigner:
    def __init__(
        self,
        bank: CharacterBank,
        page_keys: Sequence[str],
        postprocessors: Sequence[AmbiguousMatchPostprocessor] = (),
        panel_assignment: str = "legacy",
    ) -> None:
        self.bank = bank
        self.page_keys = list(page_keys)
        self.postprocessors = tuple(postprocessors)
        if panel_assignment not in {"legacy", "overlap"}:
            raise ValueError("Unsupported panel assignment rule")
        self.panel_assignment = panel_assignment
        self.bank.metadata["postprocessors"] = postprocessor_manifest(self.postprocessors)
        self._current_pending_occurrences: dict[str, list[int]] = {}
        # Stable bank identity is separate from the editable display label.
        self.last_character_ids: list[int | None] = []
        self.last_assignment_trace: list[dict] = []

    def bind(self, model) -> None:
        """Bind the override to one final model instance, never the global class."""

        assigner = self

        def override(
            model_self,
            images,
            character_bboxes,
            character_bank,
            character_clusters,
            eta=0.75,
        ):
            return assigner.assign_names_to_characters(
                model_self,
                images,
                character_bboxes,
                character_bank,
                character_clusters,
                eta=eta,
            )

        model.assign_names_to_characters = MethodType(override, model)

    def _build_observations(
        self,
        images: Sequence[np.ndarray],
        bboxes_by_page: Sequence[Sequence[Sequence[float]]],
        clusters_by_page: Sequence[Sequence[int]],
        embeddings_by_page: Sequence[np.ndarray],
        panels_by_page: Sequence[Sequence[Sequence[float]]],
    ) -> list[_Observation]:
        observations: list[_Observation] = []
        global_offset = 0
        for page_index, (image, bboxes, labels, embeddings) in enumerate(
            zip(images, bboxes_by_page, clusters_by_page, embeddings_by_page)
        ):
            image_digest = hashlib.sha256(np.asarray(image).tobytes()).digest()
            grouped: dict[int, list[int]] = {}
            for detection_index, label in enumerate(labels):
                grouped.setdefault(int(label), []).append(detection_index)
            panels = panels_by_page[page_index] if page_index < len(panels_by_page) else []
            for label, indices in grouped.items():
                group_embeddings = _normalize_rows(embeddings[indices])
                representative = consensus_mean(group_embeddings)
                crops = tuple(_crop(image, bboxes[index]) for index in indices)
                observations.append(
                    _Observation(
                        page_index=page_index,
                        page_key=self.page_keys[page_index],
                        cluster_label=label,
                        detection_indices=indices,
                        global_indices=[global_offset + index for index in indices],
                        embeddings=group_embeddings,
                        representative=representative,
                        crops=crops,
                        panel_ids={self._panel_for_character(bboxes[index], panels) for index in indices},
                        source_keys=tuple(hashlib.sha256(self.page_keys[page_index].encode('utf-8') + image_digest
                            + np.asarray(bboxes[index], dtype=np.float32).tobytes()).hexdigest() for index in indices),
                    )
                )
            global_offset += len(bboxes)
        return observations

    def _panel_for_character(self, bbox, panels):
        if self.panel_assignment == "overlap":
            from ..extraction.document import panel_for_box
            return panel_for_box(bbox, panels)
        return _panel_for_bbox(bbox, panels)

    def _candidate_scores(
        self,
        observation: _Observation,
        blocked_ids: set[int],
    ) -> list[tuple[int, float]]:
        scored: list[tuple[int, float]] = []
        for key, record in self.bank.characters.items():
            character_id = int(key)
            if record.get("disabled") or character_id in blocked_ids:
                continue
            prototypes = self.bank.get_character_embeddings(character_id)
            if prototypes.size:
                scored.append(
                    (character_id, _aggregate_distance(observation.representative, prototypes))
                )
        return sorted(scored, key=lambda item: (item[1], item[0]))

    def _resolve_existing(
        self,
        observation: _Observation,
        blocked_ids: set[int],
    ) -> int | None:
        return self._resolve_scores(observation, self._candidate_scores(observation, blocked_ids))

    def _resolve_scores(self, observation, scores):
        if not scores:
            return None
        best_id, best_score = scores[0]
        second_score = scores[1][1] if len(scores) > 1 else math.inf
        config = self.bank.config
        if (
            best_score <= float(config["max_distance"])
            and second_score - best_score >= float(config["min_margin"])
        ):
            return best_id

        candidates = tuple(
            MatchCandidate(
                character_id=character_id,
                display_name=self.bank.display_label(character_id),
                magi_score=score,
                crop_paths=tuple(
                    str(path)
                    for path in self.bank.characters[str(character_id)].get("crop_paths", [])
                ),
            )
            for character_id, score in scores[:5]
        )
        ambiguous = AmbiguousMatch(
            crops=observation.crops,
            magi_embedding=observation.representative,
            candidates=candidates,
            page_key=observation.page_key,
        )
        allowed_ids = {candidate.character_id for candidate in candidates}
        for processor in self.postprocessors:
            decision = processor.resolve(ambiguous)
            if decision is None:
                continue
            if decision.character_id is None:
                return None
            if decision.character_id not in allowed_ids:
                raise ValueError(
                    f"Postprocessor {processor.name!r} selected an ID outside its candidates"
                )
            return decision.character_id
        return None

    def _plan_page(self, observations):
        """Freeze page scores, then reserve IDs from strongest to weakest match.

        A cluster can span several panels: reserve all its panels together.
        Prototype updates and new IDs become evidence for the next page only.
        """
        scores = [self._candidate_scores(observation, set()) for observation in observations]
        appearances = {int(identity): int(record.get('appearance_count', 0))
                       for identity, record in self.bank.characters.items()}
        remaining = set(range(len(observations)))
        used = {}
        plan = []

        def available(index):
            blocked = set()
            for panel in observations[index].panel_ids:
                blocked.update(used.get(panel, set()))
            return [(identity, score) for identity, score in scores[index] if identity not in blocked]

        def reserve(index, identity):
            observation = observations[index]
            self.last_assignment_trace.append({
                'page': observation.page_index + 1,
                'detections': [i + 1 for i in observation.detection_indices],
                'global_indices': list(observation.global_indices),
                'panels': sorted(observation.panel_ids, key=str),
                'all_candidates': scores[index],
                'available_candidates': available(index),
                'selected_existing_id': identity,
                'page_assignment_rank': len(plan) + 1,
                'selected_id_prior_appearances': appearances.get(identity, 0),
            })
            remaining.remove(index)
            plan.append((observations[index], identity))
            if identity is not None:
                for panel in observations[index].panel_ids:
                    used.setdefault(panel, set()).add(identity)

        while remaining:
            proposals = []
            for index in sorted(remaining):
                candidates = available(index)
                if not candidates:
                    continue
                identity, score = candidates[0]
                margin = candidates[1][1] - score if len(candidates) > 1 else math.inf
                if score <= float(self.bank.config['max_distance']) and margin >= float(self.bank.config['min_margin']):
                    proposals.append((-appearances.get(identity, 0), score, index, identity))
            if not proposals:
                break
            _, _, index, identity = min(proposals)
            reserve(index, identity)

        # Optional ambiguity processors only handle observations without an
        # automatic match, after the strong automatic reservations are fixed.
        while remaining:
            index = min(remaining, key=lambda i: (available(i)[0][1] if available(i) else math.inf, i))
            reserve(index, self._resolve_scores(observations[index], available(index)))
        return plan

    def _planned_observations(self, observations):
        for _, page in groupby(observations, key=lambda observation: observation.page_index):
            yield from self._plan_page(list(page))

    def _record_appearance(self, character_id, observation):
        record = self.bank.characters[str(character_id)]
        previous = set(record.get('appearance_keys', []))
        added = set(observation.source_keys) - previous
        if added:
            record['appearance_count'] = int(record.get('appearance_count', len(previous))) + len(added)
            record['appearance_keys'] = sorted(previous | added)
            self.bank.dirty = True

    def _match_pending(self, observation: _Observation) -> str | None:
        scored: list[tuple[str, float]] = []
        maximum_gap = int(self.bank.config.get("pending_max_page_gap", 1))
        for pending_id, record in self.bank.pending.items():
            origin = record.get("origin_page_key") or next(iter(record.get("page_keys", [])), None)
            if origin not in self.page_keys:
                record["state"] = "closed"
                self.bank.dirty = True
                continue
            distance = observation.page_index - self.page_keys.index(origin)
            if record.get("state") == "closed" or not 1 <= distance <= maximum_gap:
                if distance > maximum_gap:
                    record["state"] = "closed"
                    self.bank.dirty = True
                continue
            if observation.page_key in record.get("page_keys", []):
                continue
            embeddings = self.bank.get_pending_embeddings(pending_id)
            if embeddings.size:
                scored.append(
                    (pending_id, _aggregate_distance(observation.representative, embeddings))
                )
        scored.sort(key=lambda item: (item[1], item[0]))
        if not scored:
            return None
        best_id, best_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else math.inf
        if (
            best_score <= float(self.bank.config["max_distance"])
            and second_score - best_score >= float(self.bank.config["min_margin"])
        ):
            return best_id
        return None

    def _save_crops(self, crops: Iterable[np.ndarray], prefix: str) -> list[str]:
        paths: list[str] = []
        maximum = int(self.bank.config["max_crops_per_character"])
        for crop in crops:
            if len(paths) >= maximum:
                break
            digest = hashlib.sha1(np.asarray(crop).tobytes()).hexdigest()[:12]
            relative = Path("crops") / f"{prefix}-{digest}.png"
            destination = self.bank.directory / relative
            if relative.as_posix() in paths:
                continue
            if not destination.exists():
                Image.fromarray(np.asarray(crop).astype(np.uint8)).save(destination)
            paths.append(relative.as_posix())
        return paths

    def _add_prototypes(self, character_id: int, observation: _Observation) -> None:
        record = self.bank.characters[str(character_id)]
        key = self.bank.embedding_key_for_character(character_id)
        raw = self.bank.arrays[key]
        paths = record.get('prototype_crop_paths', [None]*len(raw))
        source_keys = record.get('prototype_source_keys', [None]*len(raw))
        if len(paths) != len(raw) or len(source_keys) != len(raw):
            raise ValueError('Prototype/crop mapping length mismatch')
        disabled = set(record.get('disabled_prototype_indices', []))
        pairs = [(vector, path, source) for i, (vector, path, source) in enumerate(zip(raw, paths, source_keys)) if i not in disabled]
        known = {source for _, _, source in pairs if source is not None}
        for i, (vector, crop) in enumerate(zip(observation.embeddings, observation.crops)):
            source = observation.source_keys[i] if observation.source_keys else None
            if source is not None and source in known:
                continue
            saved = self._save_crops([crop], f'character-{character_id}')
            path = saved[0] if saved else None
            if source is not None:
                known.add(source)
            pairs.append((vector, path, source))
        # Never invent a crop for old, unmapped vectors. Once there are two
        # genuine paired samples, rebuild that legacy support from those pairs.
        paired = [(vector, path, source) for vector, path, source in pairs if path is not None]
        if len(paired) >= 2:
            pairs = paired
        if not pairs:
            return
        combined = _normalize_rows(np.vstack([vector for vector, _, _ in pairs]))
        chosen = closest_indices(combined, int(self.bank.config['max_embeddings_per_character']))
        self.bank.arrays[key] = combined[chosen]
        record['prototype_crop_paths'] = [pairs[i][1] for i in chosen]
        record['prototype_source_keys'] = [pairs[i][2] for i in chosen]
        record['crop_paths'] = [pairs[i][1] for i in chosen if pairs[i][1] is not None]
        record["disabled_prototype_indices"] = []
        self.bank.dirty = True

    def assign_names_to_characters(
        self,
        model,
        images,
        character_bboxes,
        character_bank,
        character_clusters,
        eta=0.75,
    ) -> list[str]:
        del character_bank, eta  # Persistent bank/config replace the upstream arguments.
        total = sum(len(bboxes) for bboxes in character_bboxes)
        self.last_character_ids = [None] * total
        self.last_assignment_trace = []
        if total == 0:
            return []
        if len(self.page_keys) != len(images):
            raise ValueError("page_keys must be aligned with chapter images")

        tensor_embeddings = model.predict_crop_embeddings(images, character_bboxes)
        embeddings_by_page = [
            _normalize_rows(value.detach().cpu().numpy()) for value in tensor_embeddings
        ]
        panels_by_page = getattr(model, "_character_assignment_panels", [])
        observations = self._build_observations(
            images,
            character_bboxes,
            character_clusters,
            embeddings_by_page,
            panels_by_page,
        )
        labels = ["Other"] * total
        for observation, character_id in self._planned_observations(observations):
            if character_id is None:
                pending_id = self._match_pending(observation)
                if pending_id is not None:
                    character_id = self.bank.promote_pending(pending_id)
                    for previous_index in self._current_pending_occurrences.pop(pending_id, []):
                        labels[previous_index] = self.bank.display_label(character_id)
                        self.last_character_ids[previous_index] = character_id
                    self._add_prototypes(character_id, observation)
                elif len(observation.detection_indices) >= 2:
                    prefix = f"new-{uuid.uuid4().hex[:8]}"
                    chosen = _diverse_indices(
                        observation.embeddings,
                        int(self.bank.config["max_embeddings_per_character"]),
                    )
                    paths = [self._save_crops([observation.crops[i]], prefix)[0] for i in chosen]
                    character_id = self.bank.create_character(
                        observation.embeddings[chosen], paths
                    )
                    self.bank.characters[str(character_id)]['prototype_source_keys'] = [observation.source_keys[i] for i in chosen]
                else:
                    pending_id = uuid.uuid4().hex
                    paths = self._save_crops(observation.crops, f"pending-{pending_id[:8]}")
                    self.bank.create_pending(
                        pending_id,
                        observation.embeddings,
                        observation.page_key,
                        paths,
                    )
                    self.bank.pending[pending_id].update({
                        "origin_page_key": observation.page_key, "state": "active",
                        "appearance_keys": list(observation.source_keys),
                        "prototype_source_keys": list(observation.source_keys),
                    })
                    self._current_pending_occurrences[pending_id] = list(
                        observation.global_indices
                    )
            else:
                self._add_prototypes(character_id, observation)

            if character_id is None:
                continue
            self._record_appearance(character_id, observation)
            label = self.bank.display_label(character_id)
            for global_index in observation.global_indices:
                labels[global_index] = label
                self.last_character_ids[global_index] = character_id
        for record in self.bank.pending.values():
            origin = record.get("origin_page_key") or next(iter(record.get("page_keys", [])), None)
            if origin not in self.page_keys or len(self.page_keys) - 1 - self.page_keys.index(origin) > int(self.bank.config.get("pending_max_page_gap", 1)):
                record["state"] = "closed"
        self.bank.dirty = True
        for record in self.last_assignment_trace:
            record['final_id'] = self.last_character_ids[record['global_indices'][0]]
        return labels


def install_detection_patch(model, text_detection_threshold: float = DETECTION_DEFAULTS["text_detection_threshold"],
                            character_character_matching_threshold: float = DETECTION_DEFAULTS["character_character_matching_threshold"],
                            *, character_detection_threshold: float = DETECTION_DEFAULTS["character_detection_threshold"],
                            text_character_matching_threshold: float = DETECTION_DEFAULTS["text_character_matching_threshold"],
                            text_classification_threshold: float = DETECTION_DEFAULTS["text_classification_threshold"]) -> None:
    """Set extraction thresholds and retain panels for panel-aware constraints."""

    original = model.predict_detections_and_associations

    def patched(model_self, images, *args, **kwargs):
        kwargs.setdefault("text_detection_threshold", text_detection_threshold)
        kwargs.setdefault("character_character_matching_threshold", character_character_matching_threshold)
        kwargs.setdefault("character_detection_threshold", character_detection_threshold)
        kwargs.setdefault("text_character_matching_threshold", text_character_matching_threshold)
        kwargs.setdefault("text_classification_threshold", text_classification_threshold)
        results = original(images, *args, **kwargs)
        panel_buffer = getattr(model_self, "_character_assignment_panels", None)
        if panel_buffer is None:
            panel_buffer = []
            model_self._character_assignment_panels = panel_buffer
        panel_buffer.extend(result.get("panels", []) for result in results)
        return results

    model.predict_detections_and_associations = MethodType(patched, model)


def reset_assignment_context(model) -> None:
    model._character_assignment_panels = []
