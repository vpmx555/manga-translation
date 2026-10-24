# Calling events — Một lần gọi tên đã xác nhận

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

## Phân biệt với name mention

**Name mention** là một đoạn có tên trong text: ví dụ “I saw Arase yesterday”.
NER có thể đề xuất nó, nhưng đây chưa phải lần gọi trực tiếp và chưa xác định
cluster đang được nói tới. SFX như “Klink” bị đề xuất thành tên càng không là
bằng chứng gọi nhân vật.

**Calling event** là một lần một người gọi trực tiếp người khác bằng tên/cách
gọi, đã được analyze M7 xác nhận recipient và nguồn. Ví dụ người A nói
“Arase-san!” hướng tới nhân vật có raw cluster `8`; ngữ cảnh xác định tên đó
hướng tới `person17`. Khi người được gọi ngoài ảnh/chưa rõ, event có thể được
lưu nhưng không đủ để gán cluster.

Person candidates được phân giải qua tên/alias và context I1/M7; không lấy chính
link đang đề xuất ở I2 làm bằng chứng vòng tròn để xác nhận nó. Introduction có
thể lập tên/alias, nhưng không tự được đếm là một direct calling event.

## Phương pháp và mô hình

Dùng kết quả semantic confirmation trong lượt analyze hiện có, không gọi model
riêng để tạo event. Code đóng gói provenance và deduplicate nguồn. Caller chưa
xác định có thể để null; không biến sentinel Other thành person. Chỉ caller đã
biết mới có `caller_person_id`, nhưng việc chưa biết caller không tự làm mất một
lần gọi có recipient rõ.

**Input:** utterance/source boxes, direct-address confirmation, recipient cluster
và target person/context đã phân giải từ I1/M7.

**Output:** ví dụ một event, không phải cluster/person bank record:

```json
{
  "event_id": "p006:call01", "event_type": "direct_name_call",
  "source_utterance_id": "p006:u02", "source_text_ids": ["p006:t03"],
  "source_occurrence_key": "<image-hash-and-source-region-anchor>",
  "name_key": "arase-san", "caller_person_id": "person05",
  "recipient_cluster_ref": "chapter001:extract17:c8",
  "target_person_id": "person17", "confirmation_status": "confirmed",
  "evidence_refs": ["<M7-context-receipt>"]
}
```

## Quy tắc đếm và điều kiện hoàn thành

Một occurrence nguồn tính một lần: tên lặp trong cùng câu, receipt khác nhau,
replay cache hoặc OCR lại không tạo thêm lần gọi. Source occurrence key dựa trên
ảnh/vùng nguồn và target/role đã xác nhận, không dựa vào số thứ tự run/model.
Nếu OCR/group IDs đổi, adapter đối chiếu source-region anchor để tránh nhân đôi.

Hai lần gọi trong hai occurrence/utterance thực sự khác nhau có thể tính hai,
kể cả cùng caller. Chỉ events confirmed, recipient rõ và hướng tới cùng person
đã phân giải mới tăng support I2. Name collision chưa rõ không được cộng dồn để
biến một giả thuyết mơ hồ thành confirmed.

**Điều kiện hoàn thành:** event truy ngược được text/ảnh/recipient/context; không
đếm raw mention/SFX/replay như calling support. Không thay đổi bank MAGI khi lưu
event. Xem [I2](i2-identity-evidence.md) và [I1](i1-names-and-aliases.md).

---

[← Tổng quan v6](../README.md)
