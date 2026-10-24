# Historical contracts

The `.py.txt` files retain tests for removed v2/v5 pipelines, standalone legacy
translation, Ollama, unified semantics and forward/backward dual-pass analysis.
They are historical source, not runnable tests for the independent v6 contract.
They were moved without changing their content instead of hiding failures behind
skip markers or reintroducing compatibility runtime code.

Current replacements live in `test_v6_independent_batches.py`,
`test_v6_semantic_contract.py`, `test_v6_pipeline.py`, `test_vllm_inference_policy.py`,
the storage/provider tests and the native delivery tests. Geometry/font/inpaint,
registry immutability, timing, checkpoint integrity and mention ownership assertions
that still apply remain in the active test suite. Noise-rule snapshots are retained;
the old normalization route that deleted whole OCR boxes is no longer active.
