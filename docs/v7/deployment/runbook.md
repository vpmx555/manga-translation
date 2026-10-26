# Runbook v7 — chuẩn bị, chạy và thu hồi

[← Chỉ mục](../README.md)

Hướng dẫn thao tác không cần agent, có đầy đủ lệnh PowerShell và thiết lập
Runpod Console: [Tự chạy test toàn pipeline](self-service-test.md).

## Phạm vi và điểm vào

Đọc toàn bộ [fast policy](../../archive/news/runpod_agent_fast_policy_vi.md) trước thao
tác Runpod; đối chiếu [operational plan](../../archive/plans/runpod-v6-agent-operational-plan.md).
Runpod cần người dùng yêu cầu trực tiếp cho tác vụ hiện tại. Chạy local hoặc
chuẩn bị script không cấp quyền thuê/chạy máy. Mặc định L40S Secure, không GPU
fallback. Các lệnh dưới là hướng dẫn, không tự cấp quyền thực thi model.

Canonical lane là `deployment/runpod/local_v6.py` với native Syncthing;
`runpod-v6.ps1` là wrapper. Không dùng `v6.py`, `sync.py run` hoặc `legacy` như
đường mặc định. CLI giữ tên v6 cho tương thích, dù tài liệu gọi pipeline v7.

## 1. Chuẩn bị offline trước khi thuê

Xác định story/chapter/input và stage cuối cần chạy. Nếu chỉ test graph, dừng ở
`relationship`; không chạy dịch/render để rồi bỏ kết quả. Chọn checkpoint đã
verified nếu muốn reuse. Kiểm tra dung lượng cả ổ C (env/service) và D (output).

Chốt source, config và locks. JSON Runpod chính hiện là
`configs/pipeline-v6-runpod.json`; `local_v6.prepare` đóng băng effective config
từ lane hiện hành, không có flag `--config` tùy ý ở CLI prepare này. Nếu cần
thay config base, sửa trước prepare rồi tạo batch mới. Không tự thêm flag chưa
có trong `--help`.

Ví dụ PowerShell cho full pipeline mới; thay batch bằng tên duy nhất:

```powershell
.\venv\Scripts\python.exe deployment/runpod/local_v6.py prepare `
  --batch iris-zero-ch1-v7-l40s-YYYYMMDD-r1 `
  --gpu-preset l40s --llm-slots 64 --story-workers 1 `
  --story iris-zero --chapter chapter-001 --locks .runpod/v6-locks `
  "D:\download\EN-data\Iris Zero\EN\Chapter 1"
```

64 là trần config được hỗ trợ, không phải bảo đảm VRAM cho mọi request64×32k.
Freeze slots thấp hơn nếu mục tiêu/budget chỉ cần8. Cần qualification hợp lệ
cho profile thực thi; không bỏ kiểm tra để đẩy concurrency cao.

Prepare tạo `.runpod/v6/<batch>/plan.json`, source/config/input archives và lock
bundle. Đọc derived summary/hash của plan; không sửa plan/archive sau prepare.
Mọi bundled source/config change yêu cầu prepare batch mới.

Chuẩn bị native pairing khi còn offline:

```powershell
.\venv\Scripts\python.exe deployment/runpod/native_sync.py prepare `
  --plan .runpod/v6/<batch>/plan.json
```

Native daemon của batch có identity/home/GUI port riêng. Không chiếm cổng hoặc
dừng daemon của batch khác. File `native-sync.json` ghi device IDs, destination
và ports; private keys không được đưa vào log/tài liệu.

## 2. Provision pod

Đọc live L40S Secure stock/giá và CUDA floor từ locks. Báo shape/giá trước tạo.
Chọn pinned boot image tương thích; tạo một pod, không mở thêm vì máy đầu đang
pull image. Chỉ expose `22/tcp` cho SSH và `22000/tcp` cho native sync của lane
này; vLLM ở localhost, không mở public inference/Jupyter nếu không cần.

Khi có direct SSH, kiểm tra GPU, driver/CUDA compatibility, local filesystem và
disk. Phân biệt status RUNNING trên API với SSH/runtime thực sự sẵn sàng. Pod
không có tiến triển đo được trong10 phút phải điều tra, không báo “đang inference”.

`local_v6.launch` dùng `.runpod/connection.json`; cập nhật từ read-back của đúng
pod, bao gồm host, SSH port, user/key path, `pod_id` và Syncthing TCP mapping.
Không dùng lại địa chỉ cổng của pod đã terminate. Chỉ ghi đường dẫn key, không
copy private key vào repo hoặc output.

## 3. Launch đúng một lần

```powershell
.\venv\Scripts\python.exe deployment/runpod/local_v6.py launch `
  --plan .runpod/v6/<batch>/plan.json --pod-id <pod-id>
```

