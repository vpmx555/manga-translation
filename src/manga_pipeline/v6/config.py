"""Resolved, fingerprinted limits; visual hints never consume OCR."""
import math

DEFAULT_CONFIG = {
    "enabled": True,
    "mask_merge_iou": 0.35, "mask_containment": 0.85,
    "repair_radius": 2, "neck_width_ratio": 0.035, "min_body_area": 2500,
    "max_reconstructed_fraction": 0.20, "hint_acceptance": 0.80,
    "body_margin_px": 10, "body_margin_ratio": 0.0,
    "font_path": "assets/fonts/astro-city-teddy-bear.ttf", "font_min_px": 10, "font_max_px": 48,
    "line_spacing": 1.18, "shorten_attempts": 2,
    "min_confirmed_call_events": 2, "analyze_batch_size": 3,
    "context_image_max_side": 384,
}


def validate_config(config):
    if not isinstance(config, dict) or set(config) != set(DEFAULT_CONFIG):
        raise ValueError("Invalid v6 configuration fields")
    if type(config["enabled"]) is not bool:
        raise ValueError("v6.enabled must be boolean")
    for key in ("mask_merge_iou", "mask_containment", "neck_width_ratio",
                "max_reconstructed_fraction", "hint_acceptance"):
        value = config[key]
        if type(value) not in {float, int} or not math.isfinite(value) or not 0 < value <= 1:
            raise ValueError(f"Invalid v6.{key}")
    ratio = config["body_margin_ratio"]
    if type(ratio) not in {float, int} or not math.isfinite(ratio) or not 0 <= ratio <= 1:
        raise ValueError("Invalid v6.body_margin_ratio")
    for key in ("repair_radius", "min_body_area", "body_margin_px", "font_min_px",
                "font_max_px", "shorten_attempts", "min_confirmed_call_events", "analyze_batch_size", "context_image_max_side"):
        if type(config[key]) is not int or config[key] < (0 if key in {"repair_radius", "shorten_attempts"} else 1):
            raise ValueError(f"Invalid v6.{key}")
    if config["font_min_px"] > config["font_max_px"] or config["font_max_px"] > 256:
        raise ValueError("Invalid v6 font range")
    if config["shorten_attempts"] > 3 or config["analyze_batch_size"] > 8 or config["repair_radius"] > 16:
        raise ValueError("v6 resource budget exceeded")
    if type(config["line_spacing"]) not in {int, float} or not 1 <= config["line_spacing"] <= 2:
        raise ValueError("Invalid v6.line_spacing")
    if not 128 <= config["context_image_max_side"] <= 768:
        raise ValueError("Invalid v6.context_image_max_side")
    if config["font_path"] is not None and not isinstance(config["font_path"], str):
        raise ValueError("v6.font_path must be a path or null")
