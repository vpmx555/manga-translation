> Interface update 2026-10-21: [independent v6 analysis](../v6/independent-analysis.md).
> GPU override 2026-10-22: **L40S Secure for every Runpod task**, unless the user explicitly chooses another GPU for the current task. Older 5090/4090 selections below are historical; do not use them as defaults or fallbacks. Freeze a new batch when changing GPU.
> The local working-set/native Syncthing lane remains canonical. Shared helpers moved to native_prepare.py/native_transport.py; v6.py, sync.py and the legacy CLI lane were removed.
> Old commands below are retained for history. New source/config must be frozen as a new batch; no GPU qualification has been performed by this refactor.

# Plan vận hành Runpod v6 dành cho agent

**Runbook hiện hành, cập nhật 2026-10-15 theo yêu cầu người dùng.** Đây là nguồn
quy trình mặc định được chỉ định trong `AGENTS.md`. `runpod-v6.ps1` gọi
`local_v6.py`; `v6.py` chỉ là controller tương thích qua `runpod-v6.ps1 legacy`
khi được yêu cầu riêng. Tài liệu cũ có chữ “mới” không thay thế runbook này.

Kiến trúc native/local được kiểm chứng gần nhất ngày 2026-10-13. vLLM/Qwen mới
chỉ có kiểm tra chuẩn bị; chưa có bằng chứng inference GPU của phiên hiện tại.
Ngày hoặc quyền chạy ghi trong tài liệu lịch sử không cấp quyền cho task mới. Tài liệu này
dành cho agent tiếp quản một batch manga nhiều chapter, từ lúc chưa thuê GPU đến
khi dữ liệu đã về máy và pod được terminate. Nó mô tả luồng Pod + working set
local; không áp dụng cho Runpod Serverless.

Phải đọc toàn bộ [chính sách Runpod của dự án](../news/runpod_agent_fast_policy_vi.md)
trước mọi thao tác lập kế hoạch, tạo pod, kết nối, chạy, theo dõi, sao lưu hoặc
teardown. Khi tài liệu này và tool/schema đang kết nối khác nhau, schema của tool
hiện tại quyết định cú pháp; các quy tắc dữ liệu, xác minh và chi phí của dự án
vẫn bắt buộc.

### Quyết định đang có hiệu lực

| Mục | Quy tắc hiện hành |
|---|---|
| Điểm vào | `runpod-v6.ps1` → `deployment/runpod/local_v6.py` |
| Engine/model | Đọc config frozen; hiện là vLLM + Qwen 3.5 27B AWQ 4-bit; không dùng Ollama/Gemma cũ |
| GPU/context | Theo task/config; từ yêu cầu 16/10 Iris Zero chuyển RTX 5090, context 32768, một LLM slot, không offload, không tự chuyển GPU khác |
| Storage | Source/env/runtime/model/input/output/log ở `/opt/manga-work`; durable archives ở `/workspace/manga-runtime/local-v6` |
| Transfer | Syncthing native, xác minh thiết bị/canary; SSH dành cho controller nhỏ và chẩn đoán |
| Chuẩn bị | Locks/source/config/input/native pairing trước thuê; boot image có tag/digest tương thích; không mặc định build/push image lớn |
| Hoàn tất | Terminal state + archive/inventory/SHA/CRC về PC, rồi terminate đúng pod task-owned khi đã có quyền |

Build CPU, prepare offline, Pod `RUNNING`, preflight và analyze là các trạng thái
khác nhau. Báo đúng bằng chứng; archive hợp lệ không chứng minh analyze thành công.

**Quyết định cuối của task Iris Zero, 16/10:** RTX 5090 **Secure**, không đổi sang
Community nếu hết máy. Người dùng sẽ yêu cầu chạy lại sau; hiện dừng provisioning
và theo dõi stock. Xem [plan tiếp tục đã lưu](iris-zero-ch1-vllm-5090-secure-resume-20261016.md).

