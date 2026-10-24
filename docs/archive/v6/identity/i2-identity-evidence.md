# I2 — Nhất quán person qua nhiều cluster và ngoại hình

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

## Mục đích và hai tầng định danh

Một person có thể có dạng chibi, hóa hình, khác góc/nét vẽ, đổi trang phục hoặc
già hơn. MAGI có thể tách những appearance đó thành cluster khác vì embedding
khác xa. I2 giữ **cùng nhân vật trong truyện**, không yêu cầu cùng ngoại hình.

`cluster_id` là nhãn MAGI nguyên gốc; `person_id` là định danh ngữ nghĩa do tầng
person quản lý. I2 chỉ đọc bank/output MAGI. Không gộp cluster, sửa ID,
prototype/embedding/crop hay trạng thái bank MAGI.

## Trạng thái mã hiện tại

`bank/assignment.py` so embedding cluster với prototype bank, có
`max_distance=0.65`, `min_margin=0.02` và guard `used_by_page_panel`. Đây là
logic visual hiện có, không phải person-consistency I2 đã triển khai.
`bank/identity.py` có `merge_characters`, nhưng I2/I3 mới **không gọi hàm này**
để sửa bank gốc. Person registry/link store là tầng riêng.

## Phương pháp: caller support ưu tiên hơn độ giống ảnh

1. I1 sinh person candidates từ tên/alias đã xác nhận. Phân giải trùng họ/tên
   bằng context; một token tên giống nhau không tự chứng minh cùng người.
2. Nhận [calling events](calling-events.md) đã xác nhận ở M7: ai được gọi, raw
   cluster recipient nào, cách gọi hướng tới person nào và nguồn bằng chứng.
3. Tổng hợp theo cluster–person candidate, dedup cùng occurrence nguồn. Các lần
   gọi thật có thể từ cùng caller hoặc nhiều caller; số receipt/cache replay
   hoặc số từ tên lặp trong một câu không là số lần gọi độc lập.
4. Xét calling support trước. Ảnh/embedding là hỗ trợ và bằng chứng appearance,
   không bắt buộc phải giống nhau. Không suy ra khác person chỉ từ embedding xa,
   chibi, hóa hình hoặc tuổi tác.
5. Kiểm tra contradiction ngữ nghĩa cụ thể rồi trả decision cho I3. I2 không ghi
   person membership hoặc thay bank gốc.

### Các nhánh quyết định

| Nhánh | Bằng chứng | Quyết định |
|---|---|---|
| Caller lặp lại đã rõ | Ít nhất 2 occurrence gọi tên độc lập, cùng hướng tới một person đã phân giải, recipient cluster rõ, không có contradiction. | Supported dù visual similarity thấp/chưa kết luận; caller support ưu tiên. |
| Một lần gọi rõ | Một confirmed calling event + visual support phù hợp, không có contradiction. | Giữ nhánh đủ bằng chứng đã chốt Q16:A. |
| Chỉ giống ảnh | Embedding/crop giống nhưng chưa có calling support để nối cluster bị tách. | Visual candidate; chưa là I2 caller-supported. Logic visual cũ vẫn ở tầng của nó. |
| Chưa rõ target/tên trùng | NER/raw mention, người được gọi ngoài ảnh hoặc nhiều person cùng tên chưa phân giải. | Candidate/unknown; không cộng thành confirmed support cho cluster. |
| Contradiction thật | Context xác nhận khác người, target gán nhầm hoặc events hướng tới person khác nhau. | Giữ riêng/needs_review; caller count không vượt qua contradiction. |

Mức 2 occurrence là default thiết kế ban đầu cho “được gọi nhiều lần”, không
phải ngưỡng xác suất đã hiệu chỉnh. Không yêu cầu nhiều caller khác nhau. Một
lần gọi + ảnh vẫn có nhánh riêng. Tên đầy đủ khác có thể là alias; khác ngoại
hình/cùng panel không tự là contradiction. Reflection, chibi inset, flashback và
hóa hình cần xét context.

**Mô hình:** code tổng hợp evidence; dùng events từ lượt analyze Gemma đã có ở
M7 và embedding/crop MAGI đã có. Không thêm face model/call LLM so ảnh chỉ vì
embedding thấp. Không trộn association 0.25, detect 0.45 hay bank distance 0.65
thành ngưỡng person identity.

**Input:** person candidates/index I1, raw cluster refs/appearance evidence,
calling events M7 và context contradictions đã xác nhận.

```json
{
  "candidate_person_ids": ["person17"],
  "clusters": [
    {
      "cluster_ref": "chapter001:extract17:c3", "cluster_id": 3,
      "appearance_context": "regular", "embedding_ref": "<original-vector-ref>"
    },
    {
      "cluster_ref": "chapter001:extract17:c8", "cluster_id": 8,
      "appearance_context": "chibi", "embedding_ref": "<original-vector-ref>"
    }
  ],
  "calling_event_refs": ["p006:call01", "p007:call01"],
  "evidence_policy": "caller_first", "min_confirmed_call_events": 2,
  "single_call_path": "confirmed_call_with_visual_support"
}
```

`cluster_ref` chỉ là khóa tham chiếu scoped ở tầng mới; scope lấy từ receipt
MAGI thực tế. Không sửa `cluster_id` hoặc giả định nó unique toàn truyện.
Appearance context là metadata nếu analyze đã có bằng chứng, không cần
inference mới chỉ để gắn tag.

**Output:** đề xuất liên kết ở tầng person, có lý do và receipt.

```json
{
  "person_id": "person17",
  "cluster_refs": ["chapter001:extract17:c3", "chapter001:extract17:c8"],
  "same_person_status": "supported",
  "decision_basis": "repeated_confirmed_calling_support",
  "caller_support": {
    "status": "supported", "confirmed_event_count": 2,
    "event_refs": ["p006:call01", "p007:call01"]
  },
  "visual_evidence": {
    "status": "different_appearance",
    "role": "supporting_not_required_for_repeated_calls",
    "evidence_refs": ["<original-embedding-and-crop-refs>"]
  },
  "contradictions": [], "source_cluster_bank_mutated": false,
  "decision_receipt": "<fingerprint>"
}
```

**Điều kiện hoàn thành:** repeated calling support giữ person qua chibi/hóa
hình/góc vẽ/già đi dù MAGI split; tên trùng/target chưa rõ không bị nối chỉ vì
đếm từ. Replay không tạo occurrence mới. Không nối bắc cầu nếu A/C được context
xác nhận khác người. Bank MAGI trước/sau có cùng hash, ID/embedding/prototype
nguyên trạng. Đo false person link/split, event count và chi phí trên M11.

[Áp dụng tại I3](i3-canonical-identity.md); [hai tầng ID](../foundation/data-contracts.md).

---

[← Tổng quan v6](../README.md)
