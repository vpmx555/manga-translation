# Pipeline MAGI → raw extraction → dialogue normalization → scene proposals

Chạy các lệnh bên dưới từ `D:\AIP491\manga-translate-v2`, không phải thư mục `tests`.
Các lệnh từ `src/dialogue_pipeline.py` không load MAGI, không dùng Ollama/Gemma 4 và không tự chọn embedding production.

## 1. Chạy MAGI và lưu cả dữ liệu gốc

Trong venv MAGI hiện tại:

```powershell
python src/main.py "D:\Manga\chapter-001" --story-name "My Story" --chapter-id "chapter-001" --output runs/chapter-001/transcript.txt --visualization-dir runs/chapter-001/images
```

Tạo transcript `.txt` tương thích luồng cũ, `transcript.raw.json` và `transcript.normalized.json`.
Đường dẫn JSON có thể chọn bằng `--raw-json` và `--normalized-json`. JSON đã tồn tại sẽ bị từ chối; chỉ thay khi có `--overwrite-json`.
`--no-visualizations` bỏ xuất ảnh đánh dấu. MAGI được pin ở revision khớp `MODEL_FINGERPRINT`.

Raw JSON giữ mọi OCR box, tọa độ và liên kết text/character/tail, kể cả text không essential.
`speaker_cluster_id` là cluster visual có namespace theo trang; `speaker_id` là ID ổn định của character bank.
Không suy ra ID từ tên hiển thị. `unlinked` khác `pending_identity`. Affinity không có trong output MAGI tiêu chuẩn nên được ghi `null`, không chế tạo confidence.

Normalized JSON giữ source IDs và từng source box. `text_original` được giữ; chuẩn hóa chỉ NFC/whitespace, không tự sửa nội dung OCR.
Scene chưa chạy thì `scene_id=null`, `scene_status=not_run`.

## 2. Dùng JSON 36 câu hiện có mà không chạy MAGI lại

JSON nguồn phải là mảng theo thứ tự đọc, hoặc object có mảng `utterances`/`dialogues`.
Các trường `addressee_type`, `addressee_ids`, `speaker_id`, `text`, `order_in_scene`, `page` được bảo toàn.
Không sắp lại theo `order_in_scene` vì số này có thể reset. JSON không có tọa độ thì adapter ghi rõ geometry không có; không thể dùng nó để dựng lại vị trí inpaint.
`source_record` giữ nguyên bản ghi nhập; `source_order_in_scene` giữ số thứ tự gốc. `order_in_scene` của normalized output dành cho scene mới được đề xuất, ban đầu là `null`.

```powershell
python src/dialogue_pipeline.py import-utterances "D:\download\utterances_selected.json" --output runs/selected.raw.json
python src/dialogue_pipeline.py normalize runs/selected.raw.json --output runs/selected.normalized.json
```

Không ghi đè file đã tồn tại: chọn tên mới khi chạy lại.

## 3. Duyệt loại nội dung và gộp logic

Heuristic baseline nhận một số mẫu UI/URL/SFX có chỉ dấu rõ. Nó không phải classifier đầy đủ cho mọi manga.
Các tiếng như “BAM”, tiếng rên, dấu ba chấm hoặc lời thoại trích URL có thể mơ hồ: giữ để duyệt.
Không loại narration/thought chỉ vì `is_essential_text=false`. Loại nội dung chưa rõ được ghi `unknown`; nhận diện narration/thought cần nhãn xác nhận khi không có đủ bằng chứng.

Ví dụ file overrides, dùng ID thực từ raw JSON:

```json
{
  "SOURCE_TEXT_ID_1": {"content_type": "sfx", "confirmed": true},
  "SOURCE_TEXT_ID_2": {"content_type": "thought", "confirmed": true},
  "SOURCE_TEXT_ID_3": {"content_type": "narration", "confirmed": true}
}
```

Loại hỗ trợ: `dialogue`, `narration`, `thought`, `unknown`, `ui`, `watermark`, `sfx`.
UI/watermark/SFX đã xác định bị loại khỏi normalized utterances và được lưu trong `excluded` với lý do. Raw vẫn giữ chúng.

```powershell
python src/dialogue_pipeline.py normalize runs/chapter-001/transcript.raw.json --output runs/chapter-001/reviewed.normalized.json --content-overrides content-overrides.json
```

Không tự gộp chỉ vì cùng speaker. `merge_candidates` là gợi ý, chưa làm thay đổi câu.
Chỉ gộp khi người dùng xác nhận các box là mảnh của cùng utterance, liên tiếp, cùng panel/trang, cùng speaker sơ bộ và cùng loại nội dung.
Các bản ghi không có geometry/speaker xác định không được gộp theo heuristic này.
Ví dụ `merge-groups.json`:

