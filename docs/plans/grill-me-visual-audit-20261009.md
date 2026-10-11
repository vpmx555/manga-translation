# Visual pipeline audit — 2026-10-09

Status: design confirmed by the user and source implementation complete. Functional tests cover the new contract, metadata propagation, targeted repair, hook cleanup, managed runtime setup and legacy resume. No new Pod or real-GPU visual integration was performed.

## Observed run versus source before this refactor

- Downloaded Habatake run `20261009T093726Z-f95ec3f0` is pipeline v4 with `layout.enabled=false` and `inpaint.enabled=false`.
- Its extract, normalize, analyze, export and translate result JSONs contain no `bubble_geometry` objects.
- Current source contains a conditional v5 Koharu path. Existing v4 output does not demonstrate that path.
- Deployment defaults to `configs/pipeline.json`; this config omits layout/inpaint. Visual defaults are disabled.
- `configs/pipeline-visual-runpod.json` enables layout/inpaint but names `/opt/manga-visual-venv/bin/python`.
- Current Dockerfile copies code and checks syntax. Current Runpod setup installs the main environment, but does not create that visual interpreter or prepare Koharu/AOT assets. Selecting the visual config alone is insufficient for this deployment.

## Panel provenance

- `src/manga_pipeline/extraction/magi.py:61` reads committed layout pages only for pipeline v5.
- `src/manga_pipeline/visual/magi_panels.py:12` temporarily replaces MAGI's module-local `sort_panels`, injects Koharu boxes before sorting/assignment, and restores the function afterward.
- `src/manga_pipeline/extraction/magi.py:85` records `panel_source=koharu` for that branch.
- `src/manga_pipeline/visual/stages.py:96` verifies extracted panel boxes equal committed Koharu boxes.
- Extraction visualizations still use MAGI's rendering function on the prediction result. The panel source depends on whether the v5 hook actually ran.
- `tests/test_visual_pipeline.py:98` tests tensor replacement on a model probe. This is not a real Koharu/MAGI GPU integration test or a detector-accuracy benchmark.

## Bubble data propagation

- `visual/bubbles.py` classifies contour shape and border using heuristic rules. Fields include `bubble_shape`, `border_style`, `bubble_style`, `style_hints`, confidence and evidence.
- Styles include `normal`, `shout_hint`, `thought_hint`, `caption_hint`, `whisper_hint` and `unknown`. They are hints, not verified semantic speech labels.
- `visual/stages.py:80` adds `bubble_id`, `bubble_bbox`, `bubble_mapping`, `bubble_geometry` to texts and classifies page bubbles.
- Normalize deep-copies essential rows and explicitly retains bubble fields for translation-only rows.
- Confirmed merges spanning multiple bubble IDs remove aggregate bubble fields. Per-source bubble information is not retained in `source_boxes`.
- Analysis requests include geometry, with an explicit prompt warning that it is fallible evidence. Analysis result JSON itself does not copy all source geometry.
- Export deep-copies normalization, so bubble metadata remains in utterances/translation-only rows when present upstream.
- Translation targets and source snapshots retain complete source rows. Translation result rows retain these under `source`.
- `translation/source.py:37` creates a restricted view for translation model requests that omits bubble geometry/style. Thus source metadata survives storage, but the translation model does not receive it.
- Excluded normalization records also do not duplicate all bubble fields; original raw records remain available upstream.

## Thresholds

- Main config/default character-to-character matching threshold: 0.85.
- Visual config overrides character-to-character matching threshold: 0.80.
- Detection wrapper fallback: 0.65. This inconsistency should be resolved.
- Requested target: 0.75. Confirm that this names `character_character_matching_threshold`, rather than bank embedding distance or text-character affinity.
- The pinned local MAGI source defaults character detection confidence to 0.30; the application currently neither exposes nor forwards this parameter.
- Accepted slight increase: 0.35. This may reduce false detections and miss faint/small/background characters; comparative accuracy remains unmeasured because the user declined image-comparison testing.
- Character bank ID assignment also uses `max_distance` and `min_margin`, separate from MAGI matching. Do not conflate these controls.

## Design frontier

1. Bubble schema: preserve visual evidence only, or add a separately sourced semantic speech label? Which stages need standalone metadata versus stable source references?
2. Default execution: make Koharu mandatory for new production runs, or require an explicit visual mode? Decide the processing order and optional inpaint branch afterward.
3. Threshold intent: map 0.75 to MAGI character-character matching; decide the exact character detection confidence increase.

After these answers, settle stage grouping, inpaint scope, compatibility with old runs/banks, deployment setup and acceptance checks. Follow the invoked grill-me confirmation workflow before implementation.

## Accepted decisions and authorized threshold implementation

