# Bootstrap tên v7 — triển khai và kiểm chứng

Ngày2026-10-23. Đã triển khai theo25 quyết định được xác nhận trong
[thiết kế](../../archive/plans/identity-bootstrap-v7-design.md). Kiểm chứng hiện
tại dùng model giả và ảnh/vector tổng hợp; chưa đánh giá chất lượng inference thật.

## Kết quả

Run mới có thể đi từ person registry chưa có tên đến named identities, profile,
directed graph và translation mà không seed sẵn tên. `build_identity` đọc name
candidates từ scan và mention_type từ analyze, dùng panel/crop bank để xác nhận
từng occurrence. Empty candidate ownership và confirmed_person_name=false không
loại tên khỏi bước bootstrap.

Policy `chapter-multimodal-name-binding-v1` được đóng băng ở
`v6.identity_name_binding_policy`. Manifest lịch sử thiếu/null marker giữ luồng
identity v2 cũ. Cả bốn config main bật policy mới; concurrency8, output reserve4096.

## Source chính

- `v6/identity_binding_evidence.py`: occurrence/context toàn chapter, panel-MAGI
  candidates, off-panel listener exception, retained labelled panels và crop bank.
- `v6/identity_binding.py`: schema/grounding validation, token preflight, child
  planning, one-pass inference, code reconciliation, name-group history và
  effective semantic projection.
- `v6/identity_v2.py`: bootstrap trước name/visual linking và profile; mới xác
  nhận tên có thể làm frozen visual anchor ngay cùng run.
- `v6/semantic.py`, `identity_profiles.py`, `relationship_v2.py`: effective
  unknown fills, original/provenance, named descriptions và supplementary pairs.
- `v6/identity_runtime.py`: kiểm tra retained images cả khi reuse decision trước
  stage commit; mất kết nối được giữ là uncertain, không resend.
- `storage/runs.py`, `deployment/runpod/v6_contract.py`, `preanalyze_import.py`:
  dependencies, crop/snapshot lineage và chặn import sau analyze qua contract khác.
- Stage/root Markdown review: binding counts, MAGI labels, nguyên câu và reasons.

Các path source ở trên tương đối với `src/manga_pipeline` trừ deployment.

## Request vận hành

Một group/request, chia theo occurrence khi input/output/image budget cần thiết,
trước khi gửi POST. Mỗi child giữ whole evidence, candidate snapshot và IDs/hash
ổn định. Không đọc kết quả child trước, không LLM reduce; mọi group/child dùng
chung queue tối đa8, chịu runtime slots. Single occurrence không vừa budget được
ghi failure trước POST. Không thêm retry OOM/schema/truncation/uncertain.

Successful decisions được giữ khi child khác lỗi. Một suspect alias result hợp
lệ treo cả group. Existing confirmed name ownership/cannot-links tiếp tục được
bảo vệ, thay vì chọn response đến trước. Group history lưu trong collection
`name_groups_v1` của person registry để commit atomic cùng identity links, với
snapshot riêng `snapshots/name-groups-v1.json` trong run.

## Artifacts

`07_build_identity/name-binding/` chứa frozen plan, packed request/evidence,
retained reference images, requests, durable decisions, request-summary và
result. Stage result chứa binding_snapshot_hash và review_status. Export giữ
hash đó, analyze_original và identity_binding provenance; chỉ unknown fields
được điền, không ghi đè resolved analyze facts.

RunStore vẫn dùng status completed cho stage commit để giữ DAG compatibility,
đồng thời lưu review_status. Batch summary chuyển completed_with_review khi có
identity review items, kể cả analyze không còn unresolved. Tên thiếu type,
người thiếu bank ID và nhóm suspect có lý do skip cụ thể.

## Kiểm tra

`tests/test_identity_name_binding.py` dùng RunStore thật, crop/vector/ảnh tổng
hợp và fake provider. Các case bao gồm empty registry → profiles/graph/export/
natural translation; narrator introduction; individual off-panel listener;
third-person/group listener rejection; unknown type/no bank; alias suspect;
preflight splitting, oversized occurrence không POST, partial success, no retries,
uncertain/never-sent resume, known owner protection, multiple forms, original
semantics, crop/plan tampering và legacy/import compatibility.

Một lượt tổng hợp chạy **84 tests, tất cả pass** trong38.794 giây:

| Suite | Tests |
|---|---:|
| `test_identity_name_binding` | 24 |
| `test_identity_relationship_v2` | 22 |
| `test_v6_relationship_translation` | 14 |
| `test_v6_semantic_contract` | 6 |
| `test_v6_pipeline` | 15 |
| `test_runpod_native_identity` | 3 |

Đã parse AST17 file Python liên quan, load/validate4 config main và tạo thử
source bundle340 files trong thư mục tạm; bundle chứa code binding mới.14 guide
v7 có liên kết nội bộ hợp lệ sau khi cập nhật đường dẫn archive trong working
tree. Không dùng các kiểm tra này để tuyên bố độ chính xác tên/graph trên Iris
Zero hoặc báo toàn bộ test suite của repository đã pass.

## Bước thử model tiếp theo

Cần một batch mới với source/config đã thay đổi. Import upstream qua scan nếu
tương thích; không reuse committed build_identity/profile/graph cũ để giả chạy
bootstrap mới. Inference Runpod cần yêu cầu trực tiếp cho tác vụ đó; mặc định
L40S Secure và teardown sau verified backup. Báo cáo cần binding job counts,
accepted names, profiles/pairs, skip/failure reasons và evidence để người dùng
kiểm tra, đặc biệt các narrator-introduction và off-panel cases.
