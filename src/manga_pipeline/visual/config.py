"""Pinned visual models; RF-DETR runs in its own Transformers 5 environment."""
from pathlib import Path
import math

KOHARU_MODEL = "mayocream/koharu-layout-rfdetr-seg-2xl-1152"
KOHARU_REVISION = "aed55fdb8ca953c6bec33cf6ed6dd52a9b72bfa2"
AOT_MODEL = "mayocream/aot-inpainting"
AOT_REVISION = "cffe2346ac2b5ebe1f2d61335d602d12cc144c6f"
PROJECT = Path(__file__).resolve().parents[3]
LAYOUT_CONFIG = {
    "enabled": True, "python": None, "model_root": str(PROJECT / "models" / "visual"),
    "model": KOHARU_MODEL, "revision": KOHARU_REVISION,
    "panel_threshold": 0.50, "bubble_threshold": 0.45, "crop_padding": 24,
    "mapping_coverage": 0.80, "mapping_margin": 0.15, "device": "auto",
}
INPAINT_CONFIG = {
    "enabled": False, "model": AOT_MODEL, "revision": AOT_REVISION,
    "model_root": str(PROJECT / "models" / "visual"), "device": "auto",
    "dilation": 4, "max_side": 1024, "white_min": 245,
    "white_fraction": 0.98, "white_std": 5.0, "white_chroma": 8,
}


def validate_config(layout, inpaint):
    for name, config, defaults in (("layout", layout, LAYOUT_CONFIG), ("inpaint", inpaint, INPAINT_CONFIG)):
        if not isinstance(config, dict) or set(config) - set(defaults):
            raise ValueError(f"Unknown {name} configuration")
        if type(config["enabled"]) is not bool or config["device"] not in {"auto", "cpu", "cuda"}:
            raise ValueError(f"Invalid {name} backend configuration")
        if not isinstance(config["model_root"], str) or not config["model_root"]:
            raise ValueError(f"Invalid {name} model_root")
        if config["model"] != defaults["model"] or config["revision"] != defaults["revision"]:
            raise ValueError(f"Unsupported {name} model/revision; update the adapter explicitly")
    if inpaint["enabled"] and not layout["enabled"]:
        raise ValueError("inpaint requires Koharu layout")
    if layout["python"] is not None and (not isinstance(layout["python"], str) or not layout["python"].strip()):
        raise ValueError("layout.python must be an interpreter path or null")
    for key in ("panel_threshold", "bubble_threshold", "mapping_coverage", "mapping_margin"):
        if type(layout[key]) not in {int, float} or not math.isfinite(layout[key]) or not 0 < layout[key] <= 1:
            raise ValueError(f"Invalid layout.{key}")
    for key, lo, hi in (("crop_padding", 0, 256),):
        if type(layout[key]) is not int or not lo <= layout[key] <= hi:
            raise ValueError(f"Invalid layout.{key}")
    for key, lo, hi in (("dilation", 0, 32), ("max_side", 64, 1024), ("white_min", 0, 255), ("white_chroma", 0, 255)):
        if type(inpaint[key]) is not int or not lo <= inpaint[key] <= hi:
            raise ValueError(f"Invalid inpaint.{key}")
    for key, lo, hi in (("white_fraction", 0, 1), ("white_std", 0, 255)):
        if type(inpaint[key]) not in {int, float} or not math.isfinite(inpaint[key]) or not lo <= inpaint[key] <= hi:
            raise ValueError(f"Invalid inpaint.{key}")


def asset_dir(config):
    return Path(config["model_root"]).resolve() / config["model"].split("/")[-1] / config["revision"]


def checked_assets(config):
    from ..storage.io import file_hash, read_json
    directory = asset_dir(config)
    receipt = directory / "assets.json"
    if not receipt.is_file():
        raise FileNotFoundError(f"Visual model not prepared: {directory}; run scripts/setup_visual_models.py")
    data = read_json(receipt)
    if data.get("model") != config["model"] or data.get("revision") != config["revision"]:
        raise ValueError("Visual asset receipt model/revision mismatch")
    hashes = data.get("sha256", {})
    if set(hashes) != {"model.safetensors"}:
        raise ValueError("Visual asset receipt must contain the pinned checkpoint")
    for name, digest in hashes.items():
        if not (directory / name).is_file() or file_hash(directory / name) != digest:
            raise ValueError(f"Visual model changed/corrupt: {directory / name}")
    return directory, hashes


def layout_interpreter(config):
    import sys
    if config.get("python"):
        return config["python"]
    for relative in (".venv-layout-local/Scripts/python.exe", ".venv-layout-local/bin/python"):
        candidate = PROJECT / relative
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def preflight(config):
    """Validate the pinned worker before creating a run or mutating its bank."""
    import subprocess
    import sys
    checked_assets(config)
    python = layout_interpreter(config)
    script = (
        "from importlib.metadata import version; import torch; "
        "from rfdetr import RFDETRSeg2XLarge; from safetensors.torch import load_file; "
        "assert version('rfdetr') == '1.7.0', 'requires rfdetr==1.7.0'; "
        "assert version('transformers').split('.')[0] == '5', 'requires isolated Transformers 5'; "
        + ("assert torch.cuda.is_available(), 'CUDA unavailable'; " if config['device'] == 'cuda' else "")
    )
    try:
        result = subprocess.run([python, "-B", "-c", script], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Koharu worker unavailable: {python}; prepare layout.python and visual assets") from exc
    if result.returncode:
        raise RuntimeError(f"Koharu worker not ready: {python}; {result.stderr[-1200:]}")
