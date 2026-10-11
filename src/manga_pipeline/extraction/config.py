"""MAGI thresholds shared by configuration and the detection adapter."""
import math

DETECTION_DEFAULTS = {
    "text_detection_threshold": 0.45,
    "character_detection_threshold": 0.35,
    "character_character_matching_threshold": 0.75,
    "text_character_matching_threshold": 0.25,
    "text_classification_threshold": -0.5,
}

# Missing fields in committed manifests mean the original MAGI behavior.
LEGACY_IMPLICIT_THRESHOLDS = {
    "character_detection_threshold": 0.30,
    "text_character_matching_threshold": 0.35,
    "text_classification_threshold": 0.5,
}


def validate_thresholds(config):
    for key in DETECTION_DEFAULTS:
        value = config[key]
        if type(value) not in {int, float} or not math.isfinite(value):
            raise ValueError(f"Invalid {key}: expected a finite number")
        if key != "text_classification_threshold" and not 0 <= value <= 1:
            raise ValueError(f"Invalid {key}: expected 0..1")


def threshold_for_manifest(config, key):
    """Preserve implicit defaults when extracting or reusing a legacy run."""
    return config.get(key, LEGACY_IMPLICIT_THRESHOLDS.get(key, DETECTION_DEFAULTS[key]))
