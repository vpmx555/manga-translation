# Analyze độc lập và merge inference-first

> Historical documentation before chapter-story-graph-v7. See [current docs](../../../v7/README.md).

[← Chỉ mục](../README.md)

## Từ scene đến batch

Scan tạo scene và name evidence. Planner tách các nhánh caption/dialogue trong
scene, xây request có target, source anchor, original speaker, candidates và
context. `v6.analyze_batch_size=8` là mốc chia **mềm**: planner có thể chờ một
anchor để cắt và phải kiểm tra multimodal token budget; không phải cam kết mọi
request đều đúng tám câu.

Cắt cứng vì context không được bỏ target. Mỗi batch có ID và plan đóng băng;
batch sau không đọc kết quả inference của batch trước. Source anchor giúp chọn
ranh giới/context nhưng `source_anchors_enforced=false`: nó không khóa kết quả
speaker bất biến.

`caption_max_distance=7` giới hạn evidence/context theo chính sách caption;
không phải ngưỡng embedding, số trang bank nhớ hay số người trong graph.

## Analyze quyết định gì

Model phân loại nội dung, speaker, addressee, name mentions và evidence cần cho
semantic resolution. Một target giữ source utterance và vị trí; không cho phép
model tùy ý tạo/xóa ID để làm response dễ trả hơn.

Kết quả được kiểm tra schema, target coverage, references và điều kiện liên kết
person/name. Các giá trị unknown và semantic needs-review là đầu ra có ý nghĩa,
không được đổi thành tên đoán chỉ để đạt coverage đẹp.

Đường analyze hiện tại **chỉ gửi text và metadata**, không gửi ảnh nhân vật;
`batch_contract.PROMPT` yêu cầu `visual_support=unknown`. Ảnh/crop trong các
profile/relationship request là đường khác. Token budget qua provider đa phương
thức không đồng nghĩa mỗi stage đều truyền ảnh.

## Merge sau inference

`speaker_merge_policy=inference-first-v1` ưu tiên suy luận hợp lệ sau analyze,
kết hợp chứng cứ liên quan và giữ conflict/provenance để review. Association
MAGI, tail hint và source anchor là evidence; chúng không tự động thắng một
kết luận ngữ nghĩa hợp lệ.

Merge không có nghĩa bỏ qua validator hoặc sửa response thô để hợp thức hóa.
Thông tin mâu thuẫn, thiếu phân giải và request lỗi được ghi vào artifact. Stage
có thể tiếp tục bằng đường fallback/review mà không dừng toàn chapter vì một
trường hợp semantic chưa rõ. Lỗi integrity nguồn/artifact vẫn phải dừng.

## Concurrency và vận hành

Analyze dùng scheduler/admission chung và frozen plan, chịu trần
`runtime.llm_slots` và budget. Chạy nhiều batch không tăng số target trong một
request. Muốn giảm áp lực VRAM trước một run mới, giảm slots và freeze batch
mới; không thay file config của run đang chạy.

Request receipts, raw responses và kết quả target được giữ để resume/audit.
Quy tắc retry của analyze phụ thuộc đường request và scheduler riêng; không suy
ra rằng nó giống profile/relationship vốn cấm retry. Khi gặp uncertain POST,
không coi việc mất kết nối là bằng chứng request chưa từng được xử lý.

## Điều kiện để hai stage tiếp theo có công việc

Scan thấy tên không đồng nghĩa analyze đã xác nhận người mang tên. Run mới dùng
bootstrap trong build_identity để xác nhận owner bằng panel/crop và text toàn
chapter. `confirmed_person_name=false` không chặn bootstrap; mention_type
thiếu/unknown vẫn bị bỏ qua. Profile cần named identity đủ điều kiện; relationship
nhận cả resolved pairs từ analyze và direct-call evidence được bootstrap xác nhận.

Run v2 lịch sử không có marker bootstrap vẫn có thể completed với zero inference
jobs khi analyze không tạo named identity. Run mới phải báo riêng binding jobs,
số tên xác nhận, profile/pair eligibility và lý do skip. Zero identities khi có
tên cần xử lý được đánh dấu completed_with_review.

Nguồn mã: `v6/independent.py`, `planning.py`, `batch_contract.py`, `merge.py`,
`anchors.py`, `scheduling.py`, `requests.py`.
