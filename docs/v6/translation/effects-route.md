# Dịch SFX và nhãn trạng thái riêng

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

### Áp dụng Q13=A vào M6/M8

**Phương pháp:** M6 tạo bucket `effects` tách khỏi `utterances`. Các record đã
xác nhận SFX/action-state được giữ làm context cho analyze; không coi chúng là
lượt nói hay tên nhân vật chỉ vì được dịch. Các whole-box rule trước đây drop
SFX phải route sang bucket này, còn giữ nguồn/reason. Điều chỉnh source adapter,
validator và cache của dịch để nhận target effects cùng target lời thoại.

**Mô hình:** Gemma local như M8. Có thể batch effects với các target khác nếu
schema phân biệt rõ loại; không cần một model dịch SFX riêng.

**Input:** text role đã xác nhận từ N2, nguồn/bbox, crop/context và ngôn ngữ đích.

```json
{
  "effect_id": "p001:effect01", "source_text_ids": ["p001:t5"],
  "text_role": "action_state", "text": "Clench",
  "bbox": [10,20,110,100], "speaker_id": null,
  "context_usage": "nonverbal_scene_evidence", "translation_bucket": "effects"
}
```

**Output:** bản dịch effects riêng, không chia vào các contour của nhóm thoại.

```json
{
  "effect_id": "p001:effect01", "text_role": "action_state",
  "source_text": "Clench", "translation": "<context-reviewed-translation>",
  "speaker_id": null, "status": "translated",
  "render_policy": "json_markdown_only"
}
```

**Điều kiện hoàn thành:** SFX không mất khỏi context/đầu ra dịch; không nhân đôi
thành utterance; không mặc định có speaker. Effects và utterances có ID/provenance
riêng, mọi source text thuộc route phù hợp. Kiểm tra không còn rule drop SFX làm
mất effect đã xác nhận trước M8.


### Đầu ra đã chốt theo Q17:A

Bản dịch effects chỉ nằm trong JSON/Markdown review, kèm role, source text IDs,
source bbox và evidence. Không đưa target này qua M9/M10, không erase, inpaint
hoặc typeset SFX trên ảnh. Giữ SFX/trạng thái đã xác nhận trong scene context cho
analyze/dịch lời thoại theo Q13:A. Role chưa rõ vẫn theo semantic confirmation
hiện tại theo Q14:B; không thêm quarantine độc lập.

**Điều kiện hoàn thành:** effects có bản dịch và provenance riêng; ảnh xuất
không đổi tại SFX vì nhánh effects. Nhãn ngữ nghĩa đã xác nhận quyết định bucket,
không dùng chỉ một chuỗi như `klink` để áp nhãn cho mọi occurrence.

---

[← Tổng quan v6](../README.md)
