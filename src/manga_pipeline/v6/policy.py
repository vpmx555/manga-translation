"""Versioned contract for new runs. Historical artifacts remain read-only."""

POLICY = "independent-batches-v6.1"
MODEL = "cyankiwi/Qwen3.5-27B-AWQ-4bit"
REVISION = "5cba9b5f1bca69fab9b75c88ae7817ae22d7d898"


def require_config(config):
    binding = config.get('v6', {}).get('identity_name_binding_policy')
    if binding is not None:
        v6 = config['v6']
        if (binding not in {'chapter-multimodal-name-binding-v1', 'chapter-name-binding-simple-v2', 'analyze-authoritative-v3'}
                or v6.get('identity_relationship_policy') != 'identity-relationship-v2'
                or type(v6.get('identity_binding_concurrency')) is not int
                or not 1 <= v6['identity_binding_concurrency'] <= 8
                or type(v6.get('identity_binding_output_tokens')) is not int
                or not 1 <= v6['identity_binding_output_tokens'] <= 8192):
            raise ValueError('Invalid frozen identity name-binding contract')
    if (config.get("analysis_contract") != POLICY or "ollama" in config
            or config.get("llm", {}).get("backend") != "vllm"
            or config.get("llm", {}).get("model") != MODEL
            or config.get("llm", {}).get("num_ctx") != 32768
            or config.get("vllm", {}).get("revision") != REVISION
            or config.get("vllm", {}).get("max_num_seqs") not in {8, 32, 64}
            or config.get("vllm", {}).get("max_num_batched_tokens") != 2048
            or config.get("ner", {}).get("backend") not in {"gliner", "llm-scene"}
            or config.get("ocr", {}).get("backend") not in {"hayai", "glm-ocr"}
            or not config.get("layout", {}).get("enabled")
            or not config.get("v6", {}).get("enabled")
            or config.get("v6", {}).get("typeset_mode") != "ocr-box"
            or config.get("v6", {}).get("analyze_engine") != POLICY):
        raise ValueError("Incompatible run/config; create a new independent-batches v6 run")
