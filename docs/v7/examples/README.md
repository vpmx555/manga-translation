# Ví dụ và visual review

## Nguồn của các hình và JSON

| Thư mục | Nội dung | Loại ví dụ |
|---|---|---|
| [layout](layout/manifest.json) | Trang 4, input/overlay/zoom panel/bubble/mask và reading order | Artifact thật được tái sử dụng trong graph r4. |
| [extract](extract/manifest.json) | Trang 12 làm ví dụ chính: panel, text/OCR, character/cluster, hai bảng association; trang 8/11 cho trường hợp thiếu dữ kiện; trang 12–14 đối chiếu bank; crop/prototype/vector bank thật | Result và bank local của batch graph r4; giữ nguyên dự đoán lịch sử, không chạy lại model. |
| [upstream](upstream/manifest.json) | Trang 11, extract, geometry group/mask và utterance normalize | Artifact thật từ cùng run r4. |
| [analyze](analyze/manifest.json) | Event `scene:30:event:1`, trang 36, ảnh gửi model và quyết định | Checkpoint event thật của r6; không chứng minh toàn chapter đã đúng. |
| [inpaint](inpaint/manifest.json) | Trang 11 trước/sau xóa chữ, white/AOT mask | Artifact lịch sử năm 2026, run `20261022T160100Z-07381866`. |
| [flow](flow/01_layout.svg) | Sơ đồ input/xử lý/output cho đủ 13 stage | Sơ đồ giải thích code, không phải model output trên một trang cụ thể. |

Các JSON minh họa schema trong stage 7–13 được đánh dấu ngay cạnh đoạn trích. File manifest ghi nguồn và checksum cho các asset thật; không dùng ví dụ lịch sử để khẳng định luồng graph/reanalyze mới đã chạy tới dịch/typeset.

## Dựng lại bằng code

Chạy từ gốc repo. Các script chỉ đọc artifact đã có và tạo tài liệu local, không gọi model:

- [build_v7_layout_examples.py](../../../scripts/build_v7_layout_examples.py): input/overlay, zoom từng phần, reading order và page JSON.
- [build_v7_extract_examples.py](../../../scripts/build_v7_extract_examples.py): tách các lớp output MAGIv2, crop/cluster, liên kết text–character/text–tail, OCR thiếu dữ kiện, ID bank qua trang và toàn bộ prototype của ID 2/5; nhận thêm `--bank-dir` để đọc metadata/vector/crop đã lưu.
- [build_v7_upstream_examples.py](../../../scripts/build_v7_upstream_examples.py): crop nhân vật, text/group/mask và utterance.
- [build_v7_analysis_examples.py](../../../scripts/build_v7_analysis_examples.py): một event checkpoint đã hoàn tất và ảnh theo checksum.
- [build_v7_inpaint_examples.py](../../../scripts/build_v7_inpaint_examples.py): sao chép trang inpaint đã có, giữ provenance lịch sử.
- [build_v7_stage_diagrams.py](../../../scripts/build_v7_stage_diagrams.py): `python -B scripts/build_v7_stage_diagrams.py`, dựng 13 SVG.

Các script lấy artifact có `--run-dir`, `--output`; script cần ảnh gốc có thêm `--source-folder`, và `--page` chọn trang. Chạy `--help` để xem tham số. Giữ nguyên đường dẫn nguồn hoặc thay bằng bản sao có đúng checksum.

Script extract dùng cố định bộ trang 8/11/12/13/14 để cover các nhóm output, không có `--page`. Bộ ảnh được dùng trong hai docs [2.1 — MAGIv2 extract](../stages/02,1_extract_magiv_extract.md) và [2.2 — extract bank](../stages/02,2_extract_bank.md). Mỗi doc bắt đầu từ input/output, sau đó giải thích từng phần, cuối cùng mới có sơ đồ tổng hợp và kết luận. Bank trong ví dụ là trạng thái đã lưu, không giả lập bank trước extract hay candidate distance của từng assignment. Các ảnh `extract-overview.png`/`character-zoom.png` cũ ở upstream giữ nguyên để bảo toàn nguồn ví dụ geometry/normalize.

## Visual graph tương tác

- [Kết quả reanalyze Iris Zero và lệnh mở review từ backup đã xác minh](iris-zero-reanalyze-review.md).

- [Iris Zero chapter 1: dựng lại visual graph local](iris-zero-graph-review.md).

Các ảnh bên dưới là ví dụ layout/extraction/geometry từ các lượt chạy trước; chúng minh họa các stage upstream còn được dùng.

- [Input](input.webp)
- [Layout](layout.png)
- [Extract](extract.png)
- [Geometry](geometry.png)

Graph/analyze/reanalyze tự tạo review.html và ảnh event từ code khi chạy, xem [operations](../operations.md).