## 1. Kiến trúc được chọn

Luồng phù hợp nhất hiện tại là một GPU Pod Secure Cloud với working set đặt trên
NVMe/container-local tại `/opt/manga-work`, còn checkpoint và publication bền
đặt dưới `/workspace/manga-runtime`. Một hàng đợi duy nhất chạy tuần tự theo thứ
tự chapter. Syncthing native truyền input từ PC và liên tục sao chép cả live data
lẫn durable data về PC; việc truyền byte không phụ thuộc agent hay Python
collector sau khi launch.

```text
PC prepared batch (send-only)
        │
        │ Syncthing native + SHA/CRC/per-image validation
        ▼
Pod /opt/manga-work/ingress
        │ atomic publish
        ▼
local_v6 remote bootstrap ──► one detached batch driver ──► chapters in order
        │                               │
        │                               ├─ live output on local disk
        │                               └─ checkpoint/publication on durable disk
        ▼
PC live/ + durable/ (receive-only, versioned, ignore remote deletes)
        │
        ▼
native_verify: archive SHA/size/CRC + terminal state
        │
        ▼
terminate only the task-owned pod after verified backup
```

Pod được chọn thay vì Serverless vì pipeline có nhiều model/env, cần local disk,
có identity bank theo story và phải giữ checkpoint/visualization đầy đủ. Không
chạy nhiều chapter của cùng story đồng thời vì thứ tự cập nhật bank là một phần
của contract.

## 2. Điều kiện bắt buộc trước khi thuê GPU

Agent chỉ được dùng Runpod khi người dùng trực tiếp yêu cầu và cho phép trong task
hiện tại. Quyền từ session cũ hoặc việc chạy local không thay thế yêu cầu này.

Hoàn tất các mục sau trên PC trước khi tạo pod:

1. Chốt source/config/code cho batch. Dữ liệu mặc định nằm dưới
   `D:\download\EN-data\...` nếu người dùng không chỉ nguồn khác.
2. Dùng batch ID mới, lowercase ASCII và không tái sử dụng thư mục đã prepare.
   `local_v6.py prepare` từ chối ghi đè batch cũ.
3. Có đủ lock `requirements.lock` trong `.runpod/v6-locks`: main theo dependency
   signature, `visual-<signature>`, `hayai-<signature>` và **`vllm`** khi backend
   là vLLM. `prepare_locks.py` hiện resolve main và vLLM trên CPU Docker/WSL;
   phải kiểm tra visual/Hayai đã có từ môi trường tương thích. Không thuê GPU để
   resolve dependency. `locks.zip` của vLLM phải có đủ bốn lock. Chọn host CUDA
   floor theo Torch/CUDA thực trong lock, không chỉ theo tag boot image.
4. Cài binary Syncthing v2.1.6 đã pin và kiểm tra SHA-256:

   ```powershell
   python deployment/runpod/prepare_native_sync.py
   ```

   Không chạy lại lệnh này khi `syncthing.exe` đang hoạt động: bản hiện tại ghi
   lại executable và Windows có thể trả `PermissionError`. Nếu archive và binary
   đúng digest đã tồn tại, dùng lại chúng.
5. Chạy các test fixture ngắn cần thiết trước khi thuê. Không chạy model pilot CPU
   dài. GPU parity thật sẽ chạy trong bootstrap trên pod trước inference.
6. Kiểm tra dung lượng PC cho bản sao output và giữ PC bật, có mạng trong suốt
   batch. Syncthing chỉ bảo vệ dữ liệu đã commit và đã truyền.

`native_sync.py prepare` chọn GUI/listen port PC đang trống và ghi chúng vào
`native-sync.json`; không ghi đè pairing đã chuẩn bị. `start_pc`/`status` kiểm
`myID` đúng device của batch. Daemon của batch khác phải được giữ nguyên. Nếu
port bị chiếm sau prepare hoặc canary không về, sửa kết nối trước inference;
không coi một API localhost đang trả lời là daemon đúng batch.

