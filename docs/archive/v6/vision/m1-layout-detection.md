# M1 — Thu candidate toàn trang và theo panel

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

**Phương pháp:** giữ lượt predict toàn trang và các crop panel hiện có. Lưu mọi
candidate đạt ngưỡng trước khi suppression làm mất dữ liệu. Tính cờ cắt ở mép
crop; lưu origin và phạm vi nhìn thấy. Panel order được khóa trước M2.

**Mô hình:** Koharu pinned như [Mô hình, phiên bản và phạm vi sử dụng](../foundation/models-and-config.md), chạy local trong môi trường layout.

**Input:** ảnh RGB `H×W×3`, source hash, config layout, model receipt.

**Output:** `layout-candidates.json` cùng các mask PNG toàn trang.

```json
{
  "schema_version": 2,
  "page_id": "p001",
  "coordinate_space": "page_pixels",
  "image_size": [1200, 1800],
  "panels": [{"id": "p001:panel01", "bbox": [0, 0, 1200, 800], "reading_order": 0}],
  "candidates": [{
    "id": "p001:c01", "class": "bubble", "score": 0.86,
    "bbox": [300, 400, 720, 930], "mask_path": "visual-v2/masks/p001/c01.png",
    "pass": "full_page", "crop_bbox": [0, 0, 1200, 1800],
    "crop_clipped": false, "source_hash": "<hash>"
  }]
}
```

**Điều kiện hoàn thành:** mask đúng kích thước; tọa độ crop chuyển chính xác;
candidate còn đủ provenance. Ở mốc này chưa xác định chắc part hay lời thoại.
Crop bổ sung cho vùng nghi ngờ bị cắt chỉ chạy có budget hữu hạn, sau khi đã kiểm
tra dữ liệu hiện có; số lượt predict được ghi vào receipt.

---

[← Tổng quan v6](../README.md)
