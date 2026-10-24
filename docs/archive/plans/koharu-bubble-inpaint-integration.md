# Koharu layout, bubble geometry and inpaint integration

Status: implementation and offline validation completed on 2026-10-08. Live Koharu/AOT sample inference remains pending model installation and weights.

## Scope and decisions

1. Replace MAGI panels with Koharu panels throughout the pipeline: reading order, text/character panel assignment, panel-aware character-bank constraints, and analysis context. Keep MAGI text detection, OCR, character association, and existing matching thresholds.
2. Add Koharu bubble masks/contours and associate them with MAGI text using geometric evidence. Preserve unmatched text, OCR, and character association; use null bubble IDs and unknown styles instead of forcing nearest-bubble assignments.
3. Classify bubble geometry with deterministic rules only. Shape labels: rounded, cloud, spiky, rectangular, irregular, unknown. Border labels: solid, dashed, unknown. Keep bubble_style separate from content_type; retain confidence and evidence. Semantic style hints must be conservative, with unknown when geometry is insufficient. Analyze combines this evidence with text to determine dialogue/thought/narration/unknown.
4. Add an inpaint stage producing clean pages and masks, without translated-text rendering. Erase only text retained by normalize for translation, including essential and translation_only. Preserve excluded text such as excluded SFX/logos.
5. White-fill only when the background is confidently white. Sample the bubble interior around text while excluding text and border/tail pixels. Otherwise use AOT. Erase text regions rather than whole bubbles; use reliable bubble geometry to protect borders.
6. Combine all AOT regions on a page into a single inference pass. Retain the source implementation's approximately 1024-pixel maximum inference edge. Composite only through the erase mask at original resolution. Cache the model within a worker.
7. Inpaint depends on normalize and geometry, not analyze results. It can run independently or concurrently with scan/analyze/translate. Logical concurrency does not require overlapping workloads on a shared GPU; resource scheduling must avoid VRAM contention.
8. Koharu failure blocks layout-dependent work. AOT failure leaves inpaint incomplete while analysis/translation can continue. Save per-page checkpoints and resume unfinished work. Never silently substitute MAGI panels or white fill on backend failure.
9. Extend input discovery to AVIF/WebP as needed; current primary input discovery only includes PNG/JPG/JPEG.
10. Validation is staged: mapping/order/mask/resume tests and sample inference on Psycho Eyes and Bocchi, with layout overlays and clean-page artifacts. Record timing, failed pages, unmatched text and unknown styles.

## Proposed stage boundaries for confirmation

- layout: Koharu panel and bubble detection, masks/contours, geometry provenance.
- extract: MAGI text/OCR/characters with Koharu panels supplied to relevant ordering and assignment paths.
- bubble_geometry: text-to-bubble mapping and deterministic shape/border/style evidence.
- normalize: retain geometry and select text to translate/erase.
- analysis branch: scan -> analyze -> export -> translate.
- reconstruction branch: inpaint -> clean-page and mask artifacts.

The proposed boundaries describe separate resumable stages and explicit dependencies. Exact CLI stage names and internal module names remain implementation details. Existing run artifacts must not be silently rewritten or treated as compatible with the new geometry; model/config/input changes must invalidate relevant downstream checkpoints.

## Refactoring approach

Port the relevant detector and AOT functionality into pipeline-owned modules and model adapters, without depending at runtime on the downloaded AIP419 directory. Keep its EasyOCR implementation outside this scope. Separate model loading/inference from geometry rules, orchestration, storage and review output. Record model revision/config and coordinate provenance in artifacts.

## Risks and validation limits

- Geometric shape/border evidence cannot reliably establish every semantic style, especially whispering or thought.
- Changing panels can alter reading order and character-bank constraints even while retaining MAGI association algorithms.
- Missed bubbles and ambiguous overlaps require conservative unmatched handling.
- Mask dilation/clipping must balance complete glyph removal against border preservation.
- Whole-page downscaling can reduce detail in small text and screentones; crop-based AOT is deferred.
- CPU/GPU timings are not benchmarked. Background checks are lightweight pixel operations; additional AOT-only pages are the main incremental compute cost.
- Koharu and AOT weights are not present in the downloaded source directory. Model preparation and environment compatibility must be checked when execution is authorized.

## Deferred or rejected scope

- Replacing MAGI OCR with EasyOCR.
- VLM-based bubble classification.
- Automatic translated-text layout/rendering.
- Per-bubble or crop-based AOT inference.
- Forced nearest-bubble matching and silent backend fallbacks.
- Immediate full-dataset processing.

## Validation status

- Offline regression suite: 141 tests passed, 2 skipped. Mapping ambiguity, MAGI panel hook, border protection, white fill/excluded text, single-pass AOT masking, and checkpoint reuse have focused tests.
- The local AOT adapter's 168 parameter names and tensor shapes match the pinned SafeTensors checkpoint metadata.
- Koharu and AOT checkpoints are not installed locally, and the separate `rfdetr==1.7.0` environment is not prepared. Psycho Eyes and Bocchi inference, overlay inspection, and clean-page quality review remain to be run.
