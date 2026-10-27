# Manga pipeline v7

Tài liệu hiện hành: **[docs/v7](docs/v7/README.md)** — kiến trúc toàn bộ pipeline,
stage, models, cấu hình có thể chỉnh, artifact, Runpod và resume.
Xem [chỉ mục docs](docs/README.md) để phân biệt hướng dẫn hiện hành với lịch sử.

v7 dùng Koharu + MAGI + GLM-OCR, Qwen/vLLM, analyze batch độc lập,
`build_identity` và `relationship`, dịch natural song song và OCR-box typesetting.
Các module/CLI/schema vẫn giữ tên v6 để tương thích checkpoint; không đổi
`pipeline_version` hoặc section config thành v7.

New runs split semantic identity into `build_identity` (protected name/visual links
and character profiles) and `relationship` (social relations and independent
directed Vietnamese address edges). Both stages allow at most 8 concurrent
requests and no automatic inference retry. Translation consumes their frozen
artifacts. See [identity and relationship v2](docs/v6/identity-relationship-v2.md)
for configuration, version history, fallback behavior and resume rules.

Current guide: [v7 pipeline](docs/v7/README.md).
Technical history: [v6 independent analysis](docs/v6/independent-analysis.md).
Implementation and offline validation: [report](docs/plan/v6-only-parallel-analyze-refactor-implementation-20261021.md).
New runs use the independent contract, llm/vLLM and OCR-box typesetting.

The following v5 notes are historical; their legacy commands/configuration do not apply to new runs.

# Manga pipeline v5 — Koharu + MAGI

[Implementation and measured chapter results](docs/news/dialogue_v3_implementation.md).

MAGI extraction with persistent character IDs, essential-only text dialogue analysis and character-name linking.
No scene segmentation or model reading-order correction. Text inference uses `think:false`.

Start from an image folder with `python run.py run <folder> --story <story> --chapter <chapter>`.
Continue with `python run.py resume --run-dir <run-directory>`. Completed steps and targets are reused.
Legacy MAGI-only extraction can be reanalyzed only with an explicit config disabling layout. It cannot be reused as Koharu extraction; start from the original images for a new v5 run.

New runs use three presentation groups with independent resumable checkpoints:

- Extraction: `layout → extract → bubble_geometry` (the `extract` CLI command completes this group).
- Analysis: `normalize → scan → analyze`.
- Output: `export → translate`.

Normalize filters whole boxes of unrelated noise with versioned rules. New runs translate only natural Vietnamese, reuse analyze batch memberships and use a frozen relationship/address table. Translation supports up to 32 concurrent requests, relationship inference up to 8, and analyze up to 8, with adaptive admission, resumable receipts and manual natural overrides. See [natural translation and reusable relationships](docs/v6/translation/natural-parallel-relationships.md). Existing output artifacts are retained.

Koharu is mandatory/default for new runs. Locally, install `requirements-visual-layout.txt` in a separate Transformers 5 environment and set `layout.python` to that interpreter in your config. Use that interpreter to run `scripts/setup_visual_models.py --only layout`. Missing/corrupt weights or an incompatible worker fail before creating a run or touching its bank. MAGI remains in its Transformers 4 environment. New defaults: character matching **0.75**, character detection **0.35**. Use a fresh bank; legacy MAGI geometry IDs are not imported into Koharu runs.

On Runpod, `manga setup` prepares both independently signed environments on the persistent runtime volume; `manga prefetch` prepares the pinned Koharu weights plus MAGI/NER/Ollama caches; `manga doctor --inference` checks both workers and assets. The managed config selects the actual visual interpreter and model path. The Docker image remains code-only. These source changes still require a code sync or a new image before they take effect on a pod.

`bubble_geometry` is heuristic visual evidence, not speech classification. New runs save `speech_style_policy: semantic-speech-v1`; analyze produces `speech_style` (`normal`, `shout`, `whisper`, `thought`, `caption`, `unknown`), evidence, source and review status. Those fields plus bubble/source-box provenance reach export and translation requests. Unknown/conflicting styles need review; nonessential text is explicitly `not_analyzed`. Merged texts retain each source's bubble metadata without inventing aggregate geometry.

Inpaint is disabled by default and was not part of this refactor's validation. If explicitly enabled after preparing AOT assets, `--until inpaint` runs its prerequisites and reconstruction branch. Inpaint does not render translated text. Koharu layout failures block extraction; AOT failures leave inpaint partial while analysis can continue.

New runs save `speaker_policy: individual-v1`: speakers are supported character IDs, `others` or `narrator`. Narrator requires narration; speaker `groups` is suspended. Saved v3 runs without this field retain their previous contract. [Speaker policy and compatibility](docs/news/speaker_policy_individual_v1.md).

Use `python run.py translate --run-dir <run-directory>` to translate a committed legacy export directly. This does not refilter or rewrite the source run. [Vietnamese translation, glossary, manual edits and future graph context](docs/news/vietnamese_translation_usage.md).

The run's `review.md` shows per-step runtime and the recorded total across retries. Legacy steps without timing remain unknown. [Regression tests and the bilingual chapter pilot](docs/news/vietnamese_translation_validation_20261005.md).

Open `<run-directory>/review.md` to check compact tables for each step and links to images. Reviews are generated automatically; use `python run.py review --run-dir <run-directory>` to create them for an existing run without model inference.

- [Installation, commands and resume](docs/news/usage.md)
- [Reusable Runpod RTX 5090 Community setup](docs/news/runpod_reusable_setup_vi.md)
- [Runpod agent fast-batch policy (required before Runpod work)](docs/news/runpod_agent_fast_policy_vi.md)
- [Implementation and validation](docs/news/refactor_report.md)
- [Current semantics, pair checks, name binding and resume](docs/news/dialogue_semantics_redesign.md)
- [Joint model probe and practical limits](docs/news/joint_semantic_probe_20261004.md)
- [Legacy essential dialogue policies](docs/news/essential_dialogue.md)
- [GLiNER name detection and chapter scan results](docs/news/gliner_names.md)
- [Agreed design](docs/refactor_plan_vi.md)
- [Directory layout](docs/refactor_directory_layout_vi.md)
- Historical experiments and documentation are under `scripts/experiments/` and `docs/archive/`.

Use `python -B -m unittest discover -s tests -v` for the offline regression suite.
