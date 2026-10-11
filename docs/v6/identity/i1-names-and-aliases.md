# I1 — Index tên chữ thường, thành phần tên và hậu tố gọi tên

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

Mốc này mở rộng N3 theo chỉ đạo mới, không đưa phục hồi token OCR N1 trở lại.

**Phương pháp:** lưu các trường tên bằng NFC + casefold; normalize spacing/dash
trong khóa gọi tên, giữ `base_name` và `honorific` riêng. Hậu tố đã nhận biết được
lưu trong address forms (`arase-san`, `arase-kun`...), không tạo person chỉ tên
`san`/`kun`. Index ánh xạ mỗi name key đến danh sách person candidates, không
ép một key chỉ có một người.

Tách thành phần tên từ introduction/glossary/cách gọi có evidence: tên đầy đủ,
tên gọi, tên đệm, họ và biệt danh. Không suy ra họ/tên chỉ bằng vị trí token vì
thứ tự tên thay đổi theo ngôn ngữ/truyện. Thành phần chưa rõ dùng
`component_unknown`; alias thành phần chỉ trở thành cách gọi đã xác nhận khi có
căn cứ trong context. Chuỗi giống nhau sinh giả thuyết cùng người; name collision
được phân giải theo Q15. Scope index là một story bank, không toàn bộ các truyện.

**Mô hình:** code index/parser; GLiNER và Gemma hiện có để xác nhận person/name,
name components và cách gọi theo context ở M7. Không thêm model nhận diện tên.

**Input:** person registry/name index riêng, legacy đọc qua adapter, name
mentions/raw span/context đã xác nhận và person candidates. Không sửa bank
cluster/embedding MAGI gốc.

```json
{
  "mention_id": "p001:mention01", "surface": "Arase -san",
  "source_text_id": "p001:t0", "span": [0,10],
  "mention_type": "direct_address", "target_person_id": "person17",
  "confirmation_source": "existing_semantic_confirmation"
}
```

**Output:** person name record và lookup index; những tên đã lưu đều chữ thường.
Ví dụ minh họa, không phải person bank thực tế:

```json
{
  "person_id": "person17", "primary_name": "arase",
  "name_forms": [{
    "surface": "arase-san", "base_name": "arase", "honorific": "san",
    "kind": "address_form", "status": "confirmed",
    "source_mention_ids": ["p001:mention01"]
  }],
  "components": [{"value": "arase", "kind": "component_unknown", "status": "confirmed_as_address"}],
  "lookup_keys": ["arase", "arase-san"],
  "lookup_index": {"arase": ["person17"], "arase-san": ["person17"]}
}
```

**Điều kiện hoàn thành:** tên/alias đã lưu chữ thường; fullname/partial/suffix
có thể sinh đúng candidates; collision không bị index ghi đè; không merge chỉ
vì hai người có cùng surname. Khóa base không làm mất hậu tố trong address form
hoặc yêu cầu bảo toàn honorific khi dịch. `Other` là sentinel không có người nói,
không phải tên người để đưa vào index. Raw OCR không được lower toàn câu để đạt
yêu cầu lowercase tên.


### Collision đã chốt theo Q15:A

Một key tên/họ/tên đệm hoặc address form trả danh sách person candidates.
Chỉ chọn khi context và evidence xác định được người được nhắc; nếu vẫn nhiều
ứng viên thì giữ `person_id: null` và danh sách ứng viên. Cùng một token tên
không tự tạo alias cho tất cả người có họ đó và không tự merge các identity.

### Tầng lưu trữ

Name/address index thuộc person store riêng; keys trả person candidates.
Source bank MAGI chỉ đọc. Name mention chưa là [calling event](calling-events.md);
I2 cần recipient/direct-address/context đã xác nhận.
---

[← Tổng quan v6](../README.md)
