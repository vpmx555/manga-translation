# Kiến trúc và trạng thái v7

[← Chỉ mục](../README.md)

## Các lớp dữ liệu

Ảnh trang là nguồn hình học. MAGI tạo detection và embedding; Koharu tạo layout
và mask. OCR chỉ đọc crop text đã phát hiện. Normalize chuyển các box/part thành
utterance có vị trí nguồn; scan và analyze làm việc trên các đơn vị này.

Bank MAGI lưu nhận dạng thị giác theo truyện. Person registry nằm ở tầng ngữ
nghĩa phía trên: một identity có thể gồm nhiều MAGI ID. Profile và relationship
được commit trước khi dịch, rồi đóng băng vào run. Translation đọc snapshot,
không xây quan hệ lại và không thay person registry.

## Trách nhiệm từng stage

| Stage | Đầu vào chính | Quyết định / đầu ra | Mô hình |
|---|---|---|---|
| `layout` | Ảnh trang | Panel, thứ tự, bubble candidates/mask và provenance | Koharu |
| `extract` | Ảnh + layout + bank | Text/character boxes, liên kết thô, OCR, embedding, MAGI bank IDs | MAGI + GLM-OCR |
| `bubble_geometry` | Mask/layout và text boxes | Group/part, contour/body, tail/bridge, text mapping, hints hình học | Hình học; backend GPU theo runtime |
| `normalize` | OCR + geometry | Utterance ổn định, reading order, source positions, noise evidence | Quy tắc |
| `scan` | Utterance toàn chapter + registry | Scene, alias groups, sửa tên replace-all tuần tự và snapshot OCR hiện hành | Qwen/vLLM, một request |
| `analyze` | Scene + text batch + cluster/person metadata; không gửi ảnh | Content type, speaker, addressee, mentions, semantic evidence; merge | Qwen/vLLM |
| `build_identity` | Scan/analyze + registry + panel và vector/crop bank | Gắn tên theo nhóm, gate >=8 crop chapter cho identity mới, liên kết MAGI và profile | Quy tắc + Qwen/vLLM |
| `relationship` | Identity/profile + giao tiếp trực tiếp | Social relations và hai hướng xưng hô độc lập | Qwen/vLLM |
| `export` | Analyze + identity + graph | Document cho translation/render, person references đã relink | Quy tắc |
| `inpaint` | Trang và vùng chữ hợp lệ | Trang sạch và provenance mask; chỉ có khi bật | White fill / AOT |
| `translate` | Export + graph/profile + glossary/overrides | Một bản natural tiếng Việt cho mỗi target | Qwen/vLLM |
| `typeset` | Natural text + source positions + trang nền | Ảnh dàn chữ, bố cục và cờ overflow | Pillow/font/layout rules |

Thứ tự trên là thứ tự stage của run. DAG dùng phụ thuộc thực tế để chọn các
ancestor cần chạy cho `--until`; không suy từ tên thư mục để sửa dependency.

## Chính sách đang có trong code

- Analyze theo `independent-batches-v6.1`; không truyền bản trả lời batch trước
  để batch sau tiếp tục một chuỗi hội thoại.
- Merge ưu tiên inference hợp lệ sau analyze; MAGI association/anchor là bằng
  chứng, không phải ràng buộc khóa người nói bất biến.
- Scan tên dùng cùng lượt LLM chia scene; chỉnh đúng span tên, không decode để
  viết lại toàn bộ OCR JSON. Phần còn nghi vấn vẫn được review.
- MAGI matching và hậu matching với bank là hai tầng khác nhau.
- Profile chỉ cho identity đã xác nhận có tên; không tạo tên để ép có graph.
- Xưng hô `A → B` khác `B → A`; quan hệ xã hội lưu riêng với address edges.
- Translation natural chạy song song theo batch analyze; phần target chưa có
  batch được gom thêm tối đa 8, không yêu cầu gần nhau hay cùng scene.
- Effects có dữ liệu dịch nhưng không được render như thoại lên ảnh.
- Request profile/relationship không retry; lỗi inference được lưu để review,
  không tự replay request failed/uncertain khi resume.

## Những điều không được suy ra từ việc stage completed

`completed` cho biết artifact đã commit theo hợp đồng; không chứng minh OCR,
speaker hay quan hệ đúng với câu chuyện. Stage identity/relationship có thể
hoàn thành mà không gọi model nếu không có identity có tên đủ điều kiện hoặc
không có cặp giao tiếp trực tiếp. Phải xem số job và trạng thái quyết định JSON.

Bank ID bị tách không chứng minh có hai người thật; identity layer có thể liên
kết chúng. Ngược lại embedding gần nhau không đủ để nhập hai identity có tên
đã được xác nhận khác nhau. Độ tuổi/tính cách từ hình ảnh là suy đoán có trạng
thái provisional, không phải sự thật trong truyện.

## Giới hạn hiện tại

Run mới có bootstrap tên trong build_identity; không yêu cầu analyze đã xác nhận
name owner. Vẫn có thể thiếu identity nếu mention_type thiếu/unknown, người không
có MAGI bank ID, nhóm alias nghi gộp nhầm hoặc evidence không đủ. Cặp giao tiếp
có thể được bổ sung từ bootstrap khi xác định rõ hai phía; không dùng lời dẫn
hay tên chỉ được nhắc tới để giả định một cuộc giao tiếp.

Đổi ngưỡng bank không bảo đảm số ID giảm đơn điệu: lựa chọn ứng viên, số lần
xuất hiện, one-to-one assignment và prototype được cập nhật qua thời gian có
thể làm đường đi của bank khác nhau. Cần so sánh cùng input/config/seed bank.

Trạng thái kiểm chứng tĩnh, unit tests và inference thật được ghi tách trong
[migration and validation](../delivery/migration-and-validation.md).