- Bubble geometry remains fallible evidence; add a separately sourced semantic speech style and preserve both through export/translation.
- Koharu will be mandatory for new runs; unavailable visual prerequisites must cause an early, explicit error. Old runs remain readable/resumable.
- Set MAGI character-character matching to 0.75 and character detection confidence to 0.35 in built-in defaults and all three pipeline config files.
- Detection is now exposed, range-validated and forwarded by the MAGI detection wrapper.
- Older manifests without character detection configuration keep the original 0.30 when resumed; extraction reuse rejects a changed detection threshold.
- Configuration loading, default forwarding, explicit overrides and invalid-value rejection were checked successfully. No real-image threshold comparison was performed, per the user's request.
- Stage organization accepted: keep internal checkpoints, group progress/review into Extraction, Analysis and Output.
- Inpaint is temporarily out of scope and disabled for the default run.
- Old MAGI banks are discarded; do not migrate or reuse their identities for new Koharu runs.

## Confirmed implementation scope

1. New local and Runpod runs use Koharu by default, with early checks of the selected interpreter, packages and pinned assets. Missing prerequisites produce an explicit failure before pipeline inference. No silent MAGI-only fallback.
2. Koharu owns panel geometry; MAGI retains OCR and character detection/associations. Preserve panel provenance and verify boxes against the committed layout. Visualizations and review paths must use the actual stage registry.
3. Keep independent layout, extract, bubble_geometry, normalize, scan, analyze, export and translate checkpoints. Group progress/review as Extraction (first three), Analysis (normalize/scan/analyze), Output (export/translate). Inpaint remains disabled for now.
4. Preserve bubble IDs, mapping, shape, border and heuristic evidence from geometry through downstream source records, including per-source metadata when merging utterances. Attach a separately sourced semantic speech_style in analyze; distinguish normal speech, thought, shout, whisper, narration and unknown. Preserve provenance/review status; ambiguous evidence remains unknown. Do not retroactively invent semantic labels before analyze.
5. Export and translation receive both geometry and the semantic conclusion. Translation model views must expose the relevant metadata, rather than merely preserving it in disk snapshots. Stages with specialized compact results retain stable source references.
6. New defaults use matching 0.75 and character detection 0.35 (already implemented). Preserve stored settings for historical run reads/resumes. No image-comparison experiment; verify configuration, contracts, propagation, hook lifecycle and resume behavior with functional checks.
7. Runpod setup/prefetch/doctor must explicitly support the isolated visual environment and model assets; configuration must refer to the managed environment actually created. Rebuild preparation is local; no new billable Pod is included in this source-refactor scope.
8. Delete only the verified legacy Bocchi/Habatake bank directories listed below. Keep historical output JSON/images/reviews and unrelated story banks. New Koharu extraction creates new identities; old IDs are not claimed equivalent.

Verified legacy bank deletion targets, relative to repository root:

- `banks/bocchi-the-rock`
- `.runpod/backups/banks-4090-20261005/bocchi-the-rock`
- `.runpod/backups/banks-bocchi-20261009/bocchi-the-rock`
- `.runpod/backups/banks-habatake-20261009/bocchi-the-rock`
- `.runpod/backups/banks-habatake-20261009/habatake-majutsu-sekai-no-shitei-tachi`

All five targets were resolved under the repository, checked against their metadata story_key, and checked for legacy geometry and directory reparse points before deletion. They were permanently deleted after the user's confirmation. Historical outputs and unrelated After-school banks remain intact. These deleted bank copies are not recoverable from these backup directories; output snapshots alone are not a complete bank backup.

## Implemented behavior and validation

- `configs/pipeline.json` explicitly enables Koharu and disables inpaint; all configs use matching 0.75 and detection 0.35.
- Production `create_run` checks checkpoint hashes and worker imports/versions before creating a run or preparing a bank. It rejects MAGI-only new runs. Legacy manifests remain resumable; explicit legacy reanalysis cannot claim Koharu geometry.
- New v5 manifests stamp `speech_style_policy: semantic-speech-v1`. Saved manifests without that marker keep the old semantic request contract. Labels are `normal`, `shout`, `whisper`, `thought`, `caption`, `unknown`; content_type continues to describe dialogue/thought/narration independently.
- Analyze/export carry speech_style, speech_style_evidence, speech_style_source and speech_style_status. Invalid model conclusions receive one targeted repair; unresolved styles become unknown/needs_review without discarding independent speaker/listener facts. Nonessential text is not_analyzed.
- Bubble metadata survives normalized source boxes, merges, excluded/nonessential text, analysis source references, export and translation model views. Review uses actual stage directories and shows pages even if no essential text remains.
- Runpod setup manages a separately signed, hash-locked Koharu environment; prefetch downloads only layout weights, and doctor/new-run preflight validates assets and worker. Existing code-only image strategy is retained.
- Offline regression suite: 158 tests run, 156 passed, 2 Linux-only integration tests skipped on Windows. Repository-native `git diff --check` passes. Tests do not measure real image detection accuracy or prove CUDA model inference.

Deployment boundary: the previously pushed image and downloaded v4 outputs have not been changed. A later code sync or new image build, managed setup/prefetch, and a real GPU smoke run are still required before claiming deployed Koharu output.

Risks: 0.35 may miss low-confidence characters and 0.75 may over-associate similar characters; real-image comparative accuracy is not measured because the user excluded that test. Geometry-based style hints remain fallible. Fresh banks reset stable identities and old run outputs must not be silently reinterpreted as Koharu outputs. Current hook unit tests use a model probe; real GPU visual integration is a later deployment check, not a result of this source audit.
