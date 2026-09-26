# Manga translation with persistent character IDs

This project runs MAGI v2 chapter-wide OCR and maintains a separate character
bank for each story. Anonymous speakers are emitted as `<1>`, `<2>`, and so
on; uncertain first sightings remain `<Other>`.

## Run a chapter

```powershell
python src/main.py "D:\Manga\One Piece\chapter-001" --story-name "One Piece"
```

Useful options:

- `--story-name`: display name and default bank key. If omitted, the image
  folder name is used.
- `--story-id`: optional explicit bank key.
- `--chapter-id`: stable chapter key used to distinguish page observations.
- `--bank-root`: defaults to `data/character_banks`.
- `--max-distance`: default `0.65`.
- `--min-margin`: default `0.08`.
- `--device auto|cpu|cuda`.

The bank contains `metadata.json`, `embeddings.npz`, reference crops, and a
per-story lock. Writes use temporary files followed by atomic replacement.

## Identity rules

- A new MAGI cluster containing at least two detections receives an ID
  immediately.
- A singleton remains `<Other>` until a matching observation appears on a
  different page.
- Promotion rewrites earlier `<Other>` occurrences in the current chapter.
- IDs are story-wide, monotonically increasing, and never reused.
- Multiple diverse MAGI embeddings and up to four reference crops are retained.
- Two clusters may use the same identity in different panels. Different
  clusters in the same panel cannot.
- A confident match must pass both the distance threshold and nearest-candidate
  margin. Ambiguous observations remain `<Other>`.

## Bank maintenance

Maintenance commands do not load MAGI when `--bank-only` is present:

```powershell
# Change only the display label; internal ID remains stable.
python src/main.py "D:\Manga\One Piece\chapter-001" --story-name "One Piece" --bank-only --rename-character "1=Luffy"

# Merge source ID 7 into target ID 1.
python src/main.py "D:\Manga\One Piece\chapter-001" --story-name "One Piece" --bank-only --merge-character "7:1"

# Disable a bad embedding prototype or crop without deleting its file.
python src/main.py "D:\Manga\One Piece\chapter-001" --story-name "One Piece" --bank-only --disable-prototype "1:2"
python src/main.py "D:\Manga\One Piece\chapter-001" --story-name "One Piece" --bank-only --disable-crop "1:0"
```

## Optional post-processing (CAFormer-ready)

`src/character_postprocessing.py` defines
`AmbiguousMatchPostprocessor.resolve(...)`. The hook is invoked only when
MAGI's distance/margin rules cannot make a confident choice. A future CAFormer
adapter can inspect the query crops and the top candidate crop paths, then:

- return a candidate ID as supporting evidence;
- return `character_id=None` to veto the match; or
- return `None` to preserve the default `<Other>` result.

Pass plugin instances to `main.run(args, postprocessors=[...])`. This keeps the
base bank independent of `imgutils`, Hugging Face authentication, gated model
downloads, and CAFormer licensing until that optional integration is enabled.

## Tests

```powershell
python -m unittest discover -s tests -v
```
