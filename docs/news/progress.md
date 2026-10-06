# Thanh tiến trình

Mặc định mọi lệnh CLI đều hiển thị thanh tiến trình. `run` hiển thị các bước và thanh lần lượt, giữ dòng kết quả sau khi mỗi bước kết thúc:

```text
[1/5] extract
[2/5] normalize
[3/5] scan
[4/5] analyze
[5/5] export
```

Mỗi dòng có thanh, số mục đã xử lý/tổng mục, thời gian và tốc độ khi có đủ dữ liệu. Trong terminal tương tác, thanh cập nhật thời gian mỗi giây kể cả khi đang chờ model. Lệnh riêng từng bước chỉ hiển thị một thanh `[1/1]`. `resume --until ...` hiển thị số bước trong phạm vi được chọn.

Run pipeline v2 giữ sáu thanh cũ, với `classify` và `link` thay cho `analyze`. Luồng được đọc từ manifest, không đổi khi resume.

| Bước | Bộ đếm |
| --- | --- |
| extract | Loading theo trang; MAGI detection/OCR theo batch thực tế; lưu output theo trang. Thanh đổi nhãn và reset cho từng phase. Khi đang load model hoặc lưu bank, hiển thị trạng thái chờ với thời gian, chưa có phần trăm. |
| normalize | Text nguồn, gồm cả text bị loại có audit |
| scan | Utterance được quét tên |
| analyze | Utterance essential đã hoàn tất cả phân loại thoại và xử lý tên. Nhãn phase lần lượt là text, repair khi cần, bind names, panel VLM khi đủ điều kiện, save bank và checkpoint. Đổi phase không reset bộ đếm utterance. |
| export | Utterance được ghi vào dialogue |

`reused` ghi số target đã commit được tái dùng. Bước đã completed hiển thị `reused` với bộ đếm một kết quả bước; không chạy lại model. `failed` ghi số target lỗi trong bước. Trong analyze v3, fallback sau tối đa một lần repair và tên unresolved đều được lưu là kết quả hoàn tất, có cảnh báo để kiểm tra; resume không tự gọi lại chúng. Lỗi ghi file/bank vẫn dừng bước để resume. Các bước legacy giữ cơ chế partial/retry cũ. Bước không có target hiển thị 0 mục và completed.

Phần trăm và ETA, nếu có, áp dụng cho bước/phase đang hiển thị, không phải dự đoán thời gian toàn chapter. Tiến độ extraction đi qua artifact phụ `01_extract/progress.json`; file này chỉ phục vụ hiển thị, không quyết định resume. Log vẫn ở `logs/magi.log`.

Tắt thanh khi redirect output hoặc chạy tự động:

```powershell
.\venv\Scripts\python.exe run.py run "D:\Manga\Chapter 1" --story "One Piece" --no-progress
.\venv\Scripts\python.exe run.py resume --run-dir "outputs/one-piece/chapter-1/<run-id>" --no-progress
```

Đã kiểm tra thứ tự năm thanh v3 và sáu thanh legacy, resume không gọi lại model, phục hồi target sau gián đoạn và bridge batch detection/OCR bằng tqdm. Run thử v3 trên 52 câu essential dùng extraction đã có, hiển thị lần lượt normalize, scan, analyze và export; extraction được tái dùng. Xem [báo cáo triển khai](dialogue_v3_implementation.md).
