import sys
import copy
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from manga_pipeline.pipeline import DEFAULT_CONFIG
from manga_pipeline.storage.io import atomic_json
from manga_pipeline.storage.runs import RunStore


def raw_document(ids=(1,), text="This is Roronoa Zoro."):
    return {"schema_version": 1, "kind": "magi_raw", "document_id": "chapter", "story_id": "story",
            "chapter_id": "chapter", "reading_direction": "rtl", "pages": [{
                "id": "page1", "page": 1, "image_path": "unused.png", "width": 100, "height": 100,
                "panels": [[0, 0, 100, 100]], "characters": [[i * 20, 20, i * 20 + 15, 70] for i in range(len(ids))],
                "character_ids": list(ids), "tails": []}],
            "texts": [{"id": "text1", "page_id": "page1", "page": 1, "reading_order": 0,
                       "text": text, "bbox": [0, 0, 100, 10], "panel_index": 0,
                       "speaker_id": ids[0] if ids else None, "character_detection_index": 0 if ids else None,
                       "is_essential_text": True}]}


def make_store(root, characters=None):
    bank_path = Path(root) / "banks" / "story" / "metadata.json"
    atomic_json(bank_path, {"schema_version": 1, "characters": characters if characters is not None else {
        "1": {"id": 1, "display_name": None}, "2": {"id": 2, "display_name": None}}, "pending": {}})
    # Existing regression fixtures exercise authoritative legacy v2 runs.
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.pop("dialogue_analysis", None)
    config.pop("speaker_policy", None)
    config.pop("noise_filter", None)
    config.pop("translation", None)
    config.pop("ner", None)
    return RunStore.create(Path(root) / "run", source={"images": [], "story": "story", "chapter": "chapter"},
                           config=config, bank_path=bank_path)


class FakeDetector:
    def __init__(self, name="Roronoa Zoro"):
        self.name, self.calls = name, 0

    def detect(self, text):
        self.calls += 1
        start = text.find(self.name)
        return [] if start < 0 else [{"name": self.name, "start": start,
                                     "end": start + len(self.name), "source": "spacy"}]


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.fail_classification = False

    def infer(self, task, prompt, data, schema, validator, images=None):
        self.calls.append((task, data, images or []))
        if task == "classify_mentions":
            if self.fail_classification:
                raise RuntimeError("timeout")
            result = {"content_type": "narration", "mentions": [
                {"candidate_id": c["id"], "mention_type": "narration_introduction"} for c in data["candidates"]]}
        else:
            result = {"is_narration": True, "links": [
                {"candidate_id": c["candidate_id"], "detection_index": 0,
                 "status": "linked", "reason": "visible introduction"} for c in data["names"]]}
        validator(result)
        return result

    def close(self):
        pass