Prepare lại cùng pairing chỉ xác minh identity và khôi phục watcher, không tạo
key/config mới. Watcher review xác nhận startup bằng launch token và PID thực
trong receipt; PID của Python redirector trên Windows có thể khác PID worker.
Môi trường vLLM tạo venv bằng `--allow-existing` để giữ lock đã đưa vào thư mục
trước bootstrap; tuyệt đối không dùng `--clear` làm mất frozen lock.

### Gate assets visual và phạm vi chạy

`local_v6.py` hiện chuẩn bị inpaint và tạo alias dưới đây trước doctor khi batch
có stage sau analyze và inpaint bật. Alias sai đích bị từ chối:

```text
/opt/manga-work/project/models/visual
    -> /opt/manga-work/runtime/models/visual
```

Đây là sửa source chuẩn bị 15/10, chưa kiểm chứng inpaint trên GPU trong phiên
mới. Batch `--until analyze` không tải AOT hoặc chạy inpaint; không mở rộng scope
để kiểm alias. Source mới phải được đóng băng vào batch mới trước launch.

## 3. Prepare một batch nhiều story

Ví dụ đã dùng cho 9 chapter, theo đúng thứ tự bank:

```powershell
$batch = 'v6-nine-stories-idfix-YYYYMMDD'
$locks = '.runpod/v6-locks'

.\runpod-v6.ps1 prepare `
  'D:\download\EN-data\Bocchi\Chapter 1' `
  --batch $batch `
  --gpu-preset rtx5090 `
  --story bocchi `
  --chapter chapter-001 `
  --additional-chapter chapter-002 'D:\download\EN-data\Bocchi\Chapter 2' `
  --additional-chapter chapter-003 'D:\download\EN-data\Bocchi\Chapter 3' `
  --additional-story iris-zero chapter-001 'D:\download\EN-data\Iris Zero\EN\Chapter 1' `
  --additional-story iris-zero chapter-002 'D:\download\EN-data\Iris Zero\EN\Chapter 2' `
  --additional-story iris-zero chapter-003 'D:\download\EN-data\Iris Zero\EN\Chapter 3' `
  --additional-story witchriv chapter-001 'D:\download\EN-data\Witchriv\EN\Chapter 1' `
  --additional-story witchriv chapter-002 'D:\download\EN-data\Witchriv\EN\Chapter 2' `
  --additional-story witchriv chapter-003 'D:\download\EN-data\Witchriv\EN\Chapter 3' `
  --locks $locks

$plan = ".runpod/v6/$batch/plan.json"
python deployment/runpod/native_sync.py --plan $plan prepare
```

`prepare` tạo `source.zip`, `pipeline-v6.json`, `locks.zip`, từng ZIP chapter và
receipt SHA-256/size/per-image. Input phải là ảnh trực tiếp trong folder chapter;
folder rỗng bị từ chối. Đọc model/revision/GPU/context từ config frozen bằng code.
Hiện config chọn vLLM 0.31.0, `cyankiwi/Qwen3.5-27B-AWQ-4bit`, revision
`5cba9b5f1bca69fab9b75c88ae7817ae22d7d898`, context 32768, thinking tắt,
output reserve 2048, một LLM slot, analyze batch tối đa 3. Chi tiết nằm trong
[thiết kế vLLM](qwen35-vllm-4090-implementation-vi.md). Section chung lịch sử vẫn
có tên `ollama`; **`backend: vllm` quyết định engine**. Prefetch/doctor thủ công
phải nhận đúng `--config` frozen, không dùng default Gemma của `pipeline.json`.

Ví dụ đúng task Iris Zero hiện tại, dùng batch ID chưa tồn tại:

```powershell
$batch = 'iris-zero-ch1-vllm-local-YYYYMMDD'
.\runpod-v6.ps1 prepare 'D:\download\EN-data\Iris Zero\EN\Chapter 1' `
  --batch $batch --story iris-zero --chapter chapter-001 --until analyze `
  --locks '.runpod/v6-locks'
$plan = ".runpod/v6/$batch/plan.json"
python deployment/runpod/native_sync.py prepare --plan $plan
```

