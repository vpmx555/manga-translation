"""Persistent, story-scoped character bank."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import numpy as np


SCHEMA_VERSION = 1


def normalize_story_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = re.sub(r"[^\w]+", "-", normalized, flags=re.UNICODE)
    normalized = normalized.strip("-_")
    if not normalized:
        raise ValueError("Story name/id must contain at least one letter or digit")
    return normalized


@contextmanager
def _exclusive_lock(path: Path, timeout_seconds: float = 30.0) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    started = time.monotonic()
    try:
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    if handle.read(1) == b"":
                        handle.seek(0)
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() - started >= timeout_seconds:
                    raise TimeoutError(f"Timed out waiting for character bank lock: {path}")
                time.sleep(0.1)
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class CharacterBank:
    def __init__(
        self,
        root: Path,
        story_name: str,
        story_id: str | None = None,
        *,
        model_fingerprint: str = "ragavsachdeva/magiv2",
    ) -> None:
        self.story_name = story_name.strip()
        self.story_key = normalize_story_key(story_id or story_name)
        self.directory = Path(root) / self.story_key
        self.metadata_path = self.directory / "metadata.json"
        self.embeddings_path = self.directory / "embeddings.npz"
        self.crops_directory = self.directory / "crops"
        self.lock_path = self.directory / ".bank.lock"
        self.model_fingerprint = model_fingerprint
        self.metadata: dict[str, object] = {}
        self.arrays: dict[str, np.ndarray] = {}
        self.dirty = False
        self._lock_context = None

    def __enter__(self) -> "CharacterBank":
        self._lock_context = _exclusive_lock(self.lock_path)
        self._lock_context.__enter__()
        self.load()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if exc_type is None and self.dirty:
                self.save()
        finally:
            assert self._lock_context is not None
            self._lock_context.__exit__(exc_type, exc, traceback)
            self._lock_context = None

    def _empty_metadata(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "story_name": self.story_name,
            "story_key": self.story_key,
            "next_character_id": 1,
            "model_fingerprint": self.model_fingerprint,
            "characters": {},
            "pending": {},
            "config": {
                "max_distance": 0.65,
                "min_margin": 0.08,
                "prototype_min_separation": 0.08,
                "max_embeddings_per_character": 8,
                "max_crops_per_character": 4,
            },
            "postprocessors": [],
        }

    def load(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.crops_directory.mkdir(parents=True, exist_ok=True)
        if not self.metadata_path.exists():
            self.metadata = self._empty_metadata()
            self.arrays = {}
            self.dirty = True
            return
        self.metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        if self.metadata.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported character bank schema: {self.metadata.get('schema_version')}"
            )
        stored_fingerprint = self.metadata.get("model_fingerprint")
        if stored_fingerprint != self.model_fingerprint:
            raise ValueError(
                "Character bank embedding model mismatch: "
                f"{stored_fingerprint!r} != {self.model_fingerprint!r}"
            )
        if self.embeddings_path.exists():
            with np.load(self.embeddings_path, allow_pickle=False) as archive:
                self.arrays = {key: archive[key].astype(np.float32) for key in archive.files}
        else:
            self.arrays = {}

    @property
    def config(self) -> dict[str, float | int]:
        return self.metadata["config"]  # type: ignore[return-value]

    @property
    def characters(self) -> dict[str, dict[str, object]]:
        return self.metadata["characters"]  # type: ignore[return-value]

    @property
    def pending(self) -> dict[str, dict[str, object]]:
        return self.metadata["pending"]  # type: ignore[return-value]

    def embedding_key_for_character(self, character_id: int) -> str:
        return f"character_{character_id}"

    def embedding_key_for_pending(self, pending_id: str) -> str:
        return f"pending_{pending_id}"

    def get_character_embeddings(self, character_id: int) -> np.ndarray:
        values = self.arrays.get(
            self.embedding_key_for_character(character_id),
            np.empty((0, 0), dtype=np.float32),
        )
        record = self.characters.get(str(character_id), {})
        disabled = {int(index) for index in record.get("disabled_prototype_indices", [])}
        if disabled and len(values):
            values = np.asarray(
                [value for index, value in enumerate(values) if index not in disabled],
                dtype=np.float32,
            )
        return values

    def get_pending_embeddings(self, pending_id: str) -> np.ndarray:
        return self.arrays.get(
            self.embedding_key_for_pending(pending_id),
            np.empty((0, 0), dtype=np.float32),
        )

    def create_character(self, embeddings: np.ndarray, crop_paths: list[str]) -> int:
        character_id = int(self.metadata["next_character_id"])
        self.metadata["next_character_id"] = character_id + 1
        self.characters[str(character_id)] = {
            "id": character_id,
            "display_name": None,
            "crop_paths": crop_paths,
            "created_at": time.time(),
            "disabled": False,
            "disabled_prototype_indices": [],
            "disabled_crop_paths": [],
        }
        self.arrays[self.embedding_key_for_character(character_id)] = embeddings.astype(np.float32)
        self.dirty = True
        return character_id

    def create_pending(
        self,
        pending_id: str,
        embeddings: np.ndarray,
        page_key: str,
        crop_paths: list[str],
    ) -> None:
        self.pending[pending_id] = {
            "id": pending_id,
            "page_keys": [page_key],
            "crop_paths": crop_paths,
            "created_at": time.time(),
        }
        self.arrays[self.embedding_key_for_pending(pending_id)] = embeddings.astype(np.float32)
        self.dirty = True

    def promote_pending(self, pending_id: str) -> int:
        record = self.pending.pop(pending_id)
        embeddings = self.arrays.pop(self.embedding_key_for_pending(pending_id))
        return self.create_character(embeddings, list(record.get("crop_paths", [])))

    def display_label(self, character_id: int) -> str:
        record = self.characters[str(character_id)]
        display_name = record.get("display_name")
        return str(display_name) if display_name else str(character_id)

    def rename_character(self, character_id: int, display_name: str | None) -> None:
        record = self.characters.get(str(character_id))
        if record is None:
            raise KeyError(f"Unknown character ID: {character_id}")
        record["display_name"] = display_name.strip() if display_name else None
        self.dirty = True

    def disable_prototype(self, character_id: int, prototype_index: int) -> None:
        record = self.characters.get(str(character_id))
        if record is None:
            raise KeyError(f"Unknown character ID: {character_id}")
        embeddings = self.arrays.get(self.embedding_key_for_character(character_id))
        if embeddings is None or not 0 <= prototype_index < len(embeddings):
            raise IndexError(f"Invalid prototype index {prototype_index} for <{character_id}>")
        disabled = {int(index) for index in record.get("disabled_prototype_indices", [])}
        disabled.add(prototype_index)
        record["disabled_prototype_indices"] = sorted(disabled)
        self.dirty = True

    def disable_crop(self, character_id: int, crop_index: int) -> None:
        record = self.characters.get(str(character_id))
        if record is None:
            raise KeyError(f"Unknown character ID: {character_id}")
        crop_paths = list(record.get("crop_paths", []))
        if not 0 <= crop_index < len(crop_paths):
            raise IndexError(f"Invalid crop index {crop_index} for <{character_id}>")
        disabled = set(record.get("disabled_crop_paths", []))
        disabled.add(crop_paths[crop_index])
        record["disabled_crop_paths"] = sorted(disabled)
        self.dirty = True

    def merge_characters(self, source_id: int, target_id: int) -> None:
        if source_id == target_id:
            raise ValueError("Source and target character IDs must differ")
        source = self.characters.get(str(source_id))
        target = self.characters.get(str(target_id))
        if source is None or target is None:
            raise KeyError("Both source and target character IDs must exist")
        source_embeddings = self.get_character_embeddings(source_id)
        target_embeddings = self.get_character_embeddings(target_id)
        if source_embeddings.size:
            combined = (
                np.vstack([target_embeddings, source_embeddings])
                if target_embeddings.size
                else source_embeddings
            )
            maximum = int(self.config["max_embeddings_per_character"])
            self.arrays[self.embedding_key_for_character(target_id)] = combined[:maximum]
            target["disabled_prototype_indices"] = []
        crop_paths = list(target.get("crop_paths", []))
        for crop_path in source.get("crop_paths", []):
            if crop_path not in crop_paths:
                crop_paths.append(crop_path)
        target["crop_paths"] = crop_paths[: int(self.config["max_crops_per_character"])]
        source["disabled"] = True
        source["merged_into"] = target_id
        self.dirty = True

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.crops_directory.mkdir(parents=True, exist_ok=True)
        metadata_fd, metadata_tmp_name = tempfile.mkstemp(
            prefix="metadata-", suffix=".tmp", dir=self.directory
        )
        embeddings_fd, embeddings_tmp_name = tempfile.mkstemp(
            prefix="embeddings-", suffix=".npz", dir=self.directory
        )
        try:
            with os.fdopen(metadata_fd, "w", encoding="utf-8") as handle:
                json.dump(self.metadata, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            with os.fdopen(embeddings_fd, "wb") as handle:
                np.savez_compressed(handle, **self.arrays)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(embeddings_tmp_name, self.embeddings_path)
            os.replace(metadata_tmp_name, self.metadata_path)
            self.dirty = False
        finally:
            for temporary in (metadata_tmp_name, embeddings_tmp_name):
                try:
                    Path(temporary).unlink()
                except FileNotFoundError:
                    pass
