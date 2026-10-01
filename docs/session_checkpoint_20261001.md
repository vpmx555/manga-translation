# Checkpoint — 2026-10-01

User requested stopping this approach, saving the project to GitHub `master`, and redesigning tomorrow. Do not automatically resume inference.

## Saved implementation

- MAGI extraction, dialogue normalization, local Ollama scene reasoning, Stage 3A/3B/3C/3D, resource profiles, validated request cache, evaluation and chapter runner.
- Stage 3D only reorders utterances within the same scene and page.
- Chapter runner writes separate output and a consolidated `check.json` with stage snapshots. Changed snapshots can retain previous versions in `step_revisions`.
- Atomic checkpoint replacement retries temporary file locks on Windows.

## Actual run and failure

Run directory: `outputs/chapter1-stage3-20261001/` (local, excluded from Git).

- Source: `DEFAULT_IMAGE_FOLDER` in `src/main.py`, Chapter 1, 13 PNG pages.
- Stage 1 complete: 53 panels, 109 OCR text boxes.
- Normalization complete: 107 retained utterances, 2 excluded.
- Visual observations complete; validated inference responses remain in `internal/cache/`.
- Initial scene inference and seam review completed, but produced 102 scenes: 101 boundary decisions, 3 no-boundary decisions, 2 uncertain decisions.
- This scene result is **not accepted**: many `boundary` reasons describe sequential dialogue or continuous thoughts, contradicting the intended scene label.
- The runner was stopped at the start of Stage 3A. No final `stage3/structured_dialogue.json` was produced. `check.json` is marked paused.
- A Windows `Access is denied` checkpoint replacement failure occurred earlier; retry handling was added and tested. Resume preserved completed extraction and cache.

## Unverified changes saved for tomorrow

`BOUNDARY_PROMPT` now explicitly defines `no_boundary` as the same narrative scene and `boundary` as a new scene, with continuity and time-jump examples. This prompt has **not** been validated through real inference or rerun over the chapter.

`scripts/check_scene_labels.py` can probe adjacent utterances on a chosen real page. The probe was not launched before the user requested stopping. Treat it as a development aid, not evidence of model quality.

Verification before saving passed all 51 tests, including temporary/permanent checkpoint locks. Automated contract tests do not establish scene quality.

## Next session

Redesign and validate the approach on a small manually inspected sample before spending CPU time on another full run. Review scene-label semantics, source reading order, and noise removal. Preserve the existing run as evidence; do not present its scene output as correct or claim Stage 3 completion.

Local model caches, character banks, inference output, environments and conversation journals remain on disk and are excluded from Git. Source, configuration, tests and project documents are saved in the repository.