Sau prepare, kiểm tra `plan.json` bằng code, không đọc thủ công hàng nghìn receipt:

- đúng batch ID, thứ tự story/chapter và tổng số trang;
- mỗi chapter có archive, byte count, SHA-256 và image inventory;
- `local_working_set=true`, `native_sync=true`;
- canonical paths là `/opt/manga-work/runtime`, `/opt/manga-work/project` và
  `/workspace/manga-runtime/local-v6`;
- đủ `source.zip`, `locks.zip`, `pipeline-v6.json` và các chapter archive;
- `native-sync.json` tồn tại, destination nằm ở
  `outputs/runpod-<batch>`; private keys/API key của Syncthing chỉ nằm dưới
  `%LOCALAPPDATA%\manga-runpod-sync\<batch>`, không nằm trong source bundle hay
  folder được replicate.

Sau khi prepare, không sửa source/config/model/prompt/threshold của batch. Muốn
đổi phải tạo batch/revision mới. Các helper theo dõi đặt trong `.runpod` hoặc
`outputs` được phép sửa nếu không nằm trong frozen bundle.

## 4. Chọn và tạo Pod

GPU theo task hiện tại và `runtime.required_gpu` trong config frozen; không dùng
thứ tự fallback GPU của batch cũ. Mỗi lần tạo phải đọc stock, data center, CUDA versions và
giá hiện tại; không dùng giá/stock từ tài liệu này. Với Runpod MCP, đọc
`get-gpu-type` với `include=["AVAILABILITY"]`, `product=["POD"]`, cloud
`SECURE` và CUDA floor phù hợp. Với runpodctl, dùng `gpu list
--include-unavailable` và đọc availability theo data center.

Mặc định dùng Secure Cloud. Incident thực tế cho thấy pod RTX 5090 Community có
`nvidia-smi` nhưng `cuInit=999` và CUDA không enumerate; pod Secure thay thế vượt
qua cuInit, Torch matmul và geometry parity. Chỉ dùng Community khi người dùng yêu
cầu trực tiếp và vẫn phải chạy toàn bộ preflight.

Shape đã kiểm chứng:

| Thành phần | Giá trị |
|---|---|
| Cloud | `SECURE` |
| GPU | 1 × GPU đúng task/config; Iris Zero hiện tại là RTX 5090 theo yêu cầu chuyển GPU ngày 16/10 |
| CUDA floor | Theo CUDA cao nhất của các env/lock thực tế, không chỉ boot image |
| Image | Boot image tương thích có tag/digest; `vpmx555/manga-translate-v2:v5.0.2-fast-code.1` là bằng chứng lịch sử, không tự chứng minh tương thích vLLM mới; không dùng `latest` |
| Container disk | Đủ bốn env + model/cache + peak output + headroom; shape cũ 100 GB không tự chứng minh đủ cho Qwen mới |
| Persistent volume disk | Đủ checkpoint/publication, mount `/workspace/manga-runtime`; không mặc định thêm network volume riêng |
| Ports | `22/tcp`, `22000/tcp` |
| SSH | `startSsh=true`, dùng SSH key đã đăng ký |
| Environment | `MANGA_EXECUTION_ROOT=/opt/manga-work/execution`, `MANGA_RUNTIME=/opt/manga-work/runtime`, `PUBLIC_KEY=<account SSH public key>` |
| Entrypoint/cmd | `/bin/bash -lc`, `exec /start.sh` |

Trước create phải báo giá GPU/giờ lấy từ catalog read. Create đúng một pod; nếu
placement trả lỗi thì đọc lại stock/state; không tự chuyển GPU, không retry mù và
không tạo song song nhiều pod. Sau create, đọc lại pod để xác nhận image, GPU,
cloud, CUDA, disk, mount và ports. `RUNNING` chưa đủ: chờ `runtime.ports` có mapping
và SSH thực sự dùng được.

