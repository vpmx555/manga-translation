"""Shared, conservative request budgets; never claims exact model tokenization."""
import base64
import copy
import io
import json
import math
from .stage_policy import request_policy


class BudgetExceeded(ValueError):
    """Split targets first; reduce only explicitly permitted supporting context."""


class OutputTruncated(BudgetExceeded):
    """The output reserve was insufficient; never accept a partial JSON result."""


TRANSLATION_SUFFIX = (
    " Preserve URLs and email addresses verbatim; do not translate or expand them."
    " For corrupted OCR, give a concise best effort and flag needs_review."
    " Never expand repeated source fragments. Keep review_reason under 30 words.")
ANALYZE_SUFFIX = (
    " Preserve every required schema field and target ID. Keep evidence and reasons concise;"
    " do not repeat the source text or add unsupported explanations.")


def output_tokens(config, task):
    cfg = config.get("llm", config)
    if task.startswith("analyze_scene"):
        return cfg.get("scene_num_predict", max(cfg["num_predict"], 2048))
    if task.startswith("analyze_"):
        return cfg.get("analyze_num_predict", max(cfg["num_predict"], 2048))
    if task.startswith("translate_vi_"):
        return cfg.get("translation_num_predict", max(cfg["num_predict"], 2048))
    return cfg["num_predict"]


def final_prompt(task, prompt):
    suffix = TRANSLATION_SUFFIX if task.startswith("translate_vi_") else (
        ANALYZE_SUFFIX if task.startswith("analyze_") else "")
    return prompt if not suffix or suffix in prompt else prompt + suffix


def image_reserve(images):
    if isinstance(images, bool):
        return 2048 if images else 0
    total = 0
    for encoded in images or []:
        try:
            from PIL import Image
            raw = encoded.split(",", 1)[-1] if encoded.startswith("data:") else encoded
            with Image.open(io.BytesIO(base64.b64decode(raw))) as image:
                width, height = image.size
            # Conservative patch allowance, not Qwen's exact vision tokenizer.
            total += math.ceil(width / 14) * math.ceil(height / 14) + 256
        except (ValueError, OSError, TypeError, AttributeError, ImportError):
            total += 4096
    return total


def input_estimate(prompt, data, *, images=False, num_predict=2048, schema=None):
    def estimate(value):
        wire = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return math.ceil(len(wire.encode("utf-8")) / 2)
    return (estimate(prompt) + estimate(data) + (estimate(schema) if schema is not None else 0)
            + image_reserve(images) + num_predict + 512)


def request_budget(config, task, prompt, data, schema, images=None):
    cfg = config.get("llm", config)
    runtime = config.get("runtime", {})
    fraction = runtime.get("context_fraction", 0.85) if runtime.get("enabled") else 1.0
    reserve = output_tokens(config, task)
    estimate = input_estimate(final_prompt(task, prompt), data, images=images or [],
                              num_predict=reserve, schema=schema)
    context = request_policy(config, task)['num_ctx']
    return {"estimate": estimate, "limit": math.floor(context * fraction),
            "num_ctx": context, "output_reserve": reserve,
            "method": "utf8-half-plus-template-and-images-v2"}


def measured_budget(provider, config, task, prompt, data, schema, images=None):
    measure = getattr(provider, "request_budget", None)
    return measure(task, prompt, data, schema, images) if callable(measure) else request_budget(config, task, prompt, data, schema, images)


def fit_request(config, task, prompt, data, schema, images=None, *, measure=None):
    """Reduce singleton supporting context only, leaving targets/mentions/schema intact.

    Multi-target requests must be split by their owner, which preserves coverage,
    thread boundaries and checkpoint order. No source text is shortened here.
    """
    result = copy.deepcopy(data)
    def budget(value):
        return measure(task, prompt, value, schema, images) if measure else request_budget(config, task, prompt, value, schema, images)
    report = budget(result)
    report.update(before=report["estimate"], reductions=[])

    def fits():
        report.update(budget(result))
        return report["estimate"] <= report["limit"]

    if report["estimate"] <= report["limit"]:
        return result, report
    if task.startswith("analyze_") or len(result.get("targets", [])) != 1 or not config.get("llm", config).get("compact_context", False):
        raise BudgetExceeded(f"Request exceeds context budget: {report['estimate']} > {report['limit']}; split targets")

    def record(field, action):
        report["reductions"].append({"field": field, "action": action})

    # Oldest history is reduced first. Mandatory targets, anchors, names and
    # mention candidate ownership are never dropped or changed.
    for field in ("previous_translations",):
        while result.get(field) and not fits():
            result[field] = result[field][1:]
            record(field, "drop_oldest_support")
    target_ids = {r["id"] for r in result.get("targets", [])}
    if result.get("context") and not fits():
        result["context"] = [r for r in result["context"] if r.get("id") in target_ids]
        record("context", "retain_target_context_only")
    if not fits():
        raise BudgetExceeded(f"Minimum protected input exceeds context budget: {report['estimate']} > {report['limit']}")
    return result, report


def validate_runpod_profile(config):
    runtime, cfg = config.get("runtime", {}), config.get("llm", {})
    if not runtime.get("required_gpu") and not cfg.get("require_full_gpu"):
        return
    expected = {"rtx4090": "RTX 4090", "rtx5090": "RTX 5090", "l40s": "L40S"}.get(runtime.get("preset"))
    if (not expected or runtime.get("required_gpu") != expected
            or not runtime.get("enabled") or runtime.get("story_workers") != 1
            or type(runtime.get("llm_slots")) is not int
            or cfg.get("backend") != "vllm" or not 1 <= runtime["llm_slots"] <= 64
            or not cfg.get("require_full_gpu")
            or config.get("device") != "cuda"):
        raise ValueError("The frozen full-GPU profile requires a matching GPU preset, CUDA, one story and vLLM slots 1..64; no GPU fallback")



def preflight_case(config):
    from ..v6.batch_contract import PROMPT, schema
    from ..v6.planning import output_reserve
    rows = [{"id": "probe:1", "speaker_anchor": {"locked": False, "person_id": None}}]
    wire = schema(rows, [], {}, {})
    data = {"targets": [{"id": "probe:1", "text": "Where did you go?"}], "scene_summary": "A brief conversation.", "analysis_branch": "dialogue", "cluster_pool": {}, "known_names": {}, "mentions": [], "unknown_reference": "others"}
    return {"prompt": final_prompt("analyze_dialogue_v6", PROMPT), "data": data, "schema": wire, "budget": request_budget(config, "analyze_dialogue_v6", PROMPT, data, wire)}
