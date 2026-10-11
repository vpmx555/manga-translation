# Review tích hợp và chuẩn bị Runpod v6

Ngày: 2026-10-11. Phạm vi được yêu cầu: sửa xung đột tích hợp và tạo file chạy
Runpod cho v6. **Không sửa thuật toán theo từng case; chưa chạy kiểm thử.**
Không sử dụng hạ tầng Runpod trong phiên này.

## Các điểm nối đã sửa

| Điểm xung đột | Thay đổi | Lý do |
|---|---|---|
| Driver hiện có chỉ nhận v5. | Thêm batch_v6.py riêng, giữ batch.py cho workload v5. | Run v6 có identity/typeset và translation artifact khác v5; không ép vào validator cũ. |
| Collector đọc translation revision directory kiểu cũ. | V6 đọc translations inline của utterances/effects và kiểm page/style typeset. | Dùng đúng hợp đồng v6; giữ nhánh v5. |
| V6 chưa xuất đủ các trường metadata style dùng khi bàn giao. | Bổ sung speech_style_evidence/source/status từ kết quả analyze hiện có. | Tương thích export/review; không thêm lượt inference hoặc đổi quyết định semantic. |
| Bundle thiếu requirements Hayai, tài liệu v6 và điểm vào mới. | Sync bao gồm requirements-hayai.txt, docs/v6, AGENTS và runpod-v6.ps1. | Pod có đủ source/dependencies/hướng dẫn của bản đã chuẩn bị. |
| Docker context có thể chứa các .venv-*; inventory image khác sync. | Loại .venv-* và giữ docs/tests để cùng inventory source. | Không COPY venv Windows; việc giữ test source không thực thi test. |
| Hayai Linux chưa khóa CUDA cùng runtime. | requirements-hayai.in ghim Torch CUDA/Transformers; signature gồm cả hai requirements. | Tách OCR khỏi MAGI, tránh reuse environment cũ sau thay dependency. |
| Typeset cần font tiếng Việt trên Linux. | Fast image cài fonts-dejavu-core; preset chỉ định DejaVuSans.ttf. | Cung cấp font thực; glyph/font hashes vẫn được lưu trong artifacts. |
| Input có thể lộ folder đích trước khi extract xong. | Extract vào staging rồi publish bằng rename cùng filesystem. | Job chỉ nhận chapter đã upload đầy đủ. |
| Failed chapter ngăn cứu dữ liệu/tiếp tục phạm vi. | V6 driver ghi lỗi, thử các chapter còn lại; collector tải partial đã ngừng ghi. | Giữ coverage thiếu đúng trạng thái, không sửa case trong phiên vận hành. |
| ZIP/exit 0 chưa chứng minh bản sao đầy đủ. | Inventory filename/bytes/SHA-256 cho outputs/banks/audit; CRC khi extract; receipts riêng. | Phân biệt integrity truyền dữ liệu, integrity artifact và completeness semantic. |
| PID/state cũ có thể báo running dù driver đã chết. | Launch/watch/collect đối chiếu PID start identity và báo stale state. | Không coi trạng thái cũ là inference đang chạy. |
| Preflight chưa ghi CUDA OCR/execution root. | Doctor ghi Hayai CUDA/interpreter và execution_root; v6 batch đối chiếu receipt. | Không dùng receipt của môi trường khác làm bằng chứng chạy GPU. |

Luồng person vẫn tách khỏi MAGI cluster bank. Registry/journal nằm trong
banks/_persons_v6 và được tải cùng gói banks. Không merge/rename cluster_id gốc.
Script thu hồi không tự terminate hoặc tác động tài nguyên hạ tầng.

## Rà soát đã thực hiện

- Parser AST: 82 file Python trong src và deployment/runpod, không lỗi cú pháp.
- Parse tĩnh 4 script Python nhúng: launcher, transfer, watch và đọc state collector.
- Parser PowerShell: runpod-v6.ps1 và runpod.ps1, không lỗi cú pháp.
- Git không có unmerged entries; diff --check không báo lỗi whitespace.
- Preset JSON hợp lệ; CUDA/v6/Hayai, các ngưỡng 0.45/0.25/-0.5 và context 16384
  khớp cấu hình đã chốt.
- Liên kết tài liệu trong docs/v6 dùng đường dẫn tương đối; rà soát tồn tại và
  chữ hoa/thường để clone sang máy khác.

Các phép trên chỉ parse/đọc tĩnh, không import model, thực thi script vận hành,
chạy unittest/pytest, build Docker, download model hoặc truy cập Runpod.
Đây là review code, không phải kết quả kiểm thử runtime hay chất lượng dịch.

## Kết quả trước khi dừng local

Pilot After School chapter 1 trước đó đã hoàn thành layout/extract/geometry/
normalize/scan; analyze bị ngắt. Không có kết quả thực đầy đủ cho identity,
translate và typeset. Hai manifest task-owned đã đổi từ running sang interrupted,
giữ checkpoint và receipts đã hoàn thành.

Receipt hồi quy lịch sử ghi 188 test, không lỗi/failure, 2 skip; bộ v6 14 ca đã
chạy trước yêu cầu dừng. Chúng thuộc source snapshot trước các sửa tích hợp
Runpod hiện tại và không được dùng làm bằng chứng các file mới đã qua test.

## Phần chưa xác minh

Chưa build/push image, resolve lại Linux locks, chạy CUDA/preflight, SSH/upload,
launch, resume, lỗi giữa chapter hoặc download thật với code mới. Các nhánh này
cần phiên kiểm thử riêng do người dùng yêu cầu; agent không tự chạy trên local
hoặc Runpod để sửa case.

Inpaint mặc định false: output typeset là placement preview còn chữ gốc.
Không báo đó là trang dịch sạch. Role/name/speaker, contour accuracy và caller
identity chưa được xác nhận bằng một pilot v6 hoàn tất.

[Hướng dẫn chạy](runpod-execution.md) · [Trạng thái triển khai](implementation-status.md)
· [Tổng quan v6](../README.md) · [Chính sách Runpod](../../news/runpod_agent_fast_policy_vi.md)
