# M5 — Liên kết text và tạo gợi ý loại thoại từ ảnh/mask

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

M5 có hai nhánh với hợp đồng input/cache riêng. **Chỉ M5b tạo gợi ý loại
thoại; nhánh này không sử dụng text.** Việc liên kết vị trí text ở M5a không được
cấp dữ liệu hay đặc trưng cho classifier M5b.

## M5a — Liên kết text với group/part

**Phương pháp liên kết:** dùng coverage text box với mask thân và group, khoảng
cách đến vùng thân, nguồn detect và thứ tự đọc. Loại candidate trùng trước khi xét
ambiguity. Text vượt nhiều part có thể giữ nhiều tham chiếu part trong cùng group;
không nhân bản text. Nếu nằm giữa các group không phân giải được, giữ candidate
mapping và trạng thái chưa rõ.

**Input:** text box/bbox nguồn M2 và geometry/masks M4. Nội dung OCR được chuyển
đến M6; không dùng để sinh gợi ý loại thoại.

**Output:** mapping vị trí, tách khỏi visual evidence.

```json
{
  "text_id": "p001:t0",
  "group_id": "p001:g01", "part_ids": ["p001:g01:part01"],
  "mapping": {"status": "matched", "coverage": 0.96, "candidate_groups": []}
}
```

## M5b — Gợi ý loại thoại chỉ từ đặc trưng hình ảnh/mask

**Phương pháp phân loại:** code trích đặc trưng và luật nhiều bằng chứng, có quyền
trả unknown. Các đặc trưng được phép:

- Hình học mask/contour thân: độ tròn, độ nhọn/lồi lõm, tỷ lệ hình dạng và độ ổn
  định ở nhiều mức simplify; loại đuôi, cầu nối và đường cắt part khỏi viền thân.
- Kiểu viền từ pixel trong dải gần biên trên ảnh nguồn: support nét, độ liên tục,
  các run nét đứt, độ đều và tương phản. Bỏ đoạn dựng thêm; không chỉ sample đúng
  trên contour simplify như hiện tại.
- Chất lượng quan sát từ mask/geometry: tỷ lệ biên có support thật, tỷ lệ dựng
  thêm và phần bị cắt; giảm độ tin cậy khi đặc trưng không ổn định.

Không nhận chuỗi OCR, từ khóa, ngôn ngữ, số/vị trí text box, OCR confidence,
reading order, mapping M5a, name/speaker hay MAGI dialogue score. Không dùng OCR
để che vùng ảnh hoặc tạo đặc trưng mật độ chữ. Ảnh nguồn vẫn có thể chứa chữ ở
dạng pixel; classifier không đọc/nhận dạng chữ và không phân tích nội dung đó.

Shape giống mây hoặc solid/rounded không tự chứng minh thought/normal.
Caption/whisper/shout/thought chỉ là visual hints cho M7. Những part có hints
khác nhau giữ evidence riêng, không ép cả group thành một nhãn ở M5.

**Mô hình:** classifier visual bằng code trên ảnh/mask có sẵn. Không thêm model
style, không gọi OCR hoặc LLM cho M5b, không tạo thêm lượt inference Koharu/MAGI
chỉ để phân loại. Dùng lại contour/mask M4 và crop ảnh cần thiết.

**Input:** ảnh nguồn/crop và masks/geometry M4 cùng ID/provenance kỹ thuật.
Metadata ID chỉ để truy vết, không là feature. Ví dụ hợp đồng:

```json
{
  "group_id": "p001:g01", "part_id": "p001:g01:part01",
  "image_ref": "<source-image>", "crop_bbox": [100, 200, 500, 600],
  "mask_refs": {
    "body": "<body-mask>", "tail": "<tail-mask>", "bridge": "<bridge-mask>"
  },
  "boundary_ref": "<observed-and-inferred-segments>"
}
```

**Output:** visual evidence theo part/group, độc lập text mapping và loại thoại
cuối cùng. Score heuristic không được coi là xác suất.

```json
{
  "evidence_id": "p001:g01:part01:visual",
  "group_id": "p001:g01", "part_id": "p001:g01:part01",
  "feature_sources": ["image_pixels", "geometry_masks"],
  "text_used": false,
  "features": {
    "shape": "rounded", "border_style": "solid",
    "observed_boundary_fraction": 0.72
  },
  "score_kind": "heuristic_not_calibrated",
  "hints": [], "status": "unknown",
  "rejection_reasons": ["insufficient_observed_boundary"]
}
```

## Chi phí tính toán và điều kiện hoàn thành

Cache M5b dùng image/mask/geometry hash, phiên bản classifier và tham số visual;
không phụ thuộc OCR text/hash hay mapping. Cache M5a có dependency text-box riêng.
M6 chuyển hints nguyên trạng; M7 kết hợp text/ngữ cảnh với hints trong lượt
analyze hiện có để chốt loại thoại, không thêm call riêng cho speech style.

**Điều kiện hoàn thành:** đổi nội dung OCR, text boxes hoặc dialogue score mà
giữ nguyên ảnh/mask thì output/cache key M5b không đổi. Payload classifier không
có các trường text; mock caller kiểm tra không phát sinh OCR/LLM inference.
Đo precision và unknown coverage visual riêng trên ca tham chiếu; bóng đèn/đồ
trang trí không tự thành thoại từ hình học đơn lẻ. Chỉ M7 có kết luận
`speech_style` cuối, xem [hợp đồng analyze](../dialogue/m7-analysis-and-speakers.md).

---

[← Tổng quan v6](../README.md)
