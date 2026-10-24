# M10 — Xử lý không vừa bằng dịch lại ngắn hơn

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

**Phương pháp:** M9 giảm font đến `min_font_px` đã resolve. Nếu không vừa, gửi
lại **toàn lời thoại** với ngân sách sức chứa nhóm, bản dịch hiện có và các ràng
buộc nghĩa. Không rút ngắn từng part riêng. Đề xuất tối đa 2 lượt dịch lại ngoài
bản dịch đầy đủ ban đầu; đây là config mới của vòng fit, khác retry sửa JSON.

Giữ tên/số/quan hệ người nói và ý nghĩa. Validator kiểm tra các bất biến có thể
kiểm tra bằng code; ý nghĩa đầy đủ vẫn cần review ở pilot. Bản ngắn được đưa lại
M9 để đo fit thực tế. Ngân sách ký tự ước lượng là gợi ý cho model, không bảo đảm
text sẽ vừa.

**Mô hình:** cùng Ollama local với M8.

**Input:** bản dịch/nguồn M8, context/analysis, báo cáo overflow M9.

```json
{
  "utterance_id": "p001:u01", "reason": "overflow_at_min_font",
  "attempt": 1, "remaining_rewrites": 1,
  "current_translation": "<full-translation>",
  "layout_budget": {"font_size_px": 18, "usable_lines": 5, "estimated_capacity": "<font-measured-budget>"},
  "constraints": {"preserve_names": true, "preserve_numbers": true, "preserve_meaning": true}
}
```

**Output:** translation variant mới có provenance và kết quả fit mới. Hết lượt
thử mà không vừa trả `needs_review`, giữ bản dịch hợp lệ gần nhất; không cắt chữ
để tạo trạng thái `fit`. Nếu không có vùng dàn chữ sử dụng được, báo lỗi hình học
để sửa vùng; không lặp dịch ngắn hơn cho một vùng có sức chứa bằng 0.

**Điều kiện hoàn thành:** loop hữu hạn; mọi variant truy xuất được; bản vừa đã
qua validator và containment thực tế. Báo số lượt model gọi và latency.

---

[← Tổng quan v6](../README.md)
