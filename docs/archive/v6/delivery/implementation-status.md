# Triển khai pipeline v6 — trạng thái kiểm chứng

Thiết kế được xác nhận ngày 2026-10-11. Code v6 đã nối vào pipeline mặc định.
Pilot local trên 11 ảnh After School chapter 1 đã dừng theo yêu cầu. Không dùng Runpod.
Chỉ sửa xung đột tích hợp và rà soát tĩnh; không tiếp tục chạy thử/fix case trên local.
File chạy Runpod v6 đã được chuẩn bị, chưa kiểm thử hoặc triển khai.

## Phần đã triển khai

| Mốc | Mã chính | Output/hành vi |
|---|---|---|
| M0/M11 | `scripts/diagnostics/validate_v6.py` | Fixtures và receipt hồi quy; manifest/source/config snapshots. |
| M1 | `visual/koharu.py`, `visual/stages.py` | Một forward toàn trang/crop cho các class; raw candidates, crop provenance và onomatopoeia bbox/score. Bubble 0.45. |
| M2 | `extraction/magi.py`, `extraction/hayai.py` | MAGI detection/associations; Hayai riêng. Local OCR assets được kiểm tra hash/revision trước khi nạp. |
| M3–M5 | `v6/visual.py`, `v6/stages.py` | Mask fusion, group rời, ring/hole kín, observed/inferred masks, body/bridge/tail, panel memberships. Mapping và classifier ảnh/mask có checkpoint riêng. |
| M6 | `v6/normalize.py` | Một physical group → một utterance; giữ raw boxes/OCR/associations/part locations. Chỉ NFC/whitespace. |
| M7/N2/N3 | `v6/semantic.py` | Một contract cho role/style/speaker/names; null/Other nếu không rõ. SFX/action-state tách khỏi image edits. |
| I1–I3 | `v6/persons.py` | Name keys/components/address forms chữ thường; key nhiều ứng viên. Caller support nhiều occurrence ưu tiên dù khác appearance. Person links/redirects/journal riêng; không đổi MAGI bank. |
| M8 | `v6/translation.py` | Dịch toàn utterance theo hai style hiện có; effects giữ context và đi JSON/Markdown. Snapshot glossary/overrides. |
| M9 | `v6/typeset.py` | Erosion body, margin/OCR anchors, font metrics/line capacity; chia từ liên tiếp cân bằng, cùng font size; containment trên glyph mask thực tế. |
| M10 | `v6/translation.py`, `v6/typeset.py` | Shrink đến min font; tối đa 2 shorter attempts; giữ tên/honorific/số/phủ định; lưu cả lượt bị từ chối. Không vừa → needs_review. |
| Runtime | `storage/runs.py`, `pipeline.py`, `cli/main.py` | DAG v6, receipts/resume/file hashes; giữ flow v2–v5; tự tạo review Markdown. |

Inpaint v6 chờ `export` để role đã xác nhận; chỉ xóa dialogue/narration.
Effects và content role unknown được bảo vệ. Đây là thay đổi thứ tự runtime
cần thiết để thực hiện yêu cầu không chỉnh effects lên ảnh.

## Kiểm chứng

- Trước yêu cầu dừng local: receipt ghi 188 test, không lỗi/failure, 2 skip.
  Bộ v6 14 ca cũng đã chạy trước đó. Các receipt này thuộc snapshot source cũ,
  không chứng minh các thay đổi tích hợp Runpod mới đã được kiểm thử.
  Receipt lịch sử: `outputs/diagnostics/v6-regression.json`.
- Phiên chuẩn bị Runpod hiện tại chỉ rà soát tĩnh; không chạy test, model, Docker
  build/push hoặc thao tác hạ tầng. Chi tiết trong [review chuẩn bị](runpod-preparation-review.md).
- Fixtures gồm bridge ghép, xuyên panel, bubble rời, ring/hole, visual cache độc
  lập OCR và nhạy với ảnh, caller support qua page-local clusters, replay dedup,
  name/honorific, surname collision, mâu thuẫn liên kết gián tiếp, bank hash,
  journal/revert, glyph containment/balance và bảo vệ effects khi inpaint.
- Pilot đã dừng: Koharu và MAGI/Hayai extraction hoàn thành 11/11 trang;
  geometry/normalize/scan hoàn thành. Analyze bị ngắt, identity/translation/typeset
  thực chưa có kết quả cuối. Hai manifest của các attempt đã ghi `interrupted`.
- Assets OCR đã tải vào `models/ocr/<model>/<revision>`, kiểm tra hash/revision.
  Receipt: `outputs/diagnostics/v6-ocr-assets.json`.

## Vận hành tiếp theo

Không chạy test dài CPU hoặc pilot/fix case trên local. Lệnh Runpod, input/output,
chuẩn bị image trước khi thuê GPU, preflight và backup nằm trong
[hướng dẫn Runpod v6](runpod-execution.md). Việc tạo các file này không cấp quyền chạy.

Tự chọn `.venv-layout-local` và `.venv-hayai-local` khi có; `layout.python`,
`ocr.python`, `MANGA_HAYAI_PYTHON` vẫn có ưu tiên. Môi trường Runpod được controller
chỉ định riêng và không dùng các venv Windows.

`person-revert --run-dir '<run>' --operation '<operation id>'` chỉ đảo operation
person mới nhất chưa thay đổi; không sửa MAGI bank. Run artifacts đã commit là
snapshot lịch sử; tạo revision mới để analyze lại sau revert.

## Giới hạn

- `inpaint.enabled=false` mặc định: typeset xuất
  `placement_preview_original_text_present`, còn OCR gốc. Đây là preview dàn
  chữ, chưa phải trang dịch sạch. AOT local chưa được chuẩn bị/kiểm chứng.
- Role/speaker/name và giữ nghĩa khi shortening vẫn phụ thuộc Ollama. Checks
  bằng code không thay thế review nghĩa ở pilot.
- Repair ghi rõ vùng suy ra; hints thiếu chứng cứ phải unknown. Fixture qua
  không chứng minh contour/role accuracy tăng trên toàn chapter.

[Tổng quan v6](../README.md) · [Kế hoạch triển khai](implementation-plan.md)
