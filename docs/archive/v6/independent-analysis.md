# v6 independent analysis — current interface

Policy: `independent-batches-v6.1`. Offline implementation and mock/fixture
validation are complete; see the [report](../plan/v6-only-parallel-analyze-refactor-implementation-20261021.md).
GPU qualification and capacity/throughput measurements remain separate.

New configurations use `analysis_contract` and `llm`, with vLLM/Qwen, Koharu,
MAGI, Hayai and GLiNER. Old configurations and manifests are rejected explicitly.
Compatible v6 visual banks/person registries remain separate. Existing artifacts
and historical documentation are retained; old run resume is not supported.

Stages: `layout → extract → bubble_geometry → normalize → scan → analyze → identity
→ export → inpaint → translate → typeset`. Inpaint is enabled by configuration;
typesetting uses OCR boxes. Start with `python run.py run <folder> --story <story>
--chapter <chapter> --config configs/pipeline.json`; resume a new-policy run with
`python run.py resume --run-dir <run-directory>`.

Analyze sends all essential dialogue/captions in one scene request. Code builds
contiguous scenes from start IDs. Each independent caption/dialogue batch receives
only its own text, scene summary and frozen source/name snapshot. Source anchors
are immutable; unknown people share the `others` sentinel without gaining identity.
Speaker merge policy (user update 2026-10-21): `inference-first-v1`. Source anchors
remain immutable audit evidence and batching hints, not constraints on the model's
speaker choice. After analyze, merge prefers valid inference, records source
disagreement, and removes an incompatible cluster reference while preserving the
inferred person and original response. Source-anchor conflicts and missing evidence
ID lists are nonfatal audit warnings; malformed or unknown references still produce
source-only unresolved targets without stopping other batches.

Scene summaries are background, never identity evidence. Semantic errors retain
valid peers and source-only unresolved targets without model repair.

The soft cap is eight targets. Dialogue cuts wait for a source anchor unless the
hard context budget forces a cut. Exact tokenization is cached across planning and
provider admission. A shared queue refills continuously, starting at two requests,
with a configured ceiling up to eight. Engine capacity is eight sequences at context
32768 and `max_num_batched_tokens=2048`. Admission defaults are not measured GPU
capacity or throughput guarantees.

Receipts are written before POST; responses and retry usage survive restart.
Unknown completion pauses admission and blocks duplicate requests. Explicitly
terminated OOM gets at most one retry after backoff/health verification. Scene
truncation has its own single retry with output reserve growing 4096 to 8192.

The native delivery entrypoint is `runpod-v6.ps1` / `deployment/runpod/local_v6.py`.
Shared offline/SSH/bundle helpers are now `native_prepare.py` and
`native_transport.py`; the old `v6.py`, `sync.py` and `legacy` CLI lane are removed.
Source/config changes require a new frozen batch. Existing identity, canary,
checksum and publication checks remain mandatory. Qualification receipts must come
from an explicitly authorized real GPU run and match the frozen profile; this
refactor does not authorize access, provision, upload or execution on Runpod.
