"""Select the configured LLM engine without silent fallback."""


def create_provider(config, cache_dir):
    settings = config["llm"]
    if settings.get("backend", "llm") == "vllm":
        from .vllm import VLLM
        return VLLM(config, cache_dir)
    raise ValueError("Unknown LLM backend; fallback is disabled")
