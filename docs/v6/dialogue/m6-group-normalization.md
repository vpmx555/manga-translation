# M6 — Normalize thành lời thoại nhóm, giữ vị trí nguồn

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

**Phương pháp:** mỗi group đã xác định và có text tạo một utterance; gom text theo
thứ tự đọc và giữ ranh giới đoạn nguồn. Part order dùng quan hệ panel/không gian
và quan hệ nhóm; kiểm tra trường hợp thứ tự mâu thuẫn hoặc xen lời thoại nhóm
khác. Không ghép theo một phép sort bbox duy nhất khi layout phức tạp.

Thay điều kiện “cùng panel và đã biết cùng speaker” hiện tại bằng group membership
được xác minh. Không cần biết speaker trước mới được phân tích chung. Các text
không thuộc group chắc chắn vẫn có đơn vị riêng với candidate context.

**Mô hình:** không có. Normalize/reading-order graph bằng code.

**Input:** raw text M2 và group/mapping/evidence M3–M5.

**Output:** normalized document có `utterances` và `source_boxes` không bị mất.

```json
{
  "utterance_id": "p001:u01", "group_id": "p001:g01",
  "source_text_ids": ["p001:t0", "p001:t1"],
  "source_parts": [{"part_id": "p001:g01:part01", "text_ids": ["p001:t0"], "order": 0},
                   {"part_id": "p001:g01:part02", "text_ids": ["p001:t1"], "order": 1}],
  "text": "I thought you would come.",
  "text_original": ["I thought", "you would come."],
  "source_boxes": [{"source_text_id": "p001:t0", "bbox": [350,450,530,550], "panel_index": 0},
                   {"source_text_id": "p001:t1", "bbox": [400,760,580,860], "panel_index": 1}],
  "panel_ids": ["p001:panel01", "p001:panel02"],
  "speaker_candidates": [{"speaker_id": "char-A", "text_id": "p001:t0", "score": 0.64},
                         {"speaker_id": "char-B", "text_id": "p001:t1", "score": 0.71}],
  "speaker_id": null, "speaker_display_label": "Other",
  "speaker_status": "awaiting_group_analysis"
}
```

**Điều kiện hoàn thành:** mỗi source text xuất hiện đúng một lần trong nguồn lời
thoại; group xuyên panel giữ nhiều source box/panel; group metadata không bị bỏ.
Các stage scan, review, export, translation sử dụng group utterance ID nhất quán.

### Chuyển visual hints mà không phân loại lại

M6 ghép text theo mapping M5a và gắn các `visual_evidence_refs` M5b theo group/part.
Giữ nguyên hints/quality/rejection reasons; không dùng text vừa ghép để sửa nhãn
gợi ý hoặc tính lại classifier. Text và hints chỉ kết hợp tại M7 để chốt
`speech_style` cuối cho utterance.
---

[← Tổng quan v6](../README.md)