```json
[
  {"source_text_ids": ["SOURCE_TEXT_ID_1", "SOURCE_TEXT_ID_2"], "confirmed": true, "reason": "Đã kiểm tra là các mảnh của cùng một utterance"}
]
```

```powershell
python src/dialogue_pipeline.py normalize runs/chapter-001/transcript.raw.json --output runs/chapter-001/merged.normalized.json --merge-groups merge-groups.json
```

Một utterance gộp vẫn giữ mọi `source_boxes`, không thay chúng bằng một box lớn. Việc phân bổ bản dịch trở lại từng box là phần triển khai inpaint về sau.

## 4. Nhãn scene tham chiếu: đề xuất độc lập, người dùng duyệt

```powershell
python src/dialogue_pipeline.py review runs/selected.normalized.json --labels runs/selected.labels.proposed.json
```

Tạo template JSON trống và file `.md` chứa hội thoại theo thứ tự. Không sao chép scene predictions vào nhãn tham chiếu.
Trợ lý đọc hội thoại để đề xuất ranh giới; người dùng duyệt/sửa.

Nhãn đề xuất sẵn cho file 36 câu: `data/scene_review/utterances_selected.proposed.json`.
Người dùng đã duyệt nhóm scene trong hội thoại. Nhãn dùng cho benchmark của document này là `data/scene_review/utterances_selected.confirmed.json`; không cần xác nhận lại. Gap trước câu 34 vẫn ambiguous.
Giải thích: [scene reference proposal](scene_reference_utterances_selected.md).
File này khớp dữ liệu đã đọc và đúng adapter/normalization nêu ở bước 2. Fingerprint dựa trên thứ tự/text/metadata hội thoại, không phụ thuộc nơi đặt file. Nếu dữ liệu đổi, fingerprint bị từ chối và cần tạo lại template.

Mỗi entry quyết định có scene mới bắt đầu **trước** utterance chỉ định hay không:

- `boundary=true/false` và `status=proposed`: đề xuất chưa duyệt.
- `boundary=null`, `status=ambiguous`, có `reason`: hội thoại không đủ để quyết định.

Sau khi tự duyệt/sửa, người dùng chạy lệnh xác nhận:
Với các bộ dữ liệu mới chưa được duyệt, dùng lệnh bên dưới. Document 36 câu hiện tại đã có file confirmed từ lần duyệt trong hội thoại.

```powershell
python src/dialogue_pipeline.py confirm-labels runs/selected.normalized.json --labels data/scene_review/utterances_selected.proposed.json --reviewer "your-name" --output runs/selected.labels.confirmed.json
```

Lệnh này có nghĩa người dùng chấp nhận các quyết định đã điền. Nó không tự đánh giá chất lượng nhãn.
Không chạy lệnh xác nhận thay người dùng. Gaps ambiguous vẫn bị loại khỏi đánh giá, không tự biến thành false.

## 5. Môi trường embedding riêng

Venv hiện tại dùng Transformers 4.44 cho MAGI. Không nâng trực tiếp venv đó để cài embedding mới.

```powershell
py -m venv venv-scenes
.\venv-scenes\Scripts\python.exe -m pip install -r requirements-scenes.txt
```

Cài đặt có thể tải PyTorch/dependencies lớn. EmbeddingGemma cần chấp nhận điều khoản model trên Hugging Face và xác thực bằng công cụ HF; không đưa token vào repo hoặc command history.
Không có model weights nào được tự tải khi import module hoặc chạy normalization/review/tests.

Ba backend:

| CLI name | Model | Prompt |
|---|---|---|
| `mpnet` | `sentence-transformers/all-mpnet-base-v2` | Không thêm prompt retrieval |
| `embeddinggemma` | `google/embeddinggemma-300M` | `task: clustering \| query: ` |
| `qwen3` | `Qwen/Qwen3-Embedding-0.6B` | Instruction cố định về narrative context/events |

Model là ứng viên, chưa được chứng minh tốt nhất cho scene manga. Vector không được trộn giữa backend/revision/prompt/text khác nhau.
Cache lưu theo resolved revision và nội dung text; nếu revision không xác định được thì không cache. Utterance dài quá giới hạn tokenizer bị từ chối, không âm thầm truncate.

## 6. Chạy thử scene proposals

```powershell
.\venv-scenes\Scripts\python.exe src/dialogue_pipeline.py scenes runs/selected.normalized.json --model embeddinggemma --output runs/selected.scenes.json
```

