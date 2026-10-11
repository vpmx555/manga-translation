# N3 — Hợp đồng honorific và tên chữ thường

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

**Phương pháp:** NFC và casefold tên/alias trước lưu trữ/so khớp. Parse suffix để
lưu `base_name`, `honorific` và address form chữ thường; hỗ trợ thành phần tên đã
xác minh qua ngữ cảnh. Ví dụ `Arase -san`/`arase- San` có key base `arase`, suffix
`san`, alias gọi tên `arase-san`. OCR nguyên văn nằm trong dữ liệu nguồn riêng;
tên display/alias đã lưu dùng chữ thường theo yêu cầu mới.

GLiNER hiện đã nhận suffix có khoảng trắng/dash nhưng giữ literal spelling; cần
khóa lookup và validator suffix có cấu trúc. Không tạo
nhân vật khác nhau chỉ vì spacing/honorific khác nhau. Base-name key chung chỉ
tạo person candidates: hai nhân vật trùng họ vẫn cần context để phân giải.

**Mô hình:** code parse/canonical và index; GLiNER đề xuất span ở scan, Gemma
semantic confirmation ở M7 như đã thiết kế. Không có gate/quarantine tên-vs-SFX
độc lập theo Q14: dùng luồng semantic confirmation hiện tại.
Không chỉ tăng GLiNER threshold để giải quyết mọi lỗi role.

**Input:** text nguồn, span/bbox nguồn, role evidence N2, glossary và bank đã
xác nhận.

**Output:**

```json
{
  "surface": "arase -san", "source_surface": "arase -san",
  "base_name": "arase", "honorific": "san", "canonical_name_key": "arase",
  "person_status": "candidate", "person_id": null,
  "source_text_ids": ["p001:t0"], "person_store_write_status": "pending_confirmation"
}
```

**Điều kiện hoàn thành:** canonical name/glossary/honorific được dùng nhất quán
ở scan, analyze và translation validator; luồng hiện tại quyết
định khi nào ghi tên đã xác nhận. Raw span/bbox giữ nguyên. Bổ sung fixture surname chung,
honorific qua hai OCR box và từ thật có hyphen.

N2 và N3/I1 phải vào config fingerprint/cache version. Review role/evidence,
lookup key nếu có và thay đổi name bank; ambiguous được đếm riêng. N1 không nằm
trong luồng thực hiện.

Tên/honorific chuẩn hóa lưu ở person index riêng. Không sửa raw bank MAGI.
---

[← Tổng quan v6](../README.md)
