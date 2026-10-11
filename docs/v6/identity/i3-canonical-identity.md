# I3 — Liên kết cluster vào person ở tầng riêng

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

## Phạm vi ghi dữ liệu

**Phương pháp:** I2 supported thì ghi `cluster_ref → person_id` trong **person
registry/link store mới**, tách khỏi bank MAGI nguyên gốc. Một person có nhiều
cluster/appearance. Không rename/merge/disable cluster, không gọi
`merge_characters`, không sửa assignment/guard/prototype của bank gốc.

Nếu hai person records do tầng mới tạo được I2 xác nhận là cùng người, giữ một
canonical `person_id` và person redirect **chỉ ở person registry**. Cluster IDs
không có redirect này. Evidence chưa đủ thì giữ candidate links cho chapter sau.

Appearance exemplars là tham chiếu tới embedding/crop gốc, có thể ghi chibi/hóa
hình/tuổi/góc vẽ khi context đã biết. Không dùng một mean để ép mọi appearance
gần nhau, không viết prototype mới vào bank MAGI.

## Resolver downstream

Analyze/translation/name index/glossary/memory/export resolve theo person,
đồng thời giữ raw MAGI associations/cluster refs. Person không thay cluster.

Contract mới ưu tiên `person_id`, `speaker_person_id`, `addressee_person_ids`
và raw `cluster_refs` riêng. Legacy `identity_id`/`speaker_id`/`name_target_id`
cần adapter ghi rõ tầng; không suy diễn hai tầng bằng ID có cùng số.

Đổi membership chỉ invalid cache target/context bị ảnh hưởng. Reanalysis cần có
receipt và số vòng hữu hạn; không rerun MAGI hoặc sửa bank gốc. Cannot-link ở
person layer dựa trên context xác nhận khác người, không cấm cùng person có
nhiều cluster trong panel khi là inset/reflection/flashback.

**Mô hình:** code person resolver/link store/validators, không thêm model.
**Input:** I2 decision, person registry snapshot, bank MAGI read-only và run refs.
**Output:** membership/index/person redirects nếu cần và journal ở tầng person.

```json
{
  "operation_id": "<idempotent-person-link-operation>",
  "person_id": "person17", "person_redirects": {},
  "cluster_to_person": {
    "chapter001:extract17:c3": "person17",
    "chapter001:extract17:c8": "person17"
  },
  "name_keys": ["arase", "arase-san", "arase-kun"],
  "appearance_refs": ["<regular-source-crop>", "<chibi-source-crop>"],
  "evidence_receipt": "<I2-receipt>",
  "person_store_before_hash": "<before>", "person_store_after_hash": "<after>",
  "source_cluster_bank_hash_before": "<unchanged>",
  "source_cluster_bank_hash_after": "<unchanged>",
  "affected_target_ids": ["p006:u01", "p007:u01"], "status": "applied"
}
```

## Idempotency và hoàn tác

Journal chỉ lưu membership/name-index/person-redirect changes ở tầng mới.
Apply lại cùng evidence không nhân đôi. Revert phục hồi person store và
downstream targets; không có bước revert MAGI bank vì chưa bị thay đổi.

Legacy có thể được đọc qua adapter để tạo person records riêng với receipt;
không migrate/ghi đè bank cluster gốc. Redirect không có cycle, mỗi membership
có provenance. Tên/alias mới trong person store chữ thường; raw source giữ nguyên.

**Điều kiện hoàn thành:** person resolve đúng qua nhiều cluster/appearance;
hash và mọi cluster ID/embedding/prototype/assignment bank MAGI không đổi.
Idempotency/revert áp dụng được ở person store. Downstream phân biệt hai tầng;
không false person merge ở fixture trùng tên/khác người. M11 đo false merge/split,
event dedup, resolver coverage và source-bank immutability; ít person hơn không
tự chứng minh chất lượng.

[Caller-first I2](i2-identity-evidence.md); [hai tầng ID](../foundation/data-contracts.md).

---

[← Tổng quan v6](../README.md)