MCP phù hợp cho catalog/create/get/log/lifecycle. Nếu MCP không có hoặc agent cần
lệnh tái lập được, dùng runpodctl phiên bản hiện tại và kiểm tra `--help` trước khi
gọi. Không ghi API key vào repo, plan, log hoặc Markdown.

## 5. Ghi connection và preflight

Từ read-back của pod, lấy direct public mapping cho `22/tcp` và `22000/tcp`. Ghi
`.runpod/connection.json` theo schema sau; đây là state cục bộ, không commit:

```json
{
  "host": "<public-ip>",
  "port": 40000,
  "key": "C:\\Users\\<user>\\.ssh\\id_ed25519",
  "user": "root",
  "pod_id": "<pod-id>",
  "syncthing_address": "tcp://<public-ip>:40001"
}
```

Port bên ngoài thay đổi khi pod stop/start; luôn đọc lại và cập nhật file trước
khi reconnect. Proxy SSH không thay thế direct TCP khi workflow cần SCP và
Syncthing direct port.

Trước launch, xác nhận ngắn:

- SSH chạy được và pod ID khớp `connection.json`;
- `/opt/manga-work` là local filesystem, không phải FUSE/NFS/CIFS/9p;
- còn ít nhất 35 GiB local disk;
- `nvidia-smi` nhìn thấy đúng GPU;
- `cuInit=0`, `torch.cuda.is_available()` và device count hợp lệ;
- Torch CUDA matmul chạy thật;
- không có driver/launch identity cũ đang hoạt động cho cùng batch;
- không có pod task-owned khác đang bill do create thất bại trước đó.

Các kiểm tra dependency/model/CUDA doctor và geometry parity 3 bài sẽ tiếp tục
được bootstrap thực hiện trước inference. Không bỏ qua integrity check để tiết
kiệm thời gian.

## 6. Một lệnh launch, không điều khiển tiếp trong luồng bình thường

Sau khi mọi gate trên đạt, chạy đúng một lệnh:

```powershell
.\runpod-v6.ps1 launch --plan $plan --pod-id <TASK_OWNED_POD_ID>
$boundPlan = ".runpod/v6/$batch/bound-plan.json"
```

Lệnh này:

1. xác minh frozen plan/config;
2. kích hoạt hai daemon Syncthing native, ghép cặp và kiểm tra canary;
3. chỉ upload các controller nhỏ qua SSH; payload lớn đi bằng Syncthing;
4. tạo detached bootstrap/driver trên pod và khóa chống launch trùng;
5. chờ input archive được commit, kiểm tra CRC/SHA/per-image rồi publish atomic;
6. cài env từ frozen locks, prefetch model, chạy doctor và GPU parity;
7. chạy toàn bộ queue tuần tự, giữ partial/failure và vẫn thử chapter còn lại;
8. khởi động detached `native_verify.py --wait` trên PC.

Nếu lệnh launch báo handshake/canary chưa xác minh thì inference chưa được khởi
động; sửa kết nối trước, không chạy một driver khác. Sau launch thành công không
cần lệnh khác để hàng đợi và việc sao chép dữ liệu tiếp tục. PC vẫn phải bật và
có mạng.

## 7. Theo dõi chỉ đọc và chẩn đoán

Theo dõi là để phát hiện lỗi và bảo vệ chi phí, không phải approval gate. Ưu tiên
state/receipt JSON được replicate về PC:

```powershell
python deployment/runpod/native_sync.py --plan $plan status
.\runpod-v6.ps1 watch --plan $boundPlan
```

`watch` cần pod còn truy cập được; native replica vẫn là nguồn bàn giao. Có thể
dùng helper batch-specific `monitor.js` qua context-mode để chỉ in queue, stage,
semantic counts và replica bytes. Không đọc Markdown review để quyết định có cho
stage sau chạy hay không.

