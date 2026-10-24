# Kế hoạch kiểm chứng riêng — Qwen 3.5 27B, vLLM, RTX 4090

Trạng thái: **đã chuẩn bị, chưa thực thi và chưa có quyền chạy Runpod**. Ngày: 2026-10-15.

Plan này đi cùng [thiết kế đã triển khai](qwen35-vllm-4090-implementation-vi.md). Không dùng yêu cầu sửa mã làm quyền mở/kết nối/upload/chạy hoặc thay đổi tài nguyên Runpod. Trước phiên thực thi phải đọc toàn bộ [chính sách dự án](../news/runpod_agent_fast_policy_vi.md).

## 1. Phạm vi và dữ liệu

- Một GPU RTX 4090; engine/model/revision và config 32K đúng bản đã chốt.
- Chọn chapter từ nguồn local `D:\download\EN-data\...`, ưu tiên đúng ba truyện của lần chạy cũ nếu có log/plan xác định được. Không tự chọn một bản dịch hoặc dedup ảnh khác.
- Cần một chapter điển hình làm mốc 30 phút, một chapter nhiều thoại/nhân vật và một chapter nhiều caption. Ghi story/chapter, số trang, targets/scenes nếu đã có artifact, bytes/hash và thứ tự vào manifest mới trước khi thuê GPU.
- ID/chapter cụ thể chưa được đóng băng vì người dùng chưa yêu cầu chạy một corpus trong task sửa mã. Việc chọn corpus không được âm thầm tạo workload trả phí.

## 2. Chuẩn bị trên CPU, trước khi thuê GPU

1. Rà soát config và source bundle; chuẩn bị image/env riêng vLLM 0.31.0. Lock dependencies có hashes, lưu provenance Python/CUDA image và lock.
2. Khi được cho phép thực hiện kiểm tra, chạy regressions model-free: `tests/test_vllm_inference_policy.py` cùng các test liên quan provider/runtime/dual branch/ingress. Không chạy toàn bộ suite nặng theo mặc định.
3. Đọc log/journal lưu sẵn của lần chạy gần ba giờ nếu có để xác định số request, queue, context và lỗi thật; không cần chạy lại model để thu bằng chứng đó.
4. Chuẩn bị input/archive/checksum, output/backup destination mới, ngân sách và quyền teardown đúng tài nguyên task. Không build Docker trên GPU thuê.
5. Prefetch checkpoint đã pin vào storage đã chọn; kiểm tra tổng dung lượng env + model + input/output + archive/headroom. Checkpoint Safetensors khoảng 20 GB không phải nhu cầu VRAM đỉnh.

Môi trường vLLM độc lập với MAGI/Koharu/Hayai. Không nâng torch/transformers trong env MAGI để cài vLLM.

## 3. Một lượt preflight có giới hạn trên GPU đã được duyệt

Gộp kiểm tra GPU đúng loại, source/config hash, env/lock, dung lượng, input ingress và service/model vào cùng lượt. Kiểm tra CUDA thật của env vLLM bằng startup và một structured inference, không chỉ import torch ở env khác.

Preflight tích hợp trong code:

- Chỉ service loopback do controller quản lý, đúng revision và context 32.768.
- `cpu_offload_gb=0`, `max_num_seqs=1`, không fallback.
- Một request analyze synthetic dài có schema thật và ba targets, thinking tắt. Không tái sử dụng cache preflight cũ để báo inference mới.
- Token count từ `/tokenize`, output có đủ IDs và không bị cắt.
- Ghi effective config, model revision, service signature và source bundle vào receipt; dừng service sau probe để nhường visual.

Nếu không nạp được hoặc không đủ cache cho 32K, dừng và báo không đáp ứng. Không tự đổi context, dtype/quantization, model, GPU hay offload. Không khởi chạy batch dài để “xem có chạy được không”.

## 4. Pilot chapter và các số đo bắt buộc

Sau preflight, chạy một chapter điển hình đã nằm trong plan được duyệt, rồi mới đánh giá có tiếp tục corpus hay không theo phạm vi quyền thực thi của phiên đó.

