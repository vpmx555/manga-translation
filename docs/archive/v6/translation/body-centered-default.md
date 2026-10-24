# Căn thân chữ mặc định

Renderer v6 và renderer OCR-area dùng chung `draw_body_centered_line` trong
[`typeset.py`](../../../src/manga_pipeline/v6/typeset.py).

Input là dòng trong plan (`text`, `x`, `y`, `height`) và font ở cỡ đã chọn.
Tâm dòng là `y + height / 2`. Lấy bbox chữ `H` với `anchor="ls"`, rồi tính
`baseline = tâm dòng - (top_H + bottom_H) / 2`. Vẽ nguyên văn chữ có dấu tại
`(x, baseline)` bằng `anchor="ls"`. Dấu không tham gia định vị thân chữ.

Plan giữ tọa độ ô dòng và bổ sung `baseline_y`, `draw_anchor`, `body_reference`
và `body_reference_bbox_ls` để chẩn đoán tái dựng đúng glyph. Font được vẽ đủ
cỡ trong plan; mặc định khoảng cách dòng là 1.6. Kiểm tra containment dùng
glyph thực tế theo baseline mới, kể cả dấu, và vẫn giữ margin bubble 10px.

Policy typeset đổi sang `mask-typeset-v6.2-h-center` để không tái dùng cache
renderer cũ. Cấu hình đã lưu trong run cũ vẫn được giữ; spacing 1.6 áp dụng
cho cấu hình mặc định mới. Phương pháp phân bổ OCR-area vẫn là opt-in.

Probe trang 3 đã có 0 cặp dòng giao nhau và 0 pixel vượt margin bubble 10px;
2 pixel vượt vùng OCR cũ. Khi fit mới, glyph vượt vùng cho phép sẽ bị từ chối
như trước. Chưa chạy lại toàn chương sau khi đổi mặc định.
