# Iris Zero chapter 1 — yêu cầu chạy lại analyze, 21/10/2026

## Yêu cầu đang có hiệu lực

Người dùng trực tiếp cho phép kết nối Runpod, tái sử dụng kết quả các bước trước
analyze và chạy analyze lại từ đầu. Sau khi kiểm kê thấy đường dẫn ban đầu chỉ có
khung Syncthing, người dùng chọn **32k-parallel-r2** làm nguồn.

Nguồn đã chọn:
`outputs/runpod-iris-zero-ch1-vllm-5090-32k-parallel-r2-20261016`.

Giữ phạm vi Iris Zero chapter 1, 44 trang, đến hết analyze theo batch nguồn.
Không nhập cache response, journal, scene/batch checkpoint hay kết quả analyze cũ
vào run mới. Giữ backup nguồn bất biến.

## Kết quả kiểm tra lúc 2026-10-21 12:20 UTC

- Runpod MCP `list_pods`: chỉ có pod `hiwkxkyqychc2j`, trạng thái `EXITED`,
  RTX 4090. Đây là tài nguyên ngoài task hiện tại; không thay đổi nó.
- `get_gpu_type` cho `NVIDIA GeForce RTX 5090`, cloud `SECURE`, product `POD`,
  `minCudaVersion=13.0`: `availability=NONE`; CUDA 13.0 và 13.2 đều
  `available=false`. Giá Secure trả về là **1.19 USD/giờ**, chưa gồm storage.
  Không tạo pod, không chuyển Community/GPU khác, không polling liên tục.
- Connection cũ trỏ pod `uw3cmx25lvudbu`, không có trong kết quả list hiện tại;
  chưa SSH hoặc sử dụng connection này.
- Đường dẫn người dùng nêu đầu tiên
  `outputs/runpod-iris-zero-ch1-vllm-5090-20261016-r2` có 7 file nhỏ, tổng
  292 bytes ngoài local-reviews. `live` và `durable` chỉ có `.stignore`, không
  có checkpoint hoặc publication để resume.
- Không có pod hoặc inference mới được tạo/chạy trong lượt này.

## Dữ liệu có thể dùng từ nguồn đã chọn

Run mới nhất trong backup: `20261016T063724Z-fe0c92a5`.
Manifest nằm tại
`live/outputs/iris-zero/chapter-001/20261016T063724Z-fe0c92a5/manifest.json`.

Manifest ghi layout, extract, bubble_geometry, normalize, scan là `completed`,
`failures=0`. Analyze là `partial`, 3 failure; không tái dùng bước này.
State ghi chapter `paused`, writer đã dừng, exit code 2, do request completion
không chắc chắn trong analyze cũ. Đây không phải kết quả analyze thành công.

Publication archive:
`durable/iris-zero-ch1-vllm-5090-32k-parallel-r2-20261016/published/outputs/iris-zero/chapter-001/82f06f4db0daedce8f77fb0bd57b6e9ebc2c8e1d5c6e5a9933a73e469d5e9c64.zip`

- SHA-256 đã tính lại và khớp receipt:
  `82f06f4db0daedce8f77fb0bd57b6e9ebc2c8e1d5c6e5a9933a73e469d5e9c64`.
- Size đã đối chiếu: 406191228 bytes.
- Receipt kê 7827 file, 502626131 bytes giải nén. Chưa xác minh lại CRC/per-file
  inventory hoặc bank trong lượt này; hash archive không thay thế các gate đó.
- Đã tính SHA-256 ảnh tại `D:\download\EN-data\Iris Zero\EN\Chapter 1`:
  44/44 khớp manifest, không thiếu hoặc lệch ảnh.

## Việc cần làm trước khi thuê

1. Đọc policy/runbook hiện hành và báo cáo refactor
   `docs/plan/v6-only-parallel-analyze-refactor-implementation-20261021.md`.
2. Xác minh publication output/bank, CRC và inventory terminal backup qua lane
   native hiện hành. Không đọc review Markdown làm approval gate.
3. Hoàn tất đường nhập checkpoint trước analyze vào **run contract mới**.
   Source backup chưa có `analysis_contract`, config còn `ollama` và selector cũ;
   `RunStore` mới từ chối trực tiếp source này. Không sửa manifest cũ để lách guard.
   `reanalyze_run` hiện chỉ copy layout/extract và có thể copy response cache nếu
   config trùng; chưa đáp ứng đầy đủ yêu cầu tái sử dụng các bước trước analyze
   và analyze sạch. Phải kiểm tra/chuẩn bị import tương thích trước thuê,
   không hotpatch khi GPU đang bill.
4. Freeze source/config/input/locks trong batch ID mới; không launch plan ngày
   16/10. Xác minh bốn frozen locks, pinned boot image/CUDA 13.0, native pairing
   riêng. Chưa tạo frozen batch mới trong lượt kiểm tra này.
5. Dùng `runpod-v6.ps1` -> `local_v6.py`, local working set `/opt/manga-work`,
   durable `/workspace/manga-runtime/local-v6`, native Syncthing.
6. Chỉ khi Secure 5090 có capacity, báo giá từ live read, tạo đúng một pod và
   read-back. Không dùng Community hoặc GPU khác. Profile hiện hành có 8 LLM
   slots nên qualification concurrency thực phải đạt trước chapter inference.
7. Chạy analyze mới từ đầu, monitor counter/receipts; lưu toàn bộ partial/failure,
   xác minh backup PC rồi teardown đúng pod do task này tạo theo policy.

Trạng thái bàn giao: **chưa chạy vì Secure 5090 hết capacity; đã xác định đúng
backup theo lựa chọn người dùng, còn gate import/prepare offline trước launch**.