| Nhóm | Số đo |
| --- | --- |
| Hoàn tất | Thời gian tới chapter đủ artifact/coverage; thiếu gì phải ghi rõ |
| GPU/runtime | VRAM đỉnh, lỗi allocation/startup, xác nhận không offload |
| Context | Token input thực, output reserve/usage, số nhóm giảm batch, số singleton không vừa |
| Chất lượng | Speaker/addressee, continuity caption, đủ hai kiểu dịch, review flags và mức giảm bằng chứng |
| Lời gọi | Số request scene/xuôi/ngược/dịch/shorten; retry và repair theo target |
| Chi phí thời gian | Ingress, visual, service load/unload, queue, đọc prompt/sinh token nếu có metric, review/validation, packaging/download |
| Tái lập | Model/revision, vLLM version, dependency locks, config/source hash, run IDs |

API chat chuẩn cung cấp token usage và tổng thời gian client; không tự suy ra prefill/decode time riêng nếu không có metric tương ứng. Telemetry `llm_service_start/stop` dùng để định lượng thời gian chuyển stage.

Mục tiêu 30 phút là thời gian tới chapter hoàn chỉnh đầu tiên theo mốc bắt đầu đã ghi, không phải cam kết trước đo. Ghi riêng thời gian khởi tạo toàn phiên và thời gian chapter để so sánh công bằng.

## 5. Điều kiện tiếp tục và thất bại

- Chỉ tiếp tục khi thực tế đáp ứng 4090/32K/không offload, không có lỗi provider/context bị che và coverage được báo đúng.
- Output thiếu hoặc JSON bị cắt không được coi là thành công. Repair phải hữu hạn; không chạy vòng thử vô hạn để làm đẹp coverage.
- Context bị rút phải có dấu vết và validator chỉ cho phép bằng chứng thực được cung cấp. Không sửa source hoặc prompt giữa workload để chữa từng case.
- Generation timeout không rõ trạng thái: giữ queue paused, không chuyển chapter khác dùng GPU và không tự restart service.
- Chỉ operator phục hồi sau khi xác nhận server/process group cũ đã kết thúc; retry nguyên request tối đa một lần. Controller `recover` không tự giết server còn sống.

Nếu cần điều chỉnh sau kết quả pilot, ghi đề xuất và tạo revision/plan mới sau quyết định phù hợp, không hot-sync dưới process đang chạy.

## 6. Theo dõi, backup và kết thúc phiên tương lai

Theo chính sách dự án: báo tiến độ mỗi hai phút, chẩn đoán một lượt sau mười phút không có tiến độ đo được; không dùng heartbeat/PID sống làm bằng chứng tiến độ. Không giữ GPU thuê chờ viết tài liệu hoặc công việc offline.

Giữ toàn bộ output, partial/failed, bank, logs/audit, visualization và Markdown review tự sinh. Không đọc review Markdown như cổng phê duyệt stage. Thu hồi từng chapter đã ngừng ghi vào đích mới, xác minh bản sao và báo coverage thiếu trung thực.

Chỉ terminate tài nguyên task được người dùng cho phép sau khi backup ngoài pod được xác minh. Dừng vLLM không dừng phí pod.

## 7. Kiểm tra đã thực hiện trong lượt sửa mã

- Parse AST 19 file Python đã sửa/thêm: không phát hiện lỗi cú pháp. Quét bảng symbol tĩnh không phát hiện tên global chưa được định nghĩa trong các file này; đây không phải kiểm chứng hành vi runtime.
- Parse JSON cấu hình Runpod thành công; không có liên kết local hỏng trong bốn tài liệu thiết kế/review/plan liên quan.
- `git diff --check`: không phát hiện lỗi whitespace.
- Các test model-free đã được chuẩn bị, **chưa chạy**.
- Chưa cài dependencies/build image/tải model/chạy inference/benchmark hoặc thao tác Runpod. Chưa có kết luận về VRAM, CUDA hay latency.

Các kết quả trên chỉ xác nhận tính hợp lệ tĩnh. Không có test pass, pilot hay benchmark nào được suy ra từ chúng.
