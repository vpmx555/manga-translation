# Iris Zero chapter 1 — plan tiếp tục với RTX 5090 Secure

Người dùng chọn **Secure**, đưa lựa chọn vào plan và sẽ yêu cầu thực hiện lại sau.
Trạng thái hiện tại: **chỉ chuẩn bị offline; không provisioning, không chờ stock
liên tục và không inference**. Plan này không cấp quyền dùng Runpod ở phiên sau.

## Phạm vi đã chốt

- Toàn bộ 44 ảnh trong `D:\download\EN-data\Iris Zero\EN\Chapter 1`,
  10.178.430 bytes tại kiểm kê; chạy v6 đến hết `analyze` và các stage phụ thuộc.
- Một **RTX 5090, Secure Cloud, 32 GB VRAM**. Không tự dùng Community/GPU khác.
  Nếu Secure hết capacity, báo chưa chạy và giữ plan, không tạo tài nguyên khác.
- vLLM 0.31.0; `cyankiwi/Qwen3.5-27B-AWQ-4bit` revision
  `5cba9b5f1bca69fab9b75c88ae7817ae22d7d898`; compressed-tensors 4-bit,
  BF16 compute, context **32768**, thinking tắt, output reserve 2048.
- Một story/LLM slot; analyze batch tối đa 3, có token admission/split; giữ
  chính sách timeout/pause và không CPU offload. Không chạy translate/typeset.
- [Profile inference](../../configs/pipeline-v6-runpod.json) và
  [body hạ tầng Secure](../../deployment/runpod/profiles/qwen35-27b-5090-secure.json).
  Body hạ tầng là cấu hình đã chuẩn bị, không phải một Pod đã được tạo.

## Phương pháp bắt buộc

Theo [runbook hiện hành](runpod-v6-agent-operational-plan.md):
`runpod-v6.ps1` → `local_v6.py`, local working-set + Syncthing native.
Source/env/runtime/model/input/output/log ở `/opt/manga-work`; durable archive
ở `/workspace/manga-runtime/local-v6`, được replicate về PC. Không mặc định
build/push image lớn hoặc chuyển về launcher workspace cũ.

Boot image trong profile đã được registry xác minh ở lượt trước; trước lần tạo
Pod mới phải kiểm tra lại. Giữ image digest và source hash riêng. CUDA floor
13.0 theo vLLM/Torch đã khóa; đọc capacity host tương thích trước create.
Container disk 150 GB, persistent 100 GB; SSH 22/tcp và Syncthing 22000/tcp.

Giá GPU Secure quan sát gần nhất là **1,19 USD/giờ**, chưa gồm storage; đây không
phải giá được giữ chỗ. Lượt kiểm tra cuối ngày 16/10 giờ Việt Nam báo Secure hết
stock. Đọc lại giá/stock/schema khi người dùng yêu cầu thực thi lại.

## Artifact offline và thao tác khi tiếp tục

Batch chuẩn bị cuối: `iris-zero-ch1-vllm-5090-secure-20261016`, dưới
`.runpod/v6/iris-zero-ch1-vllm-5090-secure-20261016/`. Gồm `plan.json`,
`pipeline-v6.json`, `source.zip`, `locks.zip`, ZIP input và
`infrastructure-plan.json` ghi Secure/paused. Chưa tạo pairing/daemon cho batch
này. Các batch 4090 và 5090 `-r2` trước đó là lịch sử/superseded, không launch.

Khi có yêu cầu trực tiếp dùng Runpod cho task này:

1. Đọc AGENTS, policy và runbook; đối chiếu input/config/source của plan bằng
   `v6.prepared`. Nếu source/config/input đã đổi, prepare batch ID mới trước thuê;
   không sửa frozen plan và không chạy lại `prepare` vào folder đã tồn tại.
2. Chuẩn bị pairing offline, trước create:

   ```powershell
   $batch = 'iris-zero-ch1-vllm-5090-secure-20261016'
   $plan = ".runpod/v6/$batch/plan.json"
   python deployment/runpod/native_sync.py prepare --plan $plan
   ```

   Helper chọn port trống và xác nhận đúng native device; watcher xác nhận bằng
   launch token/PID thực. Không ghi đè binary Syncthing đang chạy.
3. Đọc lại RTX 5090 **Secure** capacity/CUDA/giá, báo giá trước create. Dùng body
   Secure đã lưu, thay `name` bằng batch thực nếu phải prepare mới. Create một Pod,
   read-back đúng GPU/cloud/image/disk/ports; không khởi động Pod cũ của tài khoản.
4. Ghi connection đúng Pod với cả direct SSH và Syncthing TCP mapping. Kiểm local
   mount/free space, cuInit và GPU. Một lệnh launch:

   ```powershell
   .\runpod-v6.ps1 launch --plan $plan --pod-id '<TASK_OWNED_POD_ID>'
   $boundPlan = ".runpod/v6/$batch/bound-plan.json"
   .\runpod-v6.ps1 watch --plan $boundPlan
   ```

5. Bootstrap dùng bốn frozen locks, tạo venv vLLM bằng `--allow-existing` để giữ
   lock, prefetch đúng config, doctor inference thật và geometry parity; chỉ
   chạy chapter sau khi đạt. 5090/32K **chưa có kết quả GPU thực** tại lúc lưu plan.
6. Theo dõi counter/checkpoint/bytes; không đọc review Markdown làm approval gate.
   Giữ mọi partial/failure; không sửa code/prompt/config giữa workload. Chẩn đoán
   giới hạn sau 10 phút không tiến triển.
7. Xác minh bản sao output/bank/audit, terminal writers và SHA/CRC/inventory;
   sau đó terminate đúng Pod của lượt test đã được cho phép và read-back. Nếu
   bootstrap thất bại trước khi có pipeline state, vẫn cứu log/config/locks/source
   và ghi receipt failure riêng, không đánh dấu chapter completed.

## Bằng chứng và sửa quy trình đã có

Lượt 4090 `xkn8m5azj0zxyk` thất bại ở khởi tạo KV: weights 18,8 GiB; KV cần
2,15 GiB cho 32K, chỉ có 1,08 GiB; chưa analyze trang nào. Backup 308 file đã
xác minh SHA/CRC/inventory tại
`outputs/runpod-iris-zero-ch1-vllm-local-20261016/failure-verification.json`.
Delete trả 204 và read-back 404; Pod này đã kết thúc. Không có Pod 5090 được tạo.

Wrapper mặc định, root AGENTS và tài liệu ưu tiên đã dẫn về native/local workflow;
tài liệu launcher/Ollama cũ được gắn nhãn lịch sử. Đã sửa giữ vLLM frozen lock
khi tạo venv, validation GPU/preset 5090, port/device guard và startup token cho
Windows redirector. 21 regression nhỏ liên quan đạt; không phải bằng chứng
analyze hoặc inference 5090 đã thành công.
