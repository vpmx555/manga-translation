# Vận hành pipeline v6 trên Runpod

> **Tài liệu controller workspace cũ.** Quy trình mặc định từ 15/10/2026 là
> [local working-set + Syncthing native](../../plans/runpod-v6-agent-operational-plan.md).
> `runpod-v6.ps1` hiện gọi `local_v6.py`; các lệnh prepare/launch bên dưới dành cho
> controller `v6.py` tương thích và không còn là cú pháp của wrapper mặc định.
> Không lấy model/GPU/context hoặc trạng thái chưa triển khai bên dưới làm trạng thái hiện hành.

Các file dưới đây mới được chuẩn bị. **Chưa chạy kiểm thử, build/push image,
SSH/API, upload, launch hoặc tạo/khởi động pod trong phiên này.** Pilot local đã dừng.
Chỉ vận hành khi người dùng cho phép và trực tiếp yêu cầu Runpod cho công việc đó.

Đọc đầy đủ [chính sách Runpod](../../news/runpod_agent_fast_policy_vi.md) trước
phiên vận hành. [Review chuẩn bị](runpod-preparation-review.md) ghi phạm vi rà soát
tĩnh; [trạng thái triển khai](implementation-status.md) ghi phần chưa kiểm chứng.

## File và hợp đồng

| File | Vai trò | Input → output |
|---|---|---|
| [runpod-v6.ps1](../../../runpod-v6.ps1) | Điểm vào trên Windows; không có args chỉ hiện help. | Args → controller Python; không tự chọn pod. |
| [v6.py](../../../deployment/runpod/v6.py) | Prepare offline / explicit launch / watch / collect. | Folder ảnh + batch/story/chapter → plan/config/source ZIP; launch trả batch/PID/state/log và bound plan. |
| [pipeline-v6-runpod.json](../../../configs/pipeline-v6-runpod.json) | Preset CUDA, v6 và Hayai; giữ visualization. | Config tĩnh → config được runtime chỉ định interpreter/assets. |
| [batch_v6.py](../../../deployment/runpod/batch_v6.py) | Một driver detached, thứ tự chapter cố định. | Frozen plan + config hash + source hash + image receipts → state, từng run, review và errors.md. |
| [v6_contract.py](../../../deployment/runpod/v6_contract.py) | Kiểm tra artifact thực tế theo hợp đồng v6. | RunStore + entry → coverage, kiểu render, needs_review; lỗi integrity được ghi riêng khi cứu partial. |
| [collect.py](../../../deployment/runpod/collect.py) | Tải chapter đã ngừng ghi trong khi chapter sau chạy. | Bound plan → output/bank/audit và collection.json ở folder mới. |
| [sync.py](../../../deployment/runpod/sync.py) | Đồng bộ nguyên tử và transfer có inventory. | Source/input → publish sau khi archive đầy đủ; download → CRC/bytes/SHA-256 đối chiếu inventory. |
| [Dockerfile.runpod.fast](../../../Dockerfile.runpod.fast) | Chuẩn bị các môi trường trước khi thuê GPU. | Source → image có dependencies và font DejaVu; CUDA thật phải xác minh trên pod. |

Ngưỡng: text 0.45; text–character 0.25; dialogue logit -0.5; character 0.35;
character–character 0.75; bubble 0.45. Dialogue và association vẫn độc lập.
MAGI/Koharu/Hayai/GLiNER giữ model và revision đã chốt; Ollama dùng
gemma4:e4b-it-q4_K_M, num_ctx 16384. Không thêm model phân loại hints.

Hayai có môi trường riêng với [requirements-hayai.in](../../../deployment/runpod/requirements-hayai.in),
Torch 2.8.0+cu128 và Transformers 4.57.6. Thay requirements tạo signature mới;
không sửa môi trường MAGI. Layout có worker riêng. Image có font tiếng Việt;
các venv Windows không được COPY vào image.

## 1. Chuẩn bị trước khi thuê GPU