Launch kiểm tra frozen input, pair/canary native sync, upload bootstrap nhỏ,
khởi động driver và tạo `bound-plan.json` cùng native verifier. Handshake có thể
mất vài phút. Không đặt timeout ngắn rồi khởi chạy lặp: kiểm tra launch record,
driver identity và bound plan trước. `Driver already active` là bảo vệ chống
trùng driver, không phải lý do tạo thêm pod.

Bootstrap verify local working-set, unpack source/locks/input, restore/import
nếu có, tạo envs, prefetch model, kiểm tra GPU và qualification trước chapter.
Setup/prefetch/build image không phải inference. Theo dõi stage thực tế.

## 4. Theo dõi tiến triển

```powershell
.\venv\Scripts\python.exe deployment/runpod/local_v6.py watch `
  --plan .runpod/v6/<batch>/bound-plan.json

.\venv\Scripts\python.exe deployment/runpod/native_sync.py status `
  --plan .runpod/v6/<batch>/plan.json
```

`watch` trả trạng thái bootstrap/job và log tail. Khi phân tích log/JSON dùng
context-mode để xuất summary, không in lại source manifests/crop inventories.
Trong inference kiểm tra completed target/decision counts, GPU utilization,
context/OOM errors, model service health và replication errors. GPU0% ngắn có
thể là planning/checkpoint; phải đối chiếu log và counters để chẩn đoán.

Gửi cập nhật gọn về stage, tiến triển, lỗi và việc đang xử lý; không để người
dùng chờ lâu không biết tình trạng. Không dùng review Markdown làm approval
gate để giữ pod tính phí trong khi agent đọc tài liệu.

## 5. Kết thúc và backup proof

Terminal batch có thể là completed, completed_with_review, completed_with_errors
hoặc interrupted. Không gọi completed_with_errors là inference thành công toàn
bộ. Dù kết quả lỗi vẫn phải giữ đầy đủ outputs/receipts/visualizations.

```powershell
.\venv\Scripts\python.exe deployment/runpod/native_verify.py `
  --plan .runpod/v6/<batch>/plan.json
```

Launch đã mở verifier chờ ở nền; kiểm tra receipt trước mở watcher thứ hai.
`verification.json` ở destination cần chứng minh archives/hash/size/CRC và
coverage. `replication_or_pipeline_pending` chưa phải backup thành công.
`--wait` dùng khi cần verifier tiếp tục chờ; không block agent nhiều phút mà
không có cập nhật trạng thái.

Sau khi verified retrieval/backup, terminate **chỉ pod thuộc tác vụ này**, rồi
read-back xác nhận không còn tài nguyên. Ghi pod ID, terminal status, backup
path và teardown receipt. Không terminate trước khi giữ được dữ liệu, không
để pod rảnh tính phí chỉ vì còn phải viết báo cáo/docs.

## CLI pipeline trực tiếp

Các lệnh này dùng khi môi trường đã được chuẩn bị và việc chạy đã được yêu cầu;
không khuyến nghị chạy model dài trên Windows:

```powershell
.\venv\Scripts\python.exe run.py run "<image-folder>" `
  --story <story> --chapter <chapter> --config configs/pipeline-v6-runpod.json `
  --until relationship

.\venv\Scripts\python.exe run.py resume --run-dir "<run-dir>" --until relationship
.\venv\Scripts\python.exe run.py review --run-dir "<run-dir>"
```

`review` chỉ tái tạo review, không inference. Lệnh `extract` hiện đi qua
`bubble_geometry`, không có nghĩa chỉ chạy OCR. Các stage-specific commands
yêu cầu dependencies đã commit. Thay config cho v7 run cần new/reanalyze revision,
không truyền `translate --config` để sửa run đã freeze.
