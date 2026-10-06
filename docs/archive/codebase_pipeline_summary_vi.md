> Historical: describes the pre-refactor pipeline. Current usage is in ../news/usage.md.

# Tóm tắt pipeline hiện tại

Luồng chính: **ảnh chapter → Stage 1 MAGI + character bank → Stage 2 normalization → reasoning phân cảnh → Stage 3A–3D → structured_dialogue.json**.

Entry point chạy trọn luồng là `src/run_chapter.py`. `src/main.py` chạy extraction và normalization; `src/dialogue_pipeline.py` cung cấp CLI xử lý JSON và reasoning riêng.

## Stage 1 — Trích xuất và liên kết nhân vật

**Đầu vào:** thư mục ảnh PNG/JPG/JPEG, story/chapter ID và character bank của truyện.

`src/main.py` sắp ảnh theo thứ tự tự nhiên, chuyển ảnh sang grayscale rồi RGB và chạy MAGI v2 (`ragavsachdeva/magiv2`, revision được cố định) trên toàn chapter. MAGI cung cấp OCR và các detection/association dùng cho panel, nhân vật, text và liên kết người nói.

`src/character_assignment.py` thay phần gán nhân vật bằng `DynamicCharacterAssigner`. Embedding của quan sát nhân vật được so với các prototype trong bank bằng khoảng cách Euclidean trên vector đã chuẩn hóa; điểm dùng trung bình của tối đa ba prototype gần nhất. Match cần đạt cả ngưỡng khoảng cách và độ chênh với ứng viên thứ hai. Trường hợp mới/chưa chắc được giữ pending; lần xuất hiện lại đủ điều kiện có thể được promote thành ID ổn định. Không phải mọi detection đều được ép thành một nhân vật đã biết.

`src/character_bank.py` lưu bank riêng theo truyện, quản lý ID, prototype, crop, pending và các sửa đổi có chủ đích như rename/merge. `src/character_postprocessing.py` định nghĩa giao diện xử lý match mơ hồ. Transcript dùng nhãn `<1>`, `<2>` hoặc `<Other>`; ID nội bộ trong JSON được lưu riêng với nhãn hiển thị.

**Đầu ra:** transcript văn bản, raw JSON giữ các nguồn OCR/detection/association và ảnh visualization. Raw JSON là nguồn đối chiếu cho các stage sau; transcript chỉ là biểu diễn đơn giản hơn.

## Stage 2 — Chuẩn hóa lời thoại

Thực hiện bởi `normalize_document()` trong `src/dialogue_data.py`, không cần gọi model reasoning.

- Kiểm tra schema raw, chuẩn hóa Unicode NFC và khoảng trắng.
- Phân loại nội dung bằng quy tắc: dialogue, narration, thought, unknown và các loại nhiễu. Nội dung chưa đủ chắc được giữ để review; không chỉ dựa vào cờ essential của MAGI để loại toàn bộ text.
- Lưu audit cho các text bị loại. Cho phép override loại nội dung và merge text khi cấu hình đã có `confirmed: true`; merge cần có lý do.
- Giữ `text_original`, `source_text_ids`, từng `source_boxes`, bbox, page/panel và thông tin người nói nguồn.
- Tạo utterance ID và thứ tự ban đầu. `scene_id` vẫn là null, `scene_status` là `not_run`.

**Đầu ra:** `dialogue.normalized.json`, là dữ liệu đầu vào cho reasoning. Stage này chưa dịch và chưa suy luận người nghe.

## Phân cảnh — Bước reasoning trước 3A

`src/reasoning_pipeline.py` nạp normalized JSON, raw JSON, ảnh và metadata bank vào `Chapter` (`src/reasoning_batching.py`). Sau đó:

1. `visual_observations()` trong `src/scene_reasoning.py` tạo mô tả hình ảnh về bối cảnh, dấu hiệu thời gian và sự kiện từ panel/page. Chế độ text-only bỏ bước ảnh này.
2. `segment_scenes()` xét từng khoảng giữa hai utterance liên tiếp và dự đoán `boundary`, `no_boundary` hoặc `uncertain`.
3. Scene dựa trên sự liên tục về thời gian, địa điểm và sự kiện/tương tác. Đổi trang, panel, người nói hoặc chủ đề không tự động tạo scene mới.
4. Các khoảng mơ hồ và điểm nối batch được kiểm tra lại. Nếu hai lượt bất đồng thì giữ `uncertain`.
5. Chỉ `boundary` tạo scene mới. `uncertain` giữ liên tục mềm và đánh dấu scene cần review.

**Đầu ra trung gian:** `scene_dialogue`, có scene ID, thứ tự trong scene, quyết định boundary, bằng chứng và visual observations.

Luồng mặc định dùng scene dự đoán. Code cũng hỗ trợ bỏ phân cảnh và dùng scene gold để làm đối chứng/benchmark. Các thử nghiệm MPNet, EmbeddingGemma và Qwen3 clustering cũ đã được tách khỏi CLI hiện tại, theo `docs/pipeline_usage.md`.

