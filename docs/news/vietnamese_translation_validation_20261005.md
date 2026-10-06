# Kiểm chứng normalize, dịch tiếng Việt và timing — 2026-10-05

## Regression

`venv/Scripts/python.exe -B -m unittest discover -s tests -v`: 123 test qua. Kiểm tra whole-box rules trước cả hai nhánh essential/translation_only, ngoại lệ theo truyện, snapshot rule, hai phong cách độc lập, giữ metadata nguồn, honorific repair theo target, replay journal sau ngắt, partial/retry theo phong cách, glossary revision, khóa sửa tay và context-provider tùy chọn. Luồng v2/v3 đã lưu giữ nguyên; run mới có sáu bước v4.

Timing được đo bằng monotonic và lưu trong metadata/receipt của bước. Test xác nhận thời gian qua partial/retry cộng dồn 4 + 7 = 11 giây, không cộng khoảng nghỉ từ mốc 14 tới 1000; lỗi và Ctrl+C cũng giữ thời gian trước retry. Resume bước hoàn thành không đổi số đo. Run cũ không có timing hiển thị “—”, không tính ngược từ các timestamp.

## Normalize thật và review tổng

Tạo run mới, tái dùng extraction, chỉ chạy tới normalize:

```powershell
.\venv\Scripts\python.exe -B run.py reanalyze --run-dir "outputs/diagnostics/analyze-v3-20261005-01/run" --output-root "outputs/diagnostics/translation-normalize-timing-20261005" --until normalize --no-progress
```

Artifact: [review tổng](../../outputs/diagnostics/translation-normalize-timing-20261005/after-school-we-do/chapter-001/20261005T082031Z-a6e76334/review.md), [review normalize](../../outputs/diagnostics/translation-normalize-timing-20261005/after-school-we-do/chapter-001/20261005T082031Z-a6e76334/02_normalize/review.md).

11 trang, 83 box nguồn. Kết quả còn 51 utterances và 30 translation_only; loại đúng hai box `Klink` và `Clench`. Năm box được giữ với cảnh báo, gồm dấu câu đơn lẻ và chuỗi lặp dài. Câu có nội dung như `Uhhhhhh...! for me,` vẫn còn nguyên. Extraction tái dùng ghi 0 giây, normalize đo 0,047 giây, tổng review 0,047 giây; các bước chưa chạy hiển thị “—”. Run và extraction nguồn không bị sửa.

## Pilot dịch trực tiếp export cũ

Nguồn: `outputs/diagnostics/analyze-v3-20261005-01/run/05_export/result.json`, 52 utterances + 31 translation_only. Pilot đọc thẳng 83 box đã export, không áp dụng lại filter normalize. Model hiện có `gemma4:e4b-it-q4_K_M` chạy qua Ollama trên CPU; thinking tắt, mỗi batch ba target. Hai phong cách có lượt inference/checkpoint riêng.

```powershell
.\venv\Scripts\python.exe -B run.py translate --run-dir "outputs/diagnostics/analyze-v3-20261005-01/run" --no-progress
```

Revision: `c579cb0b7af10c3c3565` đã hoàn tất.

| Phong cách | Coverage | Failed / rỗng | Model đánh dấu review | Metadata nguồn / honorific |
| --- | --- | --- | --- | --- |
| natural | 83/83 ID duy nhất | 0 / 0 | 2 | Giữ nguyên / không thiếu hậu tố |
| localized | 83/83 ID duy nhất | 0 / 0 | 3 | Giữ nguyên / không thiếu hậu tố |

Mở [review natural](../../outputs/diagnostics/analyze-v3-20261005-01/run/translations/vi/c579cb0b7af10c3c3565/review_natural.md) hoặc [review localized](../../outputs/diagnostics/analyze-v3-20261005-01/run/translations/vi/c579cb0b7af10c3c3565/review_localized.md). Hai phong cách khác chuỗi bản dịch ở 46/83 câu. Có 56 phản hồi model, 76.286 prompt token, 9.736 output token; tổng duration của các request theo usage Ollama là 4.247,494 giây (khoảng 1 giờ 10 phút 47 giây) trên CPU.

Resume thật với provider bị chặn inference ghi nhận **0 lượt gọi model**, 0 failure. Hash `result.json` và toàn bộ 166 target receipt không đổi. Hash 206 file JSON nguồn và bank sau dịch/resume khớp baseline bên dưới. Kết quả chưa đồng nghĩa với chất lượng ngữ nghĩa đã được duyệt.

Các ví dụ cần review thủ công:

| Source ID | Vấn đề quan sát |
| --- | --- |
| `p1:t0:u` | `Rocketry Research Club` được natural dịch thành “Câu lạc bộ Nghiên cứu Động cơ Phản lực”; thuật ngữ chưa phù hợp và chưa nhất quán với các câu khác. Cần xác nhận glossary, ví dụ “Câu lạc bộ Nghiên cứu Tên lửa”. |
| `p1:t1:u` | OCR `provision engine` được dịch sát chữ thành “động cơ cung cấp”; cần đối chiếu nguồn để biết thuật ngữ thật. Model chưa đánh dấu review. |
| `p1:t2` | OCR `Best Firing Mybrid Rocked/B/Hence` không rõ nghĩa; model có cảnh báo review. |
| `p1:t3` | OCR `hascribe` được natural giữ nguyên nhưng localized suy ra “ghi âm”; localized chưa đánh dấu review. |
| `p1:t6:u` | OCR `exploped...` được suy ra “nổ.../Nổ tung...”; model chưa đánh dấu review cho phỏng đoán này. |

Model có thể không tuân thủ đủ yêu cầu đánh dấu OCR phỏng đoán; code hiện không có detector OCR ngữ nghĩa. Mức khẩu ngữ giữa hai bản còn tùy câu. Bản hiện tại chưa nạp hồ sơ/đồ thị nên không có thêm bằng chứng để cải thiện xưng hô. Các vấn đề này được ghi để review, không tự xác nhận glossary hay sửa speaker/listener nguồn.

Hash nguồn được kiểm tra độc lập với thư mục translations: 206 file JSON, sắp theo tên tương đối có dấu `/` đầu, hash SHA-256 của JSON danh sách `[path, file_sha256]` là `6c560bde9add3a6429ed703d942862a24764e21adaae93a2c24805b97f9be85b`. Bank metadata giữ SHA-256 `9f8c680e192623b548d4e53f110247f0452475a8921f338f3d1dff20210a8ef0`.
