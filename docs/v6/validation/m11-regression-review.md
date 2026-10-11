# M11 — Render, đối chiếu và kiểm chứng

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

**Phương pháp:** render bằng cùng font/metrics với M9, lưu PNG toàn trang, overlay
contour group/part/bridge và bảng lỗi. Đầu ra review đặt baseline hiện tại bên trái,
thiết kế mới bên phải trên cùng nguồn/config. AIP419 là cột tham khảo tùy chọn,
không làm oracle.

**Mô hình:** không có model cho đánh giá/render. Nếu render trên ảnh đã xóa chữ,
dùng output inpaint hiện có. Khi inpaint được bật, nền trắng/AOT chạy local theo
cấu hình hiện tại. Mask xóa chữ lấy từ text nguồn và vùng an toàn; không xóa cả
body hoặc phần contour đã dựng thêm. `inpaint.enabled=false` hiện tại cho phép
probe placement overlay; chưa gọi đó là trang dịch hoàn chỉnh đã làm sạch chữ.

**Input:** fixtures M0, các output M1–M10, ảnh nguồn và ảnh sạch nếu có.

**Output:** `review.md`, metrics JSON, trang so sánh PNG, receipts và danh sách
`needs_review`; không chỉ báo một điểm tổng.

```json
{
  "config_fingerprint": "<hash>",
  "geometry": {"closed_valid_groups": 0, "boundary_score": null, "mask_iou": null, "reconstructed_fraction": null},
  "topology": {"false_merges": 0, "false_splits": 0, "lost_observed_bridges": 0},
  "mapping": {"matched_texts": 0, "ambiguous_texts": 0, "duplicate_source_texts": 0},
  "hints": {"per_label": {}, "unknown_coverage": null, "confirmed_sample_count": 0},
  "utterances": {"speaker_conflicts": 0, "source_text_loss": 0},
  "typeset": {"fit_groups": 0, "needs_review": 0, "outside_safe_pixels": 0, "bridge_overlap_pixels": 0},
  "cost": {"local_inference_seconds": null, "extra_crop_passes": 0, "translation_rewrites": 0}
}
```

Giá trị 0/null trong ví dụ là placeholder schema, không phải số đo đã đạt.

**Điều kiện hoàn thành và kiểm tra bắt buộc:**

- Ring kín/hợp lệ/trong ảnh; contour thân được đo riêng với contour toàn bộ.
- Không mất cầu nối đã được xác nhận; không gộp hai bubble độc lập ở fixture âm;
  mọi false merge/split trên ảnh thật được liệt kê.
- Các part cùng nhóm có cùng speaker cuối hoặc cùng unknown; không mất/lặp text.
- Bóng đèn không nhận thought từ hình học đơn lẻ. Báo confusion matrix, precision,
  số mẫu từng nhãn và unknown coverage. Pilot ít mẫu không đủ kết luận một tỷ lệ
  chính xác cho toàn bộ manga.
- Glyph containment/bridge overlap/lost tokens đều bằng 0 đối với output `fit`.
- Ca overflow có vòng thử hữu hạn và trạng thái đúng; bản dịch rút gọn được review
  về nghĩa. Không báo thành công cho nhóm `needs_review`.
- Kiểm tra legacy run/cache: không tái sử dụng output schema cũ như output v2;
  fingerprint đổi khi membership, thuật toán, font hoặc config đổi. Run cũ giữ
  hành vi theo manifest cũ.

### Kiểm chứng bổ sung N2/N3 và I1–I3

**Phương pháp:** replay fixtures có nhãn kỳ vọng và đối chiếu receipt/journal,
không dùng số lần từ xuất hiện thay cho số event gọi tên. Chạy cùng model/config
đã khóa ở M0; review ngữ nghĩa bổ sung không thay được kiểm tra cấu trúc dữ liệu.

**Input:** output role/effects, name index, calling events, visual evidence,
canonical resolver/journal và bản dịch JSON/Markdown, cùng các fixture dương/âm.

**Output:** báo cáo pass/fail theo fixture, collision/unknown counts, false name
commits, redirect coverage và ảnh đối chiếu. Các ca bắt buộc gồm:

- Cùng tên/họ nhưng khác người: key trả nhiều ứng viên; context không đủ thì
  unknown, không merge chỉ theo token tên.
- Một call rõ + ảnh phù hợp giữ Q16:A; repeated calls confirmed cùng person/
  recipient đủ nối dù chibi/hóa hình/góc vẽ/già đi làm embedding xa. Raw NER/
  target chưa rõ không đủ; appearance khác không tự là contradiction. Context
  xác nhận khác người vẫn ngăn nối.
- Replay cùng event/cache: không sinh bằng chứng mới; A–B và B–C không tự kéo
  A–C vào cùng identity khi có contradiction.
- Tên/alias/address forms lưu và lookup chữ thường; họ/tên đệm và honorific
  qua nhiều OCR box khớp đúng, raw OCR/span không bị sửa.
- Nhiều cluster cùng person nhưng hai tầng ID riêng; source MAGI bank hash/
  IDs/embeddings/prototypes/assignment không đổi. Semantic refs resolve qua
  person store; revert chỉ thay person journal.
- Klink/Clench ở role effects và tên thật đồng âm: xét từng occurrence bằng
  evidence/ngữ nghĩa; context và bản dịch effects được giữ trong JSON/Markdown,
  không bị noise filter xóa khỏi nhánh effects và không có SFX typeset trên ảnh.
- OCR dạng cont-ribu-tion/Arase -san: kiểm tra analyze/dịch trên giả định Q12;
  ghi lỗi nếu có, không âm thầm thêm bước phục hồi token.

**Điều kiện hoàn thành:** không có false identity merge ở fixtures âm, không
còn person redirect/reference lệch sau link-store apply/revert, effects xuất đúng Q17:A và
các giả định OCR/role được báo bằng kết quả cụ thể. Các chỉ số pilot cần công bố
kèm config; tài liệu chưa khẳng định một mức chất lượng đã đạt.

### Kiểm chứng ranh giới gợi ý loại thoại và chi phí

Giữ nguyên ảnh/mask/geometry, thay OCR content, text boxes và dialogue score:
visual evidence/cache key M5b phải bất biến. Kiểm tra contract whitelist và
dependency graph để không đưa text mapping hoặc semantic output vào M5b.
Mock inference receipts xác nhận M5b không gọi OCR/LLM hoặc detect thêm.

Giữ hints cố định, đổi utterance/context trong fixture: M7 được thay đổi loại
thoại cuối với evidence phù hợp; không tự ép theo hint. Đếm call xác nhận field
`speech_style` nằm trong lượt analyze hiện có, không có call style riêng.
Báo precision/unknown coverage của hints và kết quả style cuối M7 riêng; ghi
thời gian feature extraction/cache hit và số inference thực tế.
### Fixtures caller-first và source immutability

- Regular → chibi/hóa hình/già đi/góc vẽ: embedding thấp, ít nhất hai confirmed
  calling occurrences; person chung, cluster gốc riêng.
- Người khác trùng tên: recipient/target person khác, không merge theo token/count.
- Caller chưa rõ nhưng recipient/context rõ: Other không là person; event có
  thể đóng góp. Gọi người ngoài ảnh không tự gán cluster trên ảnh.
- Snapshot MAGI bank trước/sau I2/I3/apply/replay/revert: hash/nội dung nguyên
  trạng; chỉ person store/index/journal/downstream semantic refs thay đổi.

Báo metric repeated-caller/single-call/visual-only riêng; visual similarity không
là điều kiện pass bắt buộc cho repeated-caller.
---

[← Tổng quan v6](../README.md)