Thử model khác bằng `--model mpnet` hoặc `--model qwen3` và tên output mới.
`--local-only` không cho tải weights. `--device cpu` mặc định; chỉ chọn `cuda` khi môi trường thực sự hỗ trợ.
`--revision` cho pin model. Metadata lưu revision/prompt/cache key.

Thuật toán: average-linkage trên mọi cặp vector giữa hai cụm, chỉ cho gộp cụm kề nhau, kèm giới hạn khoảng cách tối đa bên trong cụm để chống gộp dây chuyền.
Mỗi scene là đoạn liên tục. Không tự cắt theo trang, không gom hai đoạn ở xa vì cùng chủ đề.
Điểm boundary lấy từ cửa sổ trái/phải không chồng lấn. Câu rất ngắn hoặc điểm mơ hồ được đánh dấu cần duyệt.

`--distance-threshold 0.45`, `--cohesion-threshold 0.8`, `--boundary-window 2`, `--ambiguity-margin 0.08` là giá trị bắt đầu chưa hiệu chỉnh, không phải ngưỡng chất lượng đã xác nhận.
Scene vẫn có `scene_status=proposed`, không phải gold. Chọn ngưỡng sau benchmark.
Helper `context_for_utterance` giữ những lượt thoại gần câu cần dịch, kể cả qua ranh giới scene chưa chắc. Chưa sửa script dịch/inpaint hiện có.

## 7. Benchmark không dùng test để chọn ngưỡng

Cần ít nhất hai chương/document khác nhau có nhãn đã duyệt: một phần development để chỉnh ngưỡng, một phần test để đánh giá.
File 36 câu hiện tại chỉ là một document, không đủ chứng minh chất lượng tổng quát.

Manifest ví dụ (paths tương đối với nơi lưu manifest):

```json
{
  "development": [{"dialogue": "chapter-a.normalized.json", "labels": "chapter-a.labels.confirmed.json"}],
  "test": [{"dialogue": "chapter-b.normalized.json", "labels": "chapter-b.labels.confirmed.json"}]
}
```

```powershell
.\venv-scenes\Scripts\python.exe src/dialogue_pipeline.py benchmark runs/benchmark-manifest.json --output runs/benchmark-report.json --models mpnet embeddinggemma qwen3 --thresholds "0.25,0.35,0.45,0.55"
```

Benchmark từ chối nhãn chưa xác nhận, fingerprint lệch, thiếu/duplicate IDs và chapter/nội dung trùng nhau giữa các document.
Ngưỡng được hiệu chỉnh riêng cho từng embedding trên development; test chỉ được chấm sau khi ngưỡng được chọn.
Report: boundary precision/recall/F1, WindowDiff, lỗi gộp quá mức/chia quá nhỏ, gaps ambiguous và thời gian.
WindowDiff bỏ các cửa sổ đi qua gap ambiguous và báo `window_k`/số cửa sổ hợp lệ.
Thời gian hiện bao gồm model load/cache đọc, không phải số throughput thuần inference. Report không tự chọn model production.
Backend lỗi vẫn được ghi trong report; CLI trả exit 1 nếu benchmark chưa đủ tất cả model yêu cầu.

Đã có [so sánh thực tế ba embedding trên mẫu 36 câu](benchmark_three_models_20261001.md). Chưa có benchmark development/test độc lập; chưa chọn model production. Unit tests dùng vector/model giả lập để kiểm tra thuật toán, mapping, review và chống leakage; không chứng minh chất lượng scene.

## So sánh thử trên một mẫu đã duyệt

Khi chỉ có một document, có thể chạy một benchmark mô tả trên mẫu đó. Nó không thay thế benchmark development/test và không tự chọn model/ngưỡng production:

```powershell
.\venv-scenes\Scripts\python.exe src/benchmark_sample.py "D:\download\utterances_selected.json" --labels data/scene_review/utterances_selected.confirmed.json --output runs/benchmark-three-embeddings-20261001.json
```

Chạy lần lượt ba model trên CPU với 4 threads, batch size 8 và cùng cohesion threshold 0.8. Báo cáo mọi ngưỡng distance 0.25/0.35/0.45/0.55, không chọn một ngưỡng tối ưu từ mẫu này. `encode_seconds` tách khỏi thời gian download/load; cache hit không có số đo inference mới.
`--resume` chỉ tiếp tục report khớp dữ liệu/cấu hình, bỏ qua backend đã thành công và thử lại backend lỗi. Weights nằm trong `data/scene_models`, vectors trong `data/scene_embeddings`.

## Kiểm tra

```powershell
python -m unittest discover -s tests -v
python src/dialogue_pipeline.py --help
```
