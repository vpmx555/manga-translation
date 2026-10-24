# Quy ước input/output chung

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

- Tọa độ dùng pixel của ảnh gốc, gốc ở góc trên trái. `bbox=[x1,y1,x2,y2]`, miền
  mask/bbox dùng quy ước nửa mở. Thêm `coordinate_space="page_pixels"` vào schema.
- Mask lưu PNG uint8, 0 ngoài vùng, 255 trong vùng; cùng kích thước ảnh gốc.
  Crop dự đoán được chuyển về mask toàn trang trước khi so sánh.
- Contour lưu các ring kín: điểm cuối bằng điểm đầu. Component tách rời có ring
  riêng; không dùng một đoạn nối giả để biến tất cả thành một polygon.
- ID group/part/text ổn định trong run. Mọi output có `schema_version`, source
  hash, config fingerprint, thuật toán/model receipt và trạng thái.
- `score` heuristic không được gọi là xác suất đã hiệu chỉnh. Chỉ dùng
  `probability` nếu có quy trình calibration và dữ liệu kiểm chứng phù hợp.
- Ví dụ ID `p001:g01`, `p001:g01:part01` chỉ minh họa. Khi triển khai dùng namespace
  document/page hiện tại và ánh xạ tường minh sang ID nguồn.

### Ranh giới input của gợi ý và kết luận loại thoại

M5a có text-box mapping; M5b có visual evidence theo part/group. Hai output không
dùng chung feature payload. Input M5b chỉ nhận ảnh/mask/geometry và provenance,
không chứa OCR/text box/dialogue score hay kết quả semantic; schema từ chối
trường ngoài whitelist. Visual hints chưa phải loại thoại cuối.

M6 ghép lời thoại nhưng chuyển hints nguyên trạng. M7 nhận utterance text/context
và hints, rồi xuất `speech_style` cuối cùng với evidence trong response analyze
đã có. Hash/dependency M5b độc lập OCR; M7 vẫn phụ thuộc text và context.
### Hai tầng ID và source bank bất biến

- `cluster_id`: giá trị MAGI gốc trong scope receipt. `cluster_ref` chỉ là scoped
  reference ở tầng mới, không rewrite ID/bank.
- `person_id`: nhân vật ngữ nghĩa ổn định; liên kết nhiều cluster/appearance.
  Name/address index trả person candidates.
- [Calling event](../identity/calling-events.md): lần gọi tên confirmed với
  source/recipient, khác NER/name mention.
- `cluster_to_person`, person names/redirects/journal nằm ở person store riêng.
  MAGI bank/embedding/prototype/assignment chỉ đọc.
- Contract mới dùng `speaker_person_id`/`addressee_person_ids`; legacy adapter
  phải ghi rõ tầng ID. Raw association/cluster refs giữ riêng.

Repeated calling support ưu tiên hơn visual similarity. Source-bank hash trước/
sau bằng nhau; revert chỉ thay person store/downstream cache.
---

[← Tổng quan v6](../README.md)
