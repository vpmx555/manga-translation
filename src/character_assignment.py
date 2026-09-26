"""Dynamic, persistent character assignment for MAGI v2."""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import MethodType
from typing import Iterable, Sequence

import numpy as np
import torch
from PIL import Image

from character_bank import CharacterBank
from character_postprocessing import (
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
    prototypes = _normalize_rows(prototypes)
    distances = np.linalg.norm(prototypes - query[None, :], axis=1)
    nearest = np.sort(distances)[: min(3, len(distances))]
    return float(0.6 * nearest[0] + 0.4 * nearest.mean())


def _diverse_indices(embeddings: np.ndarray, maximum: int) -> list[int]:
    if len(embeddings) <= maximum:
        return list(range(len(embeddings)))
    embeddings = _normalize_rows(embeddings)
    selected = [0]
    remaining = set(range(1, len(embeddings)))
    while remaining and len(selected) < maximum:
        next_index = max(
            remaining,
            key=lambda index: min(
                float(np.linalg.norm(embeddings[index] - embeddings[chosen]))
                for chosen in selected
            ),
        )
        selected.append(next_index)
        remaining.remove(next_index)
    return selected


class DynamicCharacterAssigner:
    def __init__(
        self,
        bank: CharacterBank,
        page_keys: Sequence[str],
        postprocessors: Sequence[AmbiguousMatchPostprocessor] = (),
    ) -> None:
        self.bank = bank
        self.page_keys = list(page_keys)
        self.postprocessors = tuple(postprocessors)
        self.bank.metadata["postprocessors"] = postprocessor_manifest(self.postprocessors)
        self._current_pending_occurrences: dict[str, list[int]] = {}

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
            grouped: dict[int, list[int]] = {}
            for detection_index, label in enumerate(labels):
                grouped.setdefault(int(label), []).append(detection_index)
            panels = panels_by_page[page_index] if page_index < len(panels_by_page) else []
            for label, indices in grouped.items():
                group_embeddings = _normalize_rows(embeddings[indices])
                representative = _normalize_rows(group_embeddings.mean(axis=0, keepdims=True))[0]
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
                        panel_ids={_panel_for_bbox(bboxes[index], panels) for index in indices},
                    )
                )
            global_offset += len(bboxes)
        return observations

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
        scores = self._candidate_scores(observation, blocked_ids)
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

    def _match_pending(self, observation: _Observation) -> str | None:
        scored: list[tuple[str, float]] = []
        for pending_id, record in self.bank.pending.items():
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
        for index, crop in enumerate(crops):
            if index >= maximum:
                break
            digest = hashlib.sha1(np.asarray(crop).tobytes()).hexdigest()[:12]
            relative = Path("crops") / f"{prefix}-{digest}.png"
            destination = self.bank.directory / relative
            if not destination.exists():
                Image.fromarray(np.asarray(crop).astype(np.uint8)).save(destination)
            paths.append(relative.as_posix())
        return paths

    def _add_prototypes(self, character_id: int, observation: _Observation) -> None:
        current = self.bank.get_character_embeddings(character_id)
        additions: list[np.ndarray] = []
        for embedding in observation.embeddings:
            reference = current if not additions else np.vstack([current, additions])
            if not reference.size or np.linalg.norm(reference - embedding, axis=1).min() >= float(
                self.bank.config["prototype_min_separation"]
            ):
                additions.append(embedding)
        if not additions:
            return
        combined = np.vstack([current, additions]) if current.size else np.vstack(additions)
        chosen = _diverse_indices(
            combined, int(self.bank.config["max_embeddings_per_character"])
        )
        self.bank.arrays[self.bank.embedding_key_for_character(character_id)] = combined[chosen]
        record = self.bank.characters[str(character_id)]
        # Rewriting the compacted matrix invalidates old row indexes.
        record["disabled_prototype_indices"] = []
        crop_paths = list(record.get("crop_paths", []))
        if len(crop_paths) < int(self.bank.config["max_crops_per_character"]):
            crop_paths.extend(
                self._save_crops(
                    observation.crops,
                    f"character-{character_id}-{uuid.uuid4().hex[:8]}",
                )[: int(self.bank.config["max_crops_per_character"]) - len(crop_paths)]
            )
            record["crop_paths"] = crop_paths
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
        used_by_page_panel: dict[tuple[int, int | None], set[int]] = {}

        for observation in observations:
            blocked: set[int] = set()
            for panel_id in observation.panel_ids:
                blocked.update(used_by_page_panel.get((observation.page_index, panel_id), set()))
            character_id = self._resolve_existing(observation, blocked)
            if character_id is None:
                pending_id = self._match_pending(observation)
                if pending_id is not None:
                    character_id = self.bank.promote_pending(pending_id)
                    for previous_index in self._current_pending_occurrences.pop(pending_id, []):
                        labels[previous_index] = self.bank.display_label(character_id)
                    self._add_prototypes(character_id, observation)
                elif len(observation.detection_indices) >= 2:
                    prefix = f"new-{uuid.uuid4().hex[:8]}"
                    paths = self._save_crops(observation.crops, prefix)
                    chosen = _diverse_indices(
                        observation.embeddings,
                        int(self.bank.config["max_embeddings_per_character"]),
                    )
                    character_id = self.bank.create_character(
                        observation.embeddings[chosen], paths
                    )
                else:
                    pending_id = uuid.uuid4().hex
                    paths = self._save_crops(observation.crops, f"pending-{pending_id[:8]}")
                    self.bank.create_pending(
                        pending_id,
                        observation.embeddings,
                        observation.page_key,
                        paths,
                    )
                    self._current_pending_occurrences[pending_id] = list(
                        observation.global_indices
                    )
            else:
                self._add_prototypes(character_id, observation)

            if character_id is None:
                continue
            label = self.bank.display_label(character_id)
            for global_index in observation.global_indices:
                labels[global_index] = label
            for panel_id in observation.panel_ids:
                used_by_page_panel.setdefault(
                    (observation.page_index, panel_id), set()
                ).add(character_id)

        self.bank.dirty = True
        return labels


def install_detection_patch(model, text_detection_threshold: float = 0.15) -> None:
    """Set the text threshold and retain panel boxes for panel-aware constraints."""

    original = model.predict_detections_and_associations

    def patched(model_self, images, *args, **kwargs):
        kwargs.setdefault("text_detection_threshold", text_detection_threshold)
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