## Stage 3 — Tái dựng hội thoại

Thực hiện bởi `src/dialogue_reconstruction.py` và `src/dialogue_ordering.py`.

| Bước | Công việc | Ràng buộc chính |
| --- | --- | --- |
| 3A: speaker association | Sửa liên kết utterance với visual cluster/người nói dựa trên ảnh, hình học bubble/tail và ngữ cảnh | Chỉ chọn ứng viên được cung cấp; cho phép off-screen hoặc unknown; không recluster khuôn mặt |
| 3B: identity/name | Giải quyết ID và tên người nói từ giả thuyết 3A, bank/profile và bằng chứng text | Không tạo ID mới; tên và ID là hai thông tin riêng; tên chưa chắc chỉ là đề xuất |
| 3C: addressee | Xác định câu nói hướng đến cá nhân, nhóm, chính mình, không rõ hoặc không áp dụng | Người nói tiếp theo không mặc nhiên là người nghe; ID phải thuộc tập ứng viên |
| 3D: reading order | Điều chỉnh thứ tự đọc bằng ảnh và ngữ cảnh | Chỉ hoán vị trong cùng page và scene; không đổi text hay scene membership |

Nếu 3D đổi thứ tự, pipeline chạy lại 3A và 3C trong vùng bị ảnh hưởng; chạy lại 3B khi liên kết cluster/identity thay đổi. Các mâu thuẫn identity còn lại được ghi để review.

Validator bảo đảm không mất/lặp utterance, không sửa nguồn text/bbox và không đổi thứ tự vượt ranh giới page/scene. Output giữ liên kết gốc, trạng thái suy luận, reasoning, evidence và review flags. Stage 3 **không ghi các tên/identity do model đề xuất trở lại character bank**.

**Đầu ra:** `structured_dialogue.json` với utterance, scene, người nói, tên, người nghe và thứ tự đã xử lý, kèm audit và metadata lượt reasoning.

## Model, batching và khả năng chạy tiếp

`src/reasoning_client.py` gọi Ollama, yêu cầu output theo schema, kiểm tra response, retry và cache request đã xác thực. `src/reasoning_schemas.py` định nghĩa contract cho các task. `src/reasoning_batching.py` chọn context/ảnh/identity candidates, chia task và điều chỉnh request theo ngân sách. Nếu request không còn vừa hoặc không đáp ứng contract sau retry, pipeline báo lỗi và lưu checkpoint.

Config chạy chapter trên CPU hiện có trong `configs/reasoning.chapter-cpu.json`: model `gemma4:e4b-it-q4_K_M`, context 8192, tối đa 6 targets/request, overlap 2, một ảnh/request và ảnh cạnh tối đa 768. Đây là giá trị cấu hình trong repository; không khẳng định model đang được cài hay chạy trên máy.

`src/run_chapter.py` tổ chức thư mục:

```text
output-dir/
  stage1/
    transcript.txt
    transcript.raw.json
    visualizations/
    magi.log
  stage2/dialogue.normalized.json
  stage3/structured_dialogue.json
  check.json
  internal/cache/
  character_banks/
```

`check.json` lưu snapshot các bước và task đang chạy, cập nhật atomically. `--resume` kiểm tra nguồn ảnh, bỏ qua extraction đã hoàn tất và dùng lại request cache hợp lệ. Runner này dùng bank riêng của lượt chạy trong output directory; chạy `main.py` trực tiếp có cơ chế bank theo story để tái sử dụng giữa các chapter.

## Phần hỗ trợ và giới hạn hiện tại

- `src/reasoning_evaluation.py`: template annotation, kiểm tra reference và tính metric trên nhãn độc lập.
- `src/reasoning_benchmark.py`: chạy các biến thể tuần tự, phân biệt development/test và báo cáo thất bại.
- `scripts/check_scene_labels.py`: probe ý nghĩa nhãn boundary trước lượt chạy dài.
- `tests/`: contract/regression cho bank, normalization, reasoning, ordering, checkpoint và resume; có script smoke test live riêng.
- `tests/test_ollama_v2.py` có thử nghiệm dịch các trường text trong JSON sang tiếng Việt. Bước này chưa nối vào runner chính đến hết Stage 3.
- `src/loc 2.py` là tiện ích xử lý ảnh/transcript riêng, không nằm trong chuỗi orchestration chính nêu trên.

Kết quả hiện tại là **hội thoại có cấu trúc để phục vụ bước dịch tiếp theo**. Chưa có bước dịch hoàn chỉnh, QA bản dịch hoặc render bản dịch lên trang trong luồng `run_chapter.py`. Identity, scene và addressee do reasoning dự đoán vẫn cần review khi không chắc.

Phạm vi đọc: source trong `src/`, script, test, config và tài liệu pipeline của repository; không coi venv, model weights, ảnh và output đã sinh là mã nguồn dự án. Đây là phân tích tĩnh; không chạy MAGI/Ollama hay bộ test trong lần tổng hợp này.
