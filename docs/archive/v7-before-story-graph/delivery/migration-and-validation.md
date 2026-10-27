# Chuyển tiếp v6 → v7 và phạm vi kiểm chứng

> Historical documentation before chapter-story-graph-v7. See [current docs](../../../v7/README.md).

[← Chỉ mục](../README.md)

## Tổ chức tài liệu

`docs/v7/README.md` là điểm bắt đầu cho pipeline hiện hành. Working tree hiện
giữ thiết kế/lịch sử ở `docs/archive/v6`, điều tra/chính sách ở
`docs/archive/news`, kế hoạch ở `docs/archive/plan` và `docs/archive/plans`.
Di chuyển đường dẫn không làm mất hiệu lực các chính sách đang áp dụng; cập
nhật liên kết và giữ nguyên provenance của tài liệu lịch sử.

Khi cập nhật code cần sửa tài liệu v7 tương ứng, ngày đối chiếu, default/ranges
và trạng thái validation. Tài liệu thử nghiệm/lịch sử phải có nhãn rõ, không
để mô tả Hayai/GLiNER/Gemma/identity legacy thành default mới.

## Mapping tài liệu

| Nội dung v6 | Điểm đọc hiện hành v7 |
|---|---|
| foundation/current-state baseline | foundation/current-state.md |
| foundation/models-and-config | foundation/configuration.md + models-and-runtime.md |
| vision M1–M5 | vision/detection-and-bank.md; v6 giữ chi tiết hình học |
| text OCR/honorific | text/ocr-and-name-scan.md |
| independent-analysis, dialogue M6/M7 | dialogue/independent-analysis.md |
| identity I1–I3 + identity-relationship-v2 | identity/identity-relationship.md |
| translation M8–M10 | translation/natural-and-rendering.md |
| deployment/delivery run guides | deployment/runbook.md + recovery.md |

Giữ liên kết v6 từ báo cáo nghiên cứu hoặc implementation reports để người đọc
thấy quyết định tại đúng thời điểm; không viết lại lịch sử thành v7.

## Tương thích phần mềm

Không có migration hàng loạt manifest. Internal module/schema/CLI tiếp tục tên
v6; `flow_steps` dùng marker `identity-relationship-v2` để chọn DAG mới. Checkpoint
cũ giữ stage identity cũ và đánh số riêng. Không thêm marker vào config snapshot
của run đang tồn tại để “nâng cấp” nó.

Mọi thay đổi source/config bundled phải freeze batch mới. Bộ docs v7 được soạn
trong khi batch inference đã freeze; nó không được đưa ngược vào source.zip của
batch đang chạy. Khi đóng gói batch mới, kiểm tra quy tắc bundle và lưu snapshot
tài liệu phù hợp; đừng sửa hash/archives để đồng nhất giả với working tree.

## Validation identity–relationship đã thực hiện

Implementation report: [báo cáo chi tiết](../../archive/v6/identity-relationship-v2-implementation.md).
Thiết kế: [identity-relationship-v2-design](../../archive/plans/identity-relationship-v2-design.md).

- 22 tests mới cho threshold strictness, minimum target/main member, không
  chaining, bảo vệ named identity, ten-crop packet, update trigger, directed
  addresses, no retry, context packing, fallback, resume, crop tampering và đường
  dịch đọc graph có hướng: pass. Regression cuối chạy build_identity →
  relationship → export → translate với RunStore thật và model giả, kiểm tra
  không lẫn cờ review từ cạnh ngược, freeze graph hash và resume không gọi lại.
- 14 tests relationship/translation tương thích trong
  `tests/test_v6_relationship_translation.py`: pass sau sửa reader.
- Tập rộng103 tests:100 pass,3 fail scene/anchor đã tái hiện ở fixture legacy
  khi không bật hai stage mới; không ghi thành full-suite pass.
- JSON configs và AST integration đã được kiểm tra khi triển khai. Đây là
  bằng chứng tích hợp, không đo độ chính xác quan hệ trên truyện thật.

Ba expectation cũ: scene truncation reserve4096→8192, số scene retry lúc resume,
và quy tắc anchor bất biến không khớp inference-first hiện hành. Không sửa test
để che khác biệt khi chưa chốt thay đổi contract tương ứng.

Trong lượt viết tài liệu đã sửa hai chỗ reader translation/review còn đọc
`a/b/a_to_b/b_to_a` khi nhận graph v2 `from_id/to_id/self/address`. Thay đổi
ở source local cho batch sau; batch đang freeze dừng tại relationship không
được hotpatch và không thực thi đường translation đó.

## Inference thật và cách ghi kết quả

Batch `iris-zero-ch1-identity-relationship-l40s-20261023-r1` thử trên L40S Secure,
reuse năm stage đầu từ backup r15 và chạy lại analyze trước hai stage mới.
Batch đã kết thúc **completed_with_review**:68/68 batch analyze,260 rows,
29 unresolved; nhưng0 named identities,0 profile jobs và0 relationship jobs.
Vì thế **chưa có kết luận chất lượng graph v2**. Backup verified và pod đã
terminate sau thu hồi. Chi tiết ở `RUN-REPORT.md` trong output batch; hướng
bootstrap name ownership đã được triển khai sau lần chạy đó, xem
[báo cáo bootstrap](identity-bootstrap-implementation.md); chưa có inference
thật cho code bootstrap mới, không ép seed tên để giả lập chất lượng graph.
Không dùng trạng thái docs “implemented” thay bằng chứng inference.

Một báo cáo đủ review cần input/model/source/config fingerprints, boundary reuse,
named identities, visual links, profiles/pairs eligible, request counts/status,
evidence/reasons cho từng hướng xưng hô, fallback/unknown/conflicts và backup
proof. Không chỉ đưa graph hình đẹp mà thiếu lý do/evidence.

## Kiểm tra tài liệu

Kiểm tra liên kết local Markdown, paths và tên config/stage với source. Không
chạy model test để chứng minh một thay đổi tài liệu. Không tự mở Runpod mới cho
QA docs. Tách lỗi liên kết có sẵn ở tài liệu lịch sử khỏi lỗi mới do v7 tạo ra.