Các ví dụ Windows chạy từ root của clone. Thay folder input theo máy; đây là
đường dẫn dữ liệu trong ví dụ, không phải link điều hướng của tài liệu.

~~~powershell
.\runpod-v6.ps1 prepare 'D:\download\EN-data\After School\EN\Chapter 1' --batch after-school-v6 --story after-school --chapter chapter-001 --execution-root /opt/manga-execution
~~~

Lệnh prepare không truy cập Runpod, không nạp model, không chạy pipeline hoặc test.
Nó ghi dưới .runpod/v6/after-school-v6:

- plan.json: pipeline_version=6; batch; execution_root; source_bundle_hash;
  config_sha256; chapters gồm story/story_key/chapter/input/pages/source_folder/images.
- images là map filename → {bytes, sha256}; giữ mọi ảnh của chapter.
- pipeline-v6.json và source.zip. Folder đã có không bị ghi đè.

Điểm vào prepare hiện nhận **một chapter mỗi lần**; driver dùng danh sách chapters
và chạy tuần tự. Không dùng local pilot Windows làm run resume trên Linux.

Chốt dependencies/image/source và input trước khi thuê. Build/push image đã ghim
theo quy trình đã được cấp quyền; hiện chưa thực hiện bước này. Source ZIP là
provenance, không thay thế image đã build. Source thay đổi sau prepare phải tạo
batch mới. Không hot-sync source trong khi workload đang chạy.

Chọn GPU/region, đọc giá/capacity hiện tại và chốt ngân sách/quyền teardown
ở phiên được yêu cầu chạy. Script này không cung cấp giá cũ như giá hiện hành,
không tạo pod/volume/template và không mở rộng tài nguyên.

## 2. Môi trường và preflight trên pod được cấp quyền

Dùng đúng image đã chuẩn bị và đúng filesystem local đã kiểm tra mount/free space.
Mặc định image mới dùng /opt/manga-execution; có thể chọn root khác ở prepare,
nhưng setup, doctor và launch phải dùng cùng root.

Ví dụ Linux trên pod, từ root repository:

~~~sh
MANGA_EXECUTION_ROOT=/opt/manga-execution python3 deployment/runpod/manage.py setup
MANGA_EXECUTION_ROOT=/opt/manga-execution python3 deployment/runpod/manage.py prefetch
MANGA_EXECUTION_ROOT=/opt/manga-execution python3 deployment/runpod/manage.py doctor --inference
~~~

Không lặp setup/prefetch/doctor khi signature/assets không đổi. Setup trên image
không có GPU chỉ dùng --prepare-only; không gọi đó là CUDA đã qua.

Doctor ghi CUDA của MAGI/layout/Hayai và inference Ollama vào last-doctor.json.
Batch yêu cầu receipt đúng signatures, interpreter và execution_root. Inference
OK của doctor chỉ chứng minh đường chạy GPU; không chứng minh chất lượng ngữ nghĩa
hoặc mọi prompt M7 vừa context. Provider vẫn kiểm tra context/reserve trên request
thật; lỗi phải giữ trong journal và coverage, không đổi thành unknown hợp lệ.

Giữ metadata image tag/digest và source bundle hash riêng trong audit/bàn giao.
Không giữ pod trả phí để viết tài liệu, chạy full offline suite hoặc chờ sửa code.

## 3. Launch rõ pod và theo dõi

Lưu SSH connection đúng pod theo controller hiện có. Sau khi người dùng trực tiếp
yêu cầu chạy trên pod đó, dùng:

~~~powershell
.\runpod-v6.ps1 launch --plan '.runpod/v6/after-school-v6/plan.json' --pod-id '<pod-id>'
.\runpod-v6.ps1 watch --plan '.runpod/v6/after-school-v6/bound-plan.json' --wait 45
~~~

Launch đối chiếu Pod ID với connection đã lưu, source/config/input với plan.
Nó đồng bộ source nguyên tử, upload input rồi tạo driver detached trên pod có sẵn.
Không cài dependencies, thuê/khởi động hay xóa pod. bound-plan.json thêm pod_id
và không được chuyển sang pod khác bằng cách sửa tay.

