"""Frozen contract for new chapter graph runs; old runs keep their source bundle."""
POLICY = "chapter-story-graph-v7"
MODEL = "cyankiwi/Qwen3.5-27B-AWQ-4bit"
REVISION = "5cba9b5f1bca69fab9b75c88ae7817ae22d7d898"

EVIDENCE_CONFIDENCE_PROMPT = '''Shared evidence and confidence policy:
Confidence must reflect the clarity and strength of the supplied source evidence
and the uncertainty that remains, not how fluent or plausible your explanation is.
Do not assign high confidence when relevant evidence is ambiguous, incomplete,
conflicting, or leaves serious competing interpretations unresolved.
Inference FROM supplied sources is allowed: use source text/OCR, images when
provided, detector metadata and source anchors as appropriate to the decision.
Explain the source-to-conclusion connection and cite supplied source IDs when possible.
Distinguish those sources from model-generated hypotheses, summaries, scene memory,
names, previous decisions and their confidence scores. Your own earlier inference
is not a new source observation. Repeating it, paraphrasing it, deriving further
claims from it or receiving it back in a later packet does not independently
corroborate it. Trace reused inferences back to their underlying supplied sources.
If their premises cannot be verified from the available evidence, carry that
uncertainty forward; never turn an uncertain premise into a high-confidence
conclusion merely through a chain of your own reasoning. Increase confidence
only when clear source support justifies it, not because your prior outputs agree.
Apply this policy separately to every confidence-bearing decision and description.'''


def require_config(config):
    v6, llm, engine = config.get('v6', {}), config.get('llm', {}), config.get('vllm', {})
    if (config.get('analysis_contract') != POLICY or 'ollama' in config
            or v6.get('analyze_engine') != POLICY or v6.get('graph_policy') != POLICY
            or v6.get('identity_name_binding_policy') != 'analyze-authoritative-v3'
            or v6.get('identity_relationship_policy') != 'identity-relationship-v2'
            or llm.get('backend') != 'vllm' or llm.get('model') != MODEL
            or llm.get('num_ctx') != 65536 or llm.get('analyze_num_ctx') != 65536
            or llm.get('analyze_thinking') is not False
            or engine.get('revision') != REVISION or engine.get('max_num_seqs') not in {8, 32, 64}
            or engine.get('max_num_batched_tokens') != 2048
            or config.get('ner', {}).get('backend') != 'llm-scene'
            or config.get('ocr', {}).get('backend') not in {'hayai', 'glm-ocr'}
            or not config.get('layout', {}).get('enabled') or not v6.get('enabled')
            or v6.get('typeset_mode') != 'ocr-box'):
        raise ValueError('Incompatible run/config; create a chapter-story-graph-v7 run. Historical artifacts require their frozen source.')
