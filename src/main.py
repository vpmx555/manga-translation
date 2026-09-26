from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Sequence

import numpy as np
import torch
from PIL import Image
from transformers import AutoModel

from character_assignment import (
    DynamicCharacterAssigner,
    install_detection_patch,
    reset_assignment_context,
)
from character_bank import CharacterBank
from character_postprocessing import AmbiguousMatchPostprocessor


MODEL_ID = "ragavsachdeva/magiv2"
MODEL_FINGERPRINT = "ragavsachdeva/magiv2@fbc890fec52977142e8ee00bfe26e9458b65517c"
DEFAULT_IMAGE_FOLDER = Path(r"C:\Users\pxv23\Downloads\Manga\Manga\EN\New folder")


def read_image(path_to_image: Path) -> np.ndarray:
    with path_to_image.open("rb") as file:
        return np.asarray(Image.open(file).convert("L").convert("RGB"))


def _natural_key(path: Path) -> list[object]:
    parts = re.split(r"(\d+)", path.name)
    return [int(part) if part.isdigit() else part.casefold() for part in parts]


def discover_pages(image_folder: Path) -> list[Path]:
    extensions = {".png", ".jpg", ".jpeg"}
    return sorted(
        (path for path in image_folder.iterdir() if path.suffix.casefold() in extensions),
        key=_natural_key,
    )


def _parse_pair(value: str, separator: str, description: str) -> tuple[int, int]:
    try:
        left, right = value.split(separator, 1)
        return int(left), int(right)
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError(
            f"{description} must have the form INTEGER{separator}INTEGER"
        ) from error


def _apply_bank_edits(bank: CharacterBank, args: argparse.Namespace) -> None:
    for value in args.rename_character:
        try:
            character_id, name = value.split("=", 1)
            bank.rename_character(int(character_id), name)
        except ValueError as error:
            raise ValueError("--rename-character must have the form ID=NAME") from error
    for value in args.merge_character:
        source, target = _parse_pair(value, ":", "--merge-character")
        bank.merge_characters(source, target)
    for value in args.disable_prototype:
        character_id, prototype_index = _parse_pair(value, ":", "--disable-prototype")
        bank.disable_prototype(character_id, prototype_index)
    for value in args.disable_crop:
        character_id, crop_index = _parse_pair(value, ":", "--disable-crop")
        bank.disable_crop(character_id, crop_index)


def build_parser() -> argparse.ArgumentParser:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Run MAGI v2 with a persistent story-specific character bank."
    )
    parser.add_argument("image_folder", nargs="?", type=Path, default=DEFAULT_IMAGE_FOLDER)
    parser.add_argument("--story-name", help="Display name; defaults to the image folder name")
    parser.add_argument("--story-id", help="Optional stable key when titles need disambiguation")
    parser.add_argument("--chapter-id", help="Defaults to the image folder name")
    parser.add_argument(
        "--bank-root",
        type=Path,
        default=project_root / "data" / "character_banks",
    )
    parser.add_argument("--output", type=Path, default=project_root / "transcript.txt")
    parser.add_argument("--visualization-dir", type=Path, default=project_root)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--text-detection-threshold", type=float, default=0.15)
    parser.add_argument("--max-distance", type=float, default=None)
    parser.add_argument("--min-margin", type=float, default=None)
    parser.add_argument("--rename-character", action="append", default=[], metavar="ID=NAME")
    parser.add_argument("--merge-character", action="append", default=[], metavar="SOURCE:TARGET")
    parser.add_argument("--disable-prototype", action="append", default=[], metavar="ID:INDEX")
    parser.add_argument("--disable-crop", action="append", default=[], metavar="ID:INDEX")
    parser.add_argument(
        "--bank-only",
        action="store_true",
        help="Apply bank edits without loading MAGI or processing images",
    )
    return parser


def run(
    args: argparse.Namespace,
    *,
    postprocessors: Sequence[AmbiguousMatchPostprocessor] = (),
) -> Path:
    image_folder = args.image_folder.resolve()
    story_name = args.story_name or image_folder.name
    chapter_id = args.chapter_id or image_folder.name

    with CharacterBank(
        args.bank_root.resolve(),
        story_name,
        args.story_id,
        model_fingerprint=MODEL_FINGERPRINT,
    ) as bank:
        _apply_bank_edits(bank, args)
        if args.max_distance is not None:
            bank.config["max_distance"] = args.max_distance
            bank.dirty = True
        if args.min_margin is not None:
            bank.config["min_margin"] = args.min_margin
            bank.dirty = True
        if args.bank_only:
            return bank.metadata_path

        chapter_page_paths = discover_pages(image_folder)
        if not chapter_page_paths:
            raise FileNotFoundError(f"No manga pages found in: {image_folder}")
        print(f"Found {len(chapter_page_paths)} pages. Reading images...")
        chapter_pages = [read_image(path) for path in chapter_page_paths]
        page_keys = [f"{chapter_id}/{path.name}" for path in chapter_page_paths]

        if args.device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            device = args.device
        model = AutoModel.from_pretrained(MODEL_ID, trust_remote_code=True).eval().to(device)

        assigner = DynamicCharacterAssigner(bank, page_keys, postprocessors)
        assigner.bind(model)
        install_detection_patch(model, args.text_detection_threshold)
        reset_assignment_context(model)

        with torch.no_grad():
            per_page_results = model.do_chapter_wide_prediction(
                chapter_pages,
                {"images": [], "names": []},
                use_tqdm=True,
                do_ocr=True,
            )

        args.visualization_dir.mkdir(parents=True, exist_ok=True)
        transcript: list[str] = []
        for page_number, (image, page_result) in enumerate(
            zip(chapter_pages, per_page_results), start=1
        ):
            model.visualise_single_image_prediction(
                image,
                page_result,
                str(args.visualization_dir / f"page_{page_number}.png"),
            )
            transcript.append(f"--- Trang {page_number} ---")
            speaker_by_text = {
                int(text_index): page_result["character_names"][int(character_index)]
                for text_index, character_index in page_result["text_character_associations"]
                if int(character_index) < len(page_result["character_names"])
            }
            for text_index, text in enumerate(page_result["ocr"]):
                if not page_result["is_essential_text"][text_index]:
                    continue
                speaker = speaker_by_text.get(text_index, "Other")
                transcript.append(f"<{speaker}>: {text}")

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("\n".join(transcript) + "\n", encoding="utf-8")
        print(f"Done. Transcript saved to '{args.output}'.")
        return args.output


def main() -> None:
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
