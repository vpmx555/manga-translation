# Bounded GPU runtime for A40, RTX 4090 and RTX 5090

Implemented 2026-10-13 from the confirmed grill-me design. This change prepares code only; no Runpod access, inference, pipeline run or benchmark was performed. Model, prompts, identity rules, source integrity and review artifacts remain part of the existing contract. Adaptive batching can change generated analysis and translations. Acceptance covers structural correctness and coverage, not comparative linguistic quality.

## Enable for a new frozen batch

Both controllers accept `prepare --gpu-preset a40|rtx4090|rtx5090`:

```powershell
# Offline preparation only. Supply the existing frozen dependency-lock directory.
& '.\venv\Scripts\python.exe' -B deployment/runpod/local_v6.py prepare `
  'D:\download\EN-data\Bocchi\Chapter 1' `
  --batch 'gpu-runtime-bocchi-v6' --story bocchi --chapter chapter-001 `
  --gpu-preset rtx5090 --locks '<existing-lock-directory>'
```

Add other chapters with the existing `--additional-chapter CHAPTER FOLDER` and independent stories with `local_v6.py`'s `--additional-story STORY CHAPTER FOLDER`. These flags prepare input/source/config; they do not launch anything. Remote launch and benchmarking still require an explicit current-task request for Runpod. Do not reuse the old batch ID, overwrite previous outputs or edit a prepared config after its hash is frozen.

The option adds `runtime: {enabled: true, preset: ...}` to the prepared `pipeline-v6.json` before config/source hashing. A disabled runtime still provides the sequential batch path. The current `configs/pipeline-v6-runpod.json` explicitly enables `rtx5090`, so omitting the flag with that file does not disable parallelism. Enabled runtime must run through the batch controller; direct `run.py` without its shared coordinator is rejected. Low-level limits can be supplied through the runtime config before preparation; do not modify an existing frozen plan.

## Execution and ownership

One batch holds the global chapter-batch lock before touching coordinator state. Each story owns a worker, and that worker runs its chapters in plan order. Worker subprocesses share a read lock on the global pipeline, protecting them from setup, source updates or server stop; each also holds a story-specific write lock. Input checks, chapter verification, review generation, atomic publication and failure preservation remain enabled. State records stay indexed in original plan order; `active_chapters` lists the simultaneous jobs.

The GPU coordinator admits either one visual owner or multiple LLM requests up to the effective slot limit. Layout, extraction, CUDA geometry, inpaint and explicitly CUDA NER use exclusive visual admission. LLM requests use shared admission. FIFO waiting prevents a newly arriving LLM from bypassing a queued visual stage. CPU preparation, validation, rendering and publication can overlap with GPU inference. Translation history and chapters within a story remain ordered. Dual-branch analyze allows caption/dialogue concurrency; independent scenes may also overlap, while newly inferred aliases require ordered scene waves. Each branch keeps its own sequential forward/backward memory. See [dual-branch handoff](dual-branch-parallel-handoff-vi.md).

Before visual execution the coordinator unloads the managed LLM, after all admitted LLM calls have finished. Per-story provider teardown no longer unloads the model needed by another story. The batch changes the managed server's parallel/thread profile only at an idle boundary under the exclusive global pipeline lock. It reuses the pinned server version and rejects an unmanaged server. Restarting a server for a new profile is code behavior for a future authorized run, not an action performed during implementation.

GPU admission state lives under `MANGA_EXECUTION_ROOT/coordination/<batch-path-hash>` to avoid polling durable volume files. Waiters do not rewrite unchanged state. Telemetry remains in the batch jobs directory for retained audit/publication.

## Initial presets

NER configured as `auto` also takes exclusive GPU admission because it may select CUDA; CPU NER remains outside that gate.

| Preset | Story workers | Server/admission slots | Typeset threads per story | LLM CPU threads | CPU library threads per story |
|---|---:|---:|---:|---:|---:|
| a40 | 3 | 2 | 2 | 8 | 4 |
| rtx4090 | 3 | 2 | 2 | 8 | 4 |
| rtx5090 | 3 | 2 | 3 | 8 | 4 |

The table contains base presets. With the selected 27B model and no explicit `runtime.llm_slots`, model-aware resolution uses **2 slots on A40, 1 on RTX 4090 and RTX 5090**. An explicit frozen override takes precedence, but must be validated against model/context VRAM in a separately authorized run. Both preparation controllers now accept `--llm-slots`, `--story-workers`, `--analyze-workers` and `--caption-max-distance`; these values are applied before hashing.

These are bounded starting points, not measured optima. CPU counts cap the resolved thread limits, reserving headroom for server/driver/sync. The server has one loaded LLM; two slots can increase context/KV memory even when only one request is admitted. Reducing admission after OOM does not rebuild the server's fixed slot/KV allocation; persistent OOM may still require a new preset with one slot. No 100% GPU utilization or speedup is promised.

All runtime fields and limits are defined in `src/manga_pipeline/runtime/resources.py`. Defaults: adaptive batches enabled in optimized runtime; analyze/translation caps 8 targets; estimated context fraction 0.85; recovery splits at most 3 per cursor; gate timeout 3600 seconds; GPU sampling every 10 seconds. Runtime is disabled unless explicitly selected. Batch estimation uses the provider's conservative byte heuristic plus schema/image/output reserves; it is not exact model tokenization.

## Batching and recovery

Analyze picks the largest permitted contiguous same-page batch that fits the estimated budget. It keeps preceding semantic turns/state and rebuilds requests after a split. Translation picks contiguous targets, includes the existing context radius and prior accepted translation history for the current style, and advances only after a batch succeeds or records an explicit failure. Neither step runs dependent history batches concurrently. Styles remain sequential within a story; concurrency comes from independent stories and typeset shortening requests.

Explicit OOM reduces effective client slots to a minimum of one and permits one infrastructure retry of that HTTP request. This is separate from the single semantic repair budget. If necessary, analyze/translate split the batch with a bounded recovery counter. Translation structural rejection also permits a bounded split; analyze retains its existing per-target single-repair/source-only semantics. No context truncation, target omission, model replacement or CPU inference fallback is used. Singleton failure remains a failure.

Semantic repair receipts distinguish `resource_failed` from semantic rejection and preserve a bounded resource retry count across resume. Resource failure cannot silently exhaust the semantic budget and become an unresolved fallback. Typeset shortening likewise propagates resource errors rather than storing a permanent semantic rejection. Hayai OCR reduces crop batch size on explicit CUDA OOM, retaining patch limits, output limits, crop order and alignment; model-load OOM or singleton OOM still fails.

A transport timeout may leave inference alive on the server. The coordinator quarantines new admission on such failure or a dead active lease owner. A subsequent controller restart requires previous workers to be stopped and restarts the managed server at the idle boundary. Detached orphan visual workers must also be stopped before resuming; PID receipt cleanup alone cannot prove GPU work has exited. Concurrent duplicate controllers are rejected.

## Typeset CPU work

Typeset uses a bounded page thread pool and merges output in original page/style order. Each page processes natural/localized sequentially; target writes and upstream verification reads serialize through a store proxy. Per-thread font cache is capped at 128 entries and width cache at 4096. The existing font search, line wrapping, glyph containment and review flags are retained.

Pillow/NumPy/SciPy work may overlap, but Python layout search remains subject to the GIL. This implementation does not claim that every part of typesetting scales across cores or runs on GPU. Full GPU typography would require a separate backend and equivalence work.

## Observability and validation status

`jobs/runtime/effective-config.json` records requested/resolved limits and coordinator location. Per-process JSONL streams record queue waits, stage wall/process CPU time, request load/prompt/decode durations, cache hits, splits, OOM backoff and GPU samples. `final-gpu-state.json` records effective slot count. The monitor stops before final audit archiving so those telemetry files are immutable during publication. CPU timing from the pipeline process excludes child worker CPU; GPU samples and process CPU are complementary evidence, not proof of GPU kernel time.

Static AST syntax and whitespace/integration review are allowed by project policy and were performed. `tests/test_gpu_runtime_resources.py` contains hardware-free regressions for slot bounds, visual exclusivity/FIFO, lease cleanup, backoff and dead-owner quarantine. Tests were written but not executed. No model pilots or remote validation were performed, so throughput, GPU-version behavior and comparative semantic quality remain unverified.
