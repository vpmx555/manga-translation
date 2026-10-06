import re
from pathlib import Path
from ..storage.io import file_hash


def discover_pages(folder):
    def natural(path):
        return [int(x) if x.isdigit() else x.casefold() for x in re.split(r"(\d+)", path.name)]
    return sorted((p for p in Path(folder).iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}),
                  key=natural)


def source_manifest(folder):
    paths = discover_pages(folder)
    if not paths:
        raise ValueError(f"No manga images in {folder}")
    return [{"path": str(p.resolve()), "sha256": file_hash(p)} for p in paths]


def choose_device(requested, cuda_available):
    if requested not in {"auto", "cpu", "cuda"}:
        raise ValueError("Device must be auto, cpu or cuda")
    if requested == "cuda" and not cuda_available:
        raise RuntimeError("CUDA requested but unavailable; use --device cpu/auto or install compatible PyTorch/GPU")
    return "cuda" if requested == "cuda" or (requested == "auto" and cuda_available) else "cpu"