Báo tiến độ định kỳ khoảng hai phút. Nếu 10 phút không có tiến triển đo được, làm
một lượt chẩn đoán giới hạn:

1. đọc `state.json`, `bootstrap.json`, `launch.json`, stage receipt và PID identity;
2. phân biệt image pull/bootstrap/model download với inference;
3. đọc container/system logs có giới hạn;
4. đo GPU, VRAM, power, CPU, disk và tiến trình đang hoạt động;
5. xác định một nguyên nhân chính rồi sửa hạ tầng nếu nằm trong quyền đã có.

Không coi GPU utilization thấp là lỗi riêng. Analyze/translate/typeset có request
ngắn và CPU font/raster xen kẽ; bubble geometry đã có CUDA receipt. Tham khảo báo
cáo đo và backlog tại
`outputs/runpod-v6-nine-stories-idfix-20261013/GPU-UTILIZATION.md` và
`GPU-OPTIMIZATION-BACKLOG.md` khi lập revision tối ưu sau này.

### Dấu hiệu và hành động

| Dấu hiệu | Kết luận/hành động |
|---|---|
| `nvidia-smi` có GPU nhưng cuInit lỗi/device count 0 | Host CUDA hỏng; backup diagnostic, thay pod theo stock mới, không chờ vô hạn |
| Driver identity còn sống và receipt tăng | Đang chạy; không launch lại |
| `runtime.ports` rỗng ngay sau create | Pod còn khởi tạo; chờ/read-back, không tạo pod thứ hai |
| Input chỉ có folder rỗng hoặc `.syncthing.*.tmp` | Chưa ready; chờ committed ZIP, không bypass atomic ingress |
| Chapter fail nhưng driver chuyển chapter kế tiếp | Đúng chính sách: giữ failure rồi thử hết queue |
| Inpaint báo thiếu `project/models/visual/...` | Kiểm alias trong frozen source/runtime; giữ lỗi và partial, không hotpatch hoặc đổi model giữa batch |
| Semantic target unresolved | Giữ source-only + review flag theo contract; không biến thành provider success giả |
| Syncthing completion 100% | Chưa đủ để kết luận backup cuối; vẫn phải kiểm archive/inventory và terminal state |

Không sửa source/prompt/threshold/model giữa batch. Sửa hạ tầng delivery có thể
thực hiện nếu không đổi frozen source/config. Một recovery chỉ được xếp tự động,
có marker chống lặp, giữ publication lỗi ban đầu và tận dụng checkpoint; không tạo
retry vô hạn.

## 8. Xác minh dữ liệu về PC

Thư mục mặc định:

```text
outputs/runpod-<batch>/
├── live/        # runtime/data liên tục
├── durable/     # checkpoints, jobs, publications, banks, audits
└── verification.json
```

Verifier chuẩn:

```powershell
python deployment/runpod/native_verify.py --plan $plan --wait
```

`launch` đã tự khởi động verifier detached; lệnh trên chỉ dùng khi agent tiếp quản
cần kiểm tra/khởi động lại verifier, sau khi chắc chắn không có instance tương tự
đang chạy. Final verification phải chứng minh:

- state của toàn bộ chapter đã terminal và mỗi writer đã dừng;
- archive publication tồn tại ở PC và khớp SHA-256, byte count, CRC;
- inventory/per-file integrity khớp khi contract yêu cầu;
- story bank/audit đã publish;
- full output, images, masks, translations, visualization và review còn đủ;
- recovery/postpass, nếu có, đã `finished`, không chỉ `waiting` hoặc `started`;
- `verification.json` cuối chứa trạng thái sau recovery. Verifier ban đầu có thể
  ghi báo cáo trung gian khi state đạt `completed_with_errors`; báo cáo đó không
  thay thế xác minh postpass.

Không dùng `local_v6.py collect` làm cơ chế bảo vệ chính. Nó là fallback để tải
publication đã dừng; Syncthing native mới là cơ chế liên tục, độc lập agent.