Runtime job: /workspace/manga-runtime/jobs/<batch>/ gồm plan.json,
pipeline-v6.json, launch.json, state.json, driver.log, chapter-NN.log,
environment locks/markers và errors.md. PID kèm start identity; launch trả
starting, chưa phải xác nhận inference thành công. Watch đọc state/counters gọn,
phân biệt driver đã chết với trạng thái running cũ.

Một GPU dùng một lock chapter-batch và một hàng đợi pipeline. Resume chỉ nhận run
v6 đúng source/chapter/config. Không sửa snapshot để tái sử dụng analysis cũ;
khác cấu hình phải chuẩn bị revision theo controller được yêu cầu riêng.

Báo tiến độ mỗi 2 phút khi vận hành: chapter/stage, counter/checkpoint hoặc bytes
transfer thực tế. Sau 10 phút không tăng, chẩn đoán một lượt có giới hạn; heartbeat,
mtime log hoặc PID sống không đủ. Watch chưa có watchdog tự cảnh báo hay teardown.

## 4. Lỗi: chạy hết phạm vi rồi sửa sau

Driver giữ source/config/ngưỡng/prompt cố định. Chapter lỗi được ghi failed,
exit code, run/log, stage, target errors và bước thiếu trong errors.md; tiếp tục
chapter còn lại, không retry vòng lặp hay sửa case tại chỗ. Batch kết thúc bằng
completed_with_errors và exit 2 nếu có lỗi; không đánh dấu partial thành completed.

Review Markdown vẫn tự sinh cuối chapter, kể cả partial có run. Đây là artifact
cho người đọc, không phải cổng agent duyệt để cho phép stage tiếp theo.

## 5. Tải xuống và kết thúc phiên

~~~powershell
.\runpod-v6.ps1 collect --plan '.runpod/v6/after-school-v6/bound-plan.json' --destination 'outputs/runpod-v6-backup-new'
~~~

Collector tải chapter completed hoặc failed/interrupted có writer_stopped và
run_dir. Nó không đóng gói run đang được ghi. Output partial được giữ ngay,
không đợi coverage 100%. Sau khi batch ngừng ghi, tải banks và audit có lock.
MAGI bank và thư mục riêng banks/_persons_v6 chứa registry/journal đều nằm
trong gói banks; không merge person_id vào cluster_id.

Mỗi archive có __transfer_inventory__.json. Kiểm CRC trong lúc extract, đối chiếu
inventory tên file/bytes/SHA-256 và verify receipts đã commit. Lỗi artifact gốc
được ghi riêng; vẫn cứu phần dữ liệu còn lại đã ngừng ghi. collection.json phân biệt
verified/verified_partial, transfer integrity, artifact integrity và chapter thiếu.
Không xem verified_partial như toàn bộ pipeline đã đạt.

Đích mới không ghi đè backup cũ. Mất kết nối hoặc bản sao lỗi giữ dữ liệu phục hồi;
không coi SCP là resumable, không tự tạo lại pod. Source audit có source bundle,
effective config và job environment locks/signatures, không đóng gói model cache
hoặc SSH key làm output.

Collector **không terminate pod**. Sau khi bản sao ngoài pod được xác minh và báo
đủ phần thiếu, người vận hành chỉ terminate đúng tài nguyên task đã được cho phép,
rồi readback trạng thái. Không xóa pod/volume khác hoặc volume chung.

## Giới hạn kết quả

Preset giữ inpaint.enabled=false như cấu hình đã chốt: typeset tạo
placement_preview_original_text_present, chưa phải trang dịch sạch.
Muốn clean_render cần yêu cầu riêng và chuẩn bị AOT phù hợp trước khi chạy.
Chất lượng contour, role/name/speaker, caller identity và shortening chưa được
xác nhận bằng pilot thực đầy đủ của v6.

[Tổng quan v6](../README.md) · [Kế hoạch triển khai](implementation-plan.md)
