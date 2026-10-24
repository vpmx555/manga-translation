"""Default character-bank policy frozen into new pipeline manifests."""
import math

from .prototypes import STRATEGY


DEFAULT_CONFIG = {
    'max_distance': 0.55,
    'min_margin': 0,
    'pending_max_page_gap': 1,
    'prototype_strategy': STRATEGY,
    'assignment_strategy': 'page-frozen-frequent-id-then-distance-v1',
    'min_embeddings_per_character': 2,
    'max_embeddings_per_character': 16,
    'min_crops_per_character': 2,
    'max_crops_per_character': 16,
}


def validate_config(config):
    if set(config) != set(DEFAULT_CONFIG):
        raise ValueError('Unknown or missing character_bank configuration fields')
    for key in ('max_distance', 'min_margin'):
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2:
            raise ValueError('Invalid character_bank.' + key)
    for key in DEFAULT_CONFIG.keys() - {'max_distance', 'min_margin'}:
        if type(config[key]) is not type(DEFAULT_CONFIG[key]) or config[key] != DEFAULT_CONFIG[key]:
            raise ValueError('Unsupported character_bank.' + key)
