"""Analyze-authoritative name registration; legacy model binding was removed."""


def enabled(config):
    return config.get("v6", {}).get("identity_name_binding_policy") == "analyze-authoritative-v3"


def build(store, provider, analyzed):
    from .identity_from_analyze import build as register
    return register(store, provider, analyzed)
