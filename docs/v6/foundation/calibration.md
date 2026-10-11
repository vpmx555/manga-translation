# Tham số mới và cách hiệu chỉnh

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

Các tên bên dưới là **config đề xuất**, chưa tồn tại trong CLI/config hiện tại.

| Nhóm tham số | Đơn vị / cách chọn |
|---|---|
| `reconnect_gap_ratio` | Khoảng cách contour chuẩn hóa theo kích thước thân; thử trên ca nối thật và hai bubble gần nhưng độc lập. |
| `neck_width_ratio`, `min_body_support` | Độ rộng cổ nối so với vùng thân và support của text/image; không chỉ đếm OCR box. |
| `boundary_search_ratio`, `boundary_smoothness` | Dải tìm nét và cost đổi hướng; so boundary error với contour hiện tại. |
| `max_reconstruction_for_hint` | Tỷ lệ biên suy ra vượt mức sẽ từ chối style hint; không cấm tạo contour kín. |
| `mapping_min_coverage`, `mapping_score_margin` | Hiệu chỉnh sau khi candidate trùng được giải quyết; không trộn với ngưỡng detect/association. |
| `style_acceptance_by_label` | Ngưỡng riêng từng hint, đo precision cùng support và unknown coverage. |
| `margin_ratio`, `margin_min_px` | Margin thân; hiệu chỉnh trên contour có gai, thân nhỏ và vùng sát cầu nối. |
| `min_font_px`, `max_font_px`, `line_spacing` | Resolve theo ảnh đầu ra/font; review khả năng đọc ở kích thước hiển thị mục tiêu. |
| `max_translation_rewrites` | Đề xuất 2; hard limit cho vòng dịch lại do fit. |
| `extra_crop_budget` | Giới hạn số lượt detect bổ sung cho ca bị cắt; đo chi phí local. |

Chưa có bằng chứng để chốt một ngưỡng khoảng cách/margin mới cho mọi ảnh. M11 so
những cấu hình ứng viên, ghi giá trị chọn và ca thất bại vào receipt. Không dùng
một tập ca để vừa hiệu chỉnh vừa tuyên bố chất lượng tổng quát; giữ các ca kiểm
chứng riêng khi đủ dữ liệu.

### Hiệu chỉnh hints chỉ trên dữ liệu thị giác

Features và acceptance của M5b chỉ tính từ ảnh/mask/geometry; không thêm OCR
content, text-box density hay dialogue score làm feature, kể cả khi hiệu chỉnh.
Nhãn chuẩn có thể do người review xác định để đo precision, nhưng nội dung text
không là input classifier. Đo visual-hint precision/unknown coverage riêng với
độ chính xác loại thoại cuối M7. Cache feature dùng lại kết quả hình học M4,
tách khỏi text mapping để giảm tính toán lặp.
### Caller-first

`min_confirmed_call_events=2` là default thiết kế cho nhánh “gọi nhiều lần”,
chưa là CLI flag/ngưỡng xác suất. Dedup occurrence trước đếm; giữ nhánh một call
rõ + ảnh phù hợp. Không đặt visual-distance cutoff veto repeated confirmed
calls. Name collision/recipient mơ hồ không giải bằng count. Đo false person
link/split theo nhánh; không thay ngưỡng bank MAGI.
---

[← Tổng quan v6](../README.md)
