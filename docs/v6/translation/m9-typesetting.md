# M9 — Tạo vùng chữ an toàn và dàn chữ cân bằng

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

**Phương pháp tạo vùng:** lấy body mask mỗi part, erosion theo margin tương đối
với kích thước thân và mức tối thiểu theo pixel nguồn; trừ tail mask, bridge mask,
vật cản đã xác định và vùng ngoài ảnh. OCR box làm anchor về trung tâm/hướng text,
cho phép mở rộng trong body. Vùng được giữ phải phục vụ part đó và không đi xuyên
bridge. Không dùng bbox toàn group làm vùng chữ.

**Phương pháp dàn chữ:**

1. Với một font size chung, lấy các dải ngang hợp lệ trong từng mask; tính chiều
   rộng dòng bằng raster region và font metrics. Dải có nhiều khoảng rời nhau phải
   chọn vùng phù hợp anchor, tránh một dòng bắc qua vùng cấm.
2. Tokenize theo ngôn ngữ đích; bảo vệ tên, số và dấu câu; tìm ngắt dòng và điểm
   chuyển part theo thứ tự đọc. Với tiếng Việt dùng ranh giới từ/khoảng trắng và
   nhóm cụm từ; adapter ngôn ngữ khác phải có quy tắc riêng.
3. Tối ưu chung bằng dynamic programming: giữ thứ tự token, không bỏ/lặp chữ,
   phạt ngắt cụm xấu và chênh lệch tỷ lệ lấp đầy so với sức chứa thực tế. OCR anchor
   là một thành phần của cost về vị trí, không phải giới hạn cứng.
4. Tìm cỡ chữ lớn nhất có thể dàn hợp lệ trong giới hạn min/max. Dùng font metrics
   để tìm ứng viên; render thử glyph mask và kiểm tra containment thực tế. Không
   giả định width/height ước lượng bảo đảm glyph không vượt contour.

**Mô hình:** không có. Raster morphology, Pillow/FreeType và tối ưu ngắt dòng.
Font file/hash phải vào receipt; runtime kiểm tra hỗ trợ ký tự ngôn ngữ đích.

**Input:** bản dịch chung M8, parts/body/bridge/tail mask M4, OCR anchors M2,
reading order M6 và config margin/font/alignment.

**Output:** `typeset-plan.json`, safe mask, glyph mask và preview overlay.

```json
{
  "utterance_id": "p001:u01", "group_id": "p001:g01", "status": "fit",
  "translation": "Tôi cứ nghĩ bạn sẽ đến.",
  "font": {"path": "<local-font-path>", "sha256": "<hash>", "size_px": 24},
  "placements": [{
    "part_id": "p001:g01:part01", "token_span": [0,3],
    "safe_mask_path": "typeset/p001/part01-safe.png",
    "lines": [{"text": "Tôi cứ nghĩ", "origin": [365,480], "glyph_bbox": [365,480,510,508]}],
    "fill_ratio": 0.63
  }, {
    "part_id": "p001:g01:part02", "token_span": [3,6],
    "safe_mask_path": "typeset/p001/part02-safe.png",
    "lines": [{"text": "bạn sẽ đến.", "origin": [410,790], "glyph_bbox": [410,790,558,818]}],
    "fill_ratio": 0.60
  }],
  "checks": {"lost_tokens": 0, "duplicated_tokens": 0, "outside_safe_pixels": 0, "bridge_overlap_pixels": 0}
}
```

Tọa độ/cỡ chữ trong ví dụ chỉ minh họa format. Khi triển khai min/max font, margin,
line spacing và vị trí phải được resolve theo kích thước ảnh/font đã chọn.

**Điều kiện hoàn thành:** một font size cho toàn nhóm; mọi glyph nằm trong vùng
cho phép và không vào bridge; giữ đủ nội dung đúng thứ tự. “Cân bằng” đo theo tỷ
lệ sử dụng sức chứa, không yêu cầu số chữ mỗi part bằng nhau.

---

[← Tổng quan v6](../README.md)
