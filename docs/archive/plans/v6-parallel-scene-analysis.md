# Parallel scene analysis — offline revision

Requested to improve GPU utilization without modifying the active 32K Runpod batch.
Implementation is prepared locally; no new deployment or GPU benchmark has been performed.

## Configuration for the next batch

`configs/pipeline-v6-runpod.json` now requests:

```json
{
  "runtime": {"story_workers": 1, "llm_slots": 2},
  "v6": {
    "analyze_scene_workers": 2,
    "analyze_branch_workers": 2,
    "analyze_scene_context": "isolated-scenes-v1"
  }
}
```

The global admission gate permits at most two concurrent model requests; managed
vLLM now uses the configured slot count for `--max-num-seqs`. Server signatures
and receipts include this count, preventing reuse of an old single-sequence
server as evidence of concurrent serving. Full-GPU profiles permit bounded vLLM
slots 1..4, retain one story worker and prohibit CPU offload/GPU fallback.

## Scheduling and state

- Scene planning remains ordered because scene boundaries depend on preceding
  dialogue. Parallel execution starts after the complete scene plan is ready.
- At most two scenes are active with the supplied profile. Caption and dialogue
  retain their own history, participant state and forward/backward ordering.
- Each scene gets a private copy of the same frozen chapter person/name snapshot.
  Newly confirmed aliases from one scene are not input to another scene.
- Final semantic application and bank updates remain in original source order.
  Scene results, checkpoint receipts and failures are merged in scene order,
  regardless of thread completion order. A scene failure stops new scene admission;
  already running scenes finish and their partial checkpoints remain preserved.
- `isolated-scenes-v1` is recorded in the analysis plan. It can also run with one
  scene worker as a serial reference with the same scene inputs. Legacy defaults
  remain one scene worker plus `sequential-aliases-v1` context. Multiple scene
  workers with sequential alias propagation are rejected by config validation.

Different scenes can share characters and aliases. The explicit isolated context
policy changes cross-scene alias propagation compared with the legacy mode;
different scheduling under the same isolated policy does not share mutable
inference state. Exact token-for-token LLM equality is not guaranteed.

## Validation and rollout

Model-free regression fixtures cover scene overlap and bounds, deterministic
merge order, isolated snapshots, retention of completed partial results and
stopping unscheduled work after failure. Tests are prepared but not run under
the current project validation restriction. Static syntax/config validation is
allowed; it does not establish GPU throughput or peak-VRAM safety.

Do not hot-sync these changes into pod `uw3cmx25lvudbu` or alter its frozen plan.
The active batch retains its original single-slot source/config. A future run
must freeze a new batch with these changes, then verify real CUDA/inference and
VRAM before chapter execution. Two 32K sequences on RTX 5090 require a new GPU
readiness proof; worker counts alone do not establish improved utilization.