## 9. Teardown và chi phí

Chỉ terminate pod do task hiện tại tạo hoặc pod người dùng nêu đích danh. Không
đụng pod khác tìm thấy qua list. Trước terminate, lưu receipt hạ tầng, state cuối,
diagnostic cần thiết và bằng chứng verification trên PC.

Sau khi final verification đạt:

1. đọc lại đúng pod ID và xác nhận đây là task-owned resource;
2. terminate qua Runpod MCP `pod-action`/delete tương ứng hoặc runpodctl;
3. chấp nhận response 204 kể cả client có thể báo parse JSON lỗi;
4. đọc lại; 404/not found là bằng chứng đã terminate;
5. ghi teardown receipt gồm pod ID, thời gian, action status và read-back.

Stop không phải teardown: disk/volume vẫn có thể bill. Persistent volume disk gắn
với pod bị mất khi terminate theo shape này, vì vậy tuyệt đối không terminate
trước khi PC backup đã xác minh. Network volume, nếu một batch khác dùng, có vòng
đời riêng và vẫn bill cho đến khi xóa.

Hiện chưa có API credential tối thiểu cho daemon PC tự terminate pod. Nếu agent
hoặc session biến mất, queue và Syncthing vẫn chạy nhưng pod tiếp tục bill sau khi
hoàn thành. Đây là giới hạn vận hành phải được báo rõ; không nhúng Runpod API key
vào shared folder để giải quyết tạm.

## 10. Checklist tiếp quản nhanh cho agent khác

1. Đọc tài liệu này và policy bắt buộc.
2. Đọc `.runpod/v6/<batch>/plan.json`, `bound-plan.json`, receipt hạ tầng mới nhất,
   `outputs/runpod-<batch>/CONTINUATION.md`, `jobs/state.json`, `launch.json`,
   `bootstrap.json`, `postpass.json` và `verification.json` nếu có.
3. Không tin pod ID trong receipt cũ hơn; xác nhận resource hiện tại bằng Runpod
   read. Không in secret/private key.
4. Kiểm tra driver/controller/verifier identity trước khi tạo process mới.
5. Nếu batch chưa launch, hoàn thành toàn bộ gate offline, catalog/price/stock read,
   create/read-back và chỉ chạy một lệnh launch.
6. Nếu batch đang chạy, chỉ monitor read-only trừ khi có lỗi hạ tầng rõ ràng. Không
   đổi nội dung inference giữa batch.
7. Nếu batch terminal, đợi native replica ổn định, chạy final verification bao gồm
   recovery marker, rồi terminate đúng task-owned pod và read-back 404.

## 11. Bằng chứng của lượt chạy kiểm chứng

Lượt `v6-nine-stories-idfix-20261013` đã chứng minh các phần sau:

- Pod Secure RTX 5090 `jo300e3psjihjv`, EU-CZ-1, image pin ở trên, container/persistent
  disk 100 GB; giá tại thời điểm tạo là $1.19/giờ và không được tái sử dụng như giá hiện tại.
- cuInit=0, một GPU, Torch CUDA matmul pass; bubble geometry parity 3/3 pass không skip.
- Syncthing native v2.1.6 direct TCP chuyển input/output, PC receive-only có versioning
  và ignore-delete; queue/transfer tiếp tục khi agent ngắt.
- Một driver detached chạy toàn queue; lock ngăn launch trùng.
- Chapter failure được giữ và queue vẫn chuyển chapter sau.
- Inpaint alias là lỗi delivery thật cần sửa vĩnh viễn như gate ở mục 2.

Chi tiết thiết kế ID và lịch sử incident nằm trong
[handoff v6](../v6/deployment/HANDOFF-bocchi-v6-id-fix-runpod.md). Kiến trúc local
working set nền tảng nằm trong
[local-working-set.md](../v6/deployment/local-working-set.md).
