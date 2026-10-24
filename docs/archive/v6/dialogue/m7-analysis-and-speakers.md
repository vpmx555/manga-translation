# M7 — Analyze chung, chốt người nói và loại thoại

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

**Phương pháp:** scan tên/ngữ cảnh trên text nhóm theo stage hiện tại. Analyze
nhận text đầy đủ, lời thoại lân cận, visual hints/evidence M5b của từng part và
candidate người nói. Trong **cùng lượt analyze hiện có**, model kết hợp nghĩa câu,
ngữ cảnh và gợi ý để chốt `speech_style` ở cấp utterance. Hint là bằng chứng có
thể sai; model có thể giữ, sửa hoặc trả unknown khi không đủ căn cứ.

Không chạy classifier nội dung hay call LLM riêng cho loại thoại trước/sau M7.
Payload hint nhỏ, chỉ gồm nhãn/đặc trưng/quality và ID tham chiếu; không gửi lại
ảnh chỉ để chốt style. Nếu analyze đã cần ảnh cho nhân vật/ngữ cảnh, dùng lại
crop nhóm với mapping rõ; không chỉ gửi một panel làm mất phần xuyên panel.
Validate speaker và speech style trong cùng JSON contract.

Model quyết định một speaker cho utterance. Mâu thuẫn không giải quyết được trả
`speaker_person_id=null` và nhãn `Other` ở biểu diễn phù hợp. Adapter export dùng quy ước
unknown hiện có. Không tạo identity `Other`, không ghi đè association MAGI gốc.
Kết luận loại thoại không được dùng để bịa người nói. Phân biệt visual hints M5b
với `speech_style` cuối M7 để audit sự đồng thuận hoặc khác biệt giữa chúng.

**Mô hình:** GLiNER scan hiện có; Ollama local `gemma4:e4b-it-q4_K_M` cho analyze.
Không thêm model chuyên phân loại câu thoại. Provider đã nhận `images`; crop nhóm
và contract group là phần cần bổ sung, không phải một inference style độc lập.

**Input:** normalized utterance M6 gồm text ghép và visual evidence M5b được
chuyển nguyên trạng, scan/glossary/character bank, context và ảnh nhóm nếu đã cần
cho analyze. Payload có ID nguồn tường minh; text được dùng tại M7, không đưa
ngược vào classifier M5b.

**Output:** một quyết định speaker và một loại thoại cuối cho cả utterance, kèm
evidence và tham chiếu hints đã xét.

```json
{
  "utterance_id": "p001:u01",
  "speaker_person_id": null, "speaker_display_label": "Other",
  "speaker_status": "unresolved",
  "speaker_evidence": "Conflicting source links; image and context insufficient",
  "speech_style": "unknown",
  "speech_style_evidence": "Visual hints and utterance context are insufficient",
  "visual_evidence_refs": ["p001:g01:part01:visual"],
  "addressee_person_ids": [], "source_part_ids": ["p001:g01:part01", "p001:g01:part02"]
}
```

**Điều kiện hoàn thành:** cùng nhóm có một speaker và một loại thoại cuối; text
con không bị coi là lượt nói của những người khác nhau. Hints/evidence gốc vẫn
truy xuất được. Test case hint và nghĩa câu mâu thuẫn để kiểm tra analyze sửa
nhãn hoặc trả unknown. Đổi text có thể đổi kết luận M7, nhưng không đổi hints
M5b nếu ảnh/mask giữ nguyên. Chốt style không tăng số call LLM ngoài lượt analyze;
cache analyze vẫn phụ thuộc utterance, context, hints, prompt/model/contract.
Unknown được giữ xuyên export/translation.

### Calling events và hai tầng

Semantic confirmation tạo [calling events](../identity/calling-events.md) cho
direct-address/recipient/person target đã phân giải trong response/receipt
analyze hiện có. Name mention chưa là call; không thêm lượt gọi model. Speaker/
addressee là person refs; raw cluster links giữ riêng. I3 resolve semantic refs
trước dịch/export mà không đổi output/bank MAGI.
---

[← Tổng quan v6](../README.md)
