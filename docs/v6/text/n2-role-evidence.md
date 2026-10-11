# N2 — Bằng chứng SFX/trạng thái trước khi xác nhận tên

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

**Phương pháp:** giữ lớp Koharu `onomatopoeia` index 1 và bbox/confidence từ các
lượt predict; bubble/panel là index 2/3 (`visual/koharu.py:25–28`). Refactor M1 để
thu các lớp cần thiết trong cùng pass, với ngưỡng lọc riêng từng lớp. Ngưỡng SFX
hint là tham số riêng cần hiệu chỉnh, không mặc nhiên bằng bubble threshold.
Không giả định mask lớp 1 đã được adapter kiểm chứng như mask bubble.

Kết hợp overlap bbox OCR với onomatopoeia bbox, crop ảnh, vai trò trong câu,
context và tên đã xác nhận. Lexicon SFX chỉ là bằng chứng hỗ trợ. `Klink` đơn lẻ
trên nét hiệu ứng khác `Klink, help me!`; `Clench` nhãn hành động khác `Clench your
fists!`. Ngoài bubble không tự đồng nghĩa SFX; trong bubble không tự chứng minh
một từ là tên người.

**Mô hình:** Koharu đã có cho visual hint; code cho evidence; Gemma local xét
role mâu thuẫn/không rõ bằng text và crop. Kết luận person/name cuối vẫn phải qua
semantic confirmation ở M7. Vai trò chưa rõ không được biến thành `sfx` chắc chắn
chỉ dựa trên danh sách từ.

**Input:** raw text M2 và group context M3–M5, OCR bbox, SFX detections M1 và ảnh.

```json
{
  "text_id": "p001:t5", "raw_text": "Clench", "text": "Clench",
  "ocr_bbox": [10,20,110,100],
  "onomatopoeia_candidates": [{"bbox": [8,18,115,105], "score": 0.82}],
  "image_crop_path": "<local-crop-path>", "group_context": "<context>",
  "known_name_candidates": []
}
```

**Output:** role và evidence tách khỏi speaker/style. SFX/action-state đã xác
nhận đi vào context và target dịch effects riêng theo Q13. Khi role chưa rõ,
luồng xác nhận tên hiện tại quyết định theo Q14; không thêm gate/quarantine độc
lập chỉ vì role chưa được phân giải.

```json
{
  "text_id": "p001:t5", "role": "action_state", "role_status": "confirmed",
  "role_evidence": ["<reviewed-image-and-context-evidence>"],
  "name_candidate_status": "non_person",
  "routing_policy": "context_and_separate_translation",
  "name_resolution_policy": "existing_semantic_confirmation",
  "bank_write_allowed": false
}
```

Role đề xuất: `dialogue`, `narration`, `sfx`, `action_state`, `unknown`. Đây là
field/schema mới; `action_state` chưa phải content type có sẵn. Khi route phải
cập nhật validators/adapter, thay vì gửi một enum mới vào contract cũ.

**Điều kiện hoàn thành:** có test phân biệt tên/ngữ cảnh lời thoại với SFX/nhãn
trạng thái; unknown có evidence; SFX được giữ context và dịch riêng theo Q13. Kiểm
tra model call budget và cache để không xét lại cùng crop qua nhiều stage.


Hợp đồng honorific ở [N3](n3-honorific-contract.md); index tên đầy đủ ở [I1](../identity/i1-names-and-aliases.md). N2 đưa evidence vào luồng xác nhận hiện tại, không tạo gate/quarantine riêng.

---

[← Tổng quan v6](../README.md)
