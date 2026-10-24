# M2 — Extract text, OCR, nhân vật và bằng chứng liên kết

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

**Phương pháp:** MAGI detect và association với các ngưỡng đã chốt; dùng snapshot
panel của M1. Hayai đọc crop text bằng adapter hiện có, giữ bbox/ID gốc. Lưu cả
association ứng viên cần cho phân tích nhóm, thay vì chỉ giữ nhãn thắng nếu adapter
hiện tại chưa xuất đủ điểm.

**Mô hình:** MAGIv2; Hayai OCR trong môi trường riêng. Không thay Hayai bằng MAGI
OCR âm thầm khi môi trường chưa sẵn sàng.

**Input:** ảnh RGB theo adapter extraction, panel snapshot, config MAGI/Hayai và
character bank hiện có.

**Output:** `extract/raw` hiện có, bổ sung bằng chứng association có version.

```json
{
  "page_id": "p001",
  "texts": [{
    "id": "p001:t0", "bbox": [350, 450, 530, 550],
    "text": "I thought", "panel_index": 0, "reading_order": 0,
    "is_essential_text": true,
    "dialogue_score": {"value": 0.7, "kind": "logit"},
    "character_candidates": [{"character_id": "char-A", "association_score": 0.64}],
    "speaker_id": "char-A", "speaker_display_label": "<existing-label>",
    "tail_index": 0, "ocr_backend": "hayai"
  }],
  "tails": [{"id": "p001:tail0", "bbox": [430, 560, 460, 620]}]
}
```

MAGI hiện cung cấp tail box và association; chưa có mask đuôi. M4 phải kết hợp box
đó với ảnh/mask khi suy ra vùng đuôi. Không coi toàn tail box là hình đuôi chính xác.

**Điều kiện hoàn thành:** mỗi OCR string gắn đúng text ID/bbox; nguồn và panel
snapshot khớp. `dialogue_score`/điểm candidate mới cần adapter score capture;
không tuyên bố mọi field ví dụ đã có trong `extract/raw` hiện tại.

---

[← Tổng quan v6](../README.md)
