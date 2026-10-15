"""Opt-in configuration for the local core engine."""
DEFAULT_CONFIG = {
    "enabled": False,
}


def validate_config(config):
    if not isinstance(config, dict) or set(config) != set(DEFAULT_CONFIG):
        raise ValueError("Unknown core configuration fields")
    if type(config["enabled"]) is not bool:
        raise ValueError("core.enabled must be boolean")


def enabled(config):
    return config.get("v6", {}).get("enabled", False) and config.get("core", {}).get("enabled", False)
