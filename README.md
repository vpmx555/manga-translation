# Manga pipeline v4

[Implementation and measured chapter results](docs/news/dialogue_v3_implementation.md).

MAGI extraction with persistent character IDs, essential-only text dialogue analysis and character-name linking.
No scene segmentation or model reading-order correction. Text inference uses `think:false`.

Start from an image folder with `python run.py run <folder> --story <story> --chapter <chapter>`.
Continue with `python run.py resume --run-dir <run-directory>`. Completed steps and targets are reused.
Use `python run.py reanalyze --run-dir <old-run-directory>` to reuse completed MAGI extraction in a new analysis run.

New runs use `extract → normalize → scan → analyze → export → translate`. Normalize filters whole boxes of unrelated noise with versioned rules. Translate produces separate natural and localized Vietnamese JSON/reviews, with resumable checkpoints and manual sentence locks. `analyze` resolves text and binds scanned names after each batch, with checkpoints for text, panel decisions, bank updates and participant memory. Saved v2/v3 runs retain their original flow.

New runs save `speaker_policy: individual-v1`: speakers are supported character IDs, `others` or `narrator`. Narrator requires narration; speaker `groups` is suspended. Saved v3 runs without this field retain their previous contract. [Speaker policy and compatibility](docs/news/speaker_policy_individual_v1.md).

Use `python run.py translate --run-dir <run-directory>` to translate a committed legacy export directly. This does not refilter or rewrite the source run. [Vietnamese translation, glossary, manual edits and future graph context](docs/news/vietnamese_translation_usage.md).

The run's `review.md` shows per-step runtime and the recorded total across retries. Legacy steps without timing remain unknown. [Regression tests and the bilingual chapter pilot](docs/news/vietnamese_translation_validation_20261005.md).

Open `<run-directory>/review.md` to check compact tables for each step and links to images. Reviews are generated automatically; use `python run.py review --run-dir <run-directory>` to create them for an existing run without model inference.

- [Installation, commands and resume](docs/news/usage.md)
- [Reusable Runpod RTX 5090 Community setup](docs/news/runpod_reusable_setup_vi.md)
- [Implementation and validation](docs/news/refactor_report.md)
- [Current semantics, pair checks, name binding and resume](docs/news/dialogue_semantics_redesign.md)
- [Joint model probe and practical limits](docs/news/joint_semantic_probe_20261004.md)
- [Legacy essential dialogue policies](docs/news/essential_dialogue.md)
- [GLiNER name detection and chapter scan results](docs/news/gliner_names.md)
- [Agreed design](docs/refactor_plan_vi.md)
- [Directory layout](docs/refactor_directory_layout_vi.md)
- Historical experiments and documentation are under `scripts/experiments/` and `docs/archive/`.

Use `python -B -m unittest discover -s tests -v` for the offline regression suite.
