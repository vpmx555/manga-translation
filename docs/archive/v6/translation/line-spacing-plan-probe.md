# Probe: khoảng dòng trong plan, font vẽ đúng cỡ

[M9](m9-typesetting.md) · [OCR-area thử nghiệm](experimental-ocr-area-typesetting.md)

Thiết lập chẩn đoán hiện tại là `line_spacing=1.6`. Hệ số được truyền vào
`slots()` trước khi tìm font size/ngắt dòng/tọa độ. Plan mới ghi config 1.6;
mọi glyph được vẽ ở đúng `font_size_px` mà plan chọn, `render_font_scale=1.0`.
Không dùng hệ số 65% của thử nghiệm trước. Source run và plan cũ giữ nguyên.

[typeset_cached_translation.py](../../../scripts/diagnostics/typeset_cached_translation.py)
có `--line-spacing` (mặc định 1.6) cho probe từ bản dịch/ảnh sạch đã lưu. Geometry,
OCR mapping, quota diện tích, margin 10px, bảo vệ vùng lân cận và common font
giữ cùng chính sách. Không chạy lại OCR/inpaint/analyze/translation model.

Đây là cấu hình của plan chẩn đoán, chưa đổi tham số của renderer production.
Run đã có config snapshot 1.18 vẫn giữ snapshot đó; chỉ plan probe mới dùng 1.6.

Trên After School chapter 1 ngày 2026-10-12: dàn 44/44 vùng trên 11 trang;
font vẽ đúng plan; giao glyph giữa dòng giảm 141 → 6 cặp, còn ở 5 region/group.
Không có glyph giao giữa các part hoặc vượt safe mask. Status fit/completed
chưa thay thế audit từng dòng: AstroCity khai báo metrics thấp hơn chiều cao
glyph tiếng Việt có dấu, nên tăng hệ số nominal lên 1.6 chưa bảo đảm hết giao.

[audit_typeset_overlap.py](../../../scripts/diagnostics/audit_typeset_overlap.py)
đo từng dòng từ saved plan/glyph và có `--compare-with` để tạo so sánh hai run.
Kiểm tra này chỉ đọc/raster artifact đã có, không fitting hoặc chạy pipeline.
