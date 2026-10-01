"""Inspect and validate samples from the PopManga test dataset.

Examples:
    python tests/test_dataset.py
    python tests/test_dataset.py --split unseen --limit 5
    python tests/test_dataset.py --limit 1 --load-images
"""

from __future__ import annotations

import argparse
import json
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import fsspec
import numpy as np
from huggingface_hub import hf_hub_download
from PIL import Image


DATASET_NAME = "ragavsachdeva/popmanga_test"
DATASET_ARCHIVE = "annotations.zip"
LABEL_NAMES = {0: "characters", 1: "texts", 2: "panels", 3: "tails"}
REQUIRED_FIELDS = {
    "image_path",
    "magi_annotations",
    "character_clusters",
    "text_char_matches",
    "text_tail_matches",
    "text_classification",
    "character_names",
}


def read_image(image_source: Any) -> np.ndarray:
    """Read a local path, fsspec URI, PIL image, or NumPy array as RGB."""
    if isinstance(image_source, np.ndarray):
        image = Image.fromarray(image_source)
    elif isinstance(image_source, Image.Image):
        image = image_source
    elif isinstance(image_source, dict) and image_source.get("bytes") is not None:
        image = Image.open(BytesIO(image_source["bytes"]))
    else:
        path = image_source.get("path") if isinstance(image_source, dict) else image_source
        if not isinstance(path, str) or not path:
            raise TypeError(f"Unsupported image value: {type(image_source).__name__}")
        with fsspec.open(path, "rb") as file:
            return np.asarray(Image.open(file).convert("RGB"))

    return np.asarray(image.convert("RGB"))


def inspect_example(example: dict[str, Any], index: int, load_images: bool) -> dict[str, Any]:
    missing_fields = sorted(REQUIRED_FIELDS - example.keys())
    if missing_fields:
        raise ValueError(f"Sample {index} is missing fields: {', '.join(missing_fields)}")

    annotations = example["magi_annotations"]
    bboxes = annotations["bboxes_as_x1y1x2y2"]
    labels = annotations["labels"]
    if len(bboxes) != len(labels):
        raise ValueError(
            f"Sample {index} has {len(bboxes)} boxes but {len(labels)} labels"
        )

    summary: dict[str, Any] = {
        "index": index,
        "image_path": example["image_path"],
        "image_loaded": False,
        "annotation_counts": {
            name: sum(label == label_id for label in labels)
            for label_id, name in LABEL_NAMES.items()
        },
        "character_clusters": len(example["character_clusters"]),
        "character_names": example["character_names"],
        "text_char_matches": len(example["text_char_matches"]),
        "text_tail_matches": len(example["text_tail_matches"]),
        "text_classification": len(example["text_classification"]),
    }

    if load_images:
        image = read_image(example["image_path"])
        summary["image_loaded"] = True
        summary["image_shape"] = list(image.shape)

    return summary


def iter_examples(split: str):
    """Yield annotations without executing the dataset's outdated remote loader."""
    archive_path = Path(
        hf_hub_download(
            repo_id=DATASET_NAME,
            filename=DATASET_ARCHIVE,
            repo_type="dataset",
        )
    )
    with ZipFile(archive_path) as archive:
        split_member = f"annotations/{split}.txt"
        image_paths = archive.read(split_member).decode("utf-8").splitlines()
        members = set(archive.namelist())

        for image_path in filter(None, map(str.strip, image_paths)):
            annotation_member = f"annotations/{image_path}.json"
            annotations = json.loads(archive.read(annotation_member))
            image_member = f"images/{image_path}"
            image_source = (
                f"zip://{image_member}::{archive_path}"
                if image_member in members
                else image_path
            )
            yield {
                "image_path": image_source,
                "magi_annotations": annotations["bbox_annotations"],
                "character_clusters": [
                    int(value) for value in annotations["character_clusters"]
                ],
                "text_char_matches": annotations["text_char_matches"],
                "text_tail_matches": annotations["text_tail_matches"],
                "text_classification": annotations["text_classification"],
                "character_names": annotations["character_names"],
            }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("seen", "unseen"), default="seen")
    parser.add_argument("--limit", type=int, default=3, help="Number of samples to inspect")
    parser.add_argument(
        "--streaming",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Iterate without materializing the entire dataset (default: enabled)",
    )
    parser.add_argument(
        "--load-images",
        action="store_true",
        help="Also download/open each image and report its shape",
    )
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    return args


def main() -> None:
    args = parse_args()
    dataset = iter_examples(args.split)

    print(f"Dataset: {DATASET_NAME}")
    print(f"Split: {args.split}")
    print(f"Features: {sorted(REQUIRED_FIELDS)}")

    inspected = 0
    succeeded = 0
    for index, example in enumerate(dataset):
        if inspected >= args.limit:
            break
        try:
            summary = inspect_example(example, index, args.load_images)
        except Exception as exc:
            print(json.dumps({"index": index, "error": str(exc)}, ensure_ascii=False))
        else:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            succeeded += 1
        inspected += 1

    if inspected == 0:
        raise RuntimeError(f"Split {args.split!r} did not yield any samples")
    if succeeded == 0:
        raise RuntimeError(
            f"All {inspected} inspected samples failed validation or image loading"
        )


if __name__ == "__main__":
    main()
