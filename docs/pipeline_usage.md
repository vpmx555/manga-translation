# Dialogue pipeline usage

The current pipeline extracts and normalizes dialogue. The previous MPNet,
EmbeddingGemma and Qwen3 scene clustering experiment is saved on the
[cluster-scene-model branch](https://github.com/vpmx555/manga-translation/tree/cluster-scene-model)
at commit `5af465b`. Its scene, review, confirm-labels and benchmark commands
have been removed from the current CLI.

## MAGI output

MAGI runs export the legacy transcript plus `<output>.raw.json` and
`<output>.normalized.json`. Raw records retain OCR boxes and speaker links.
Normalization preserves original boxes and source records, keeps narration and
thought, and records excluded noise. Unresolved speaker identities remain
distinct from missing speaker links.

## Import existing utterances

Run from the repository root:

```powershell
python src/dialogue_pipeline.py import-utterances input.json --document-id chapter-1 --output chapter-1.raw.json
python src/dialogue_pipeline.py normalize chapter-1.raw.json --output chapter-1.normalized.json
```

Import preserves the input array order. Geometry unavailable in the source stays
empty. Choose a stable document ID when importing multiple chapters.

## Manual normalization decisions

`normalize` accepts `--content-overrides overrides.json` and
`--merge-groups merges.json`. Overrides must include a confirmed content type.
Merge groups must include source text IDs, `confirmed: true`, and a reason;
normalization retains each original source box.

Outputs must use new filenames and cannot overwrite inputs. The standalone CLI
does not load MAGI or any embedding model. Normalized `scene_id` fields remain
empty.

## Checks

```powershell
python src/dialogue_pipeline.py --help
python -m unittest discover -s tests -p "test_dialogue_pipeline.py" -v
```
