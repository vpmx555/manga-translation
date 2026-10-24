# Chỉ thị Runpod cho agent: chạy nhanh, ít I/O, kiểm soát chi phí

**Chỉ thị GPU 22/10/2026:** mọi tác vụ Runpod dùng **L40S Secure**, trừ khi người
dùng chỉ định GPU khác cho chính tác vụ đó. Chỉ thị này thay lựa chọn RTX 5090/4090
cũ trong các ví dụ bên dưới. Hết L40S Secure thì báo thiếu capacity, không tự đổi
GPU/cloud. Không cấp quyền chạy Runpod tự động; vẫn cần yêu cầu hiện tại của user.

Ngày chốt chính sách: 09/10/2026. Áp dụng cho pipeline manga của repository này.
Người dùng đã chọn bộ mặc định đề xuất và ngưỡng cảnh báo **10 phút không có tiến độ thực tế**.
Đây là hướng dẫn vận hành, không phải xác nhận rằng mọi tối ưu bên dưới đã được code triển khai.

**Cập nhật quy trình 15/10/2026:** mọi run v6 mới dùng
[runbook hiện hành](../plans/runpod-v6-agent-operational-plan.md):
`local_v6.py` + working-set local `/opt/manga-work` + Syncthing native + verifier PC.
`runpod-v6.ps1` trỏ vào controller này. Các ví dụ `v6.py`, `/workspace` runtime,
Ollama/Gemma hoặc build/push image lớn bên dưới là hướng dẫn tương thích/lịch sử;
không dùng chúng để thay quy trình hiện hành. Giá/GPU/model phải đọc từ task,
config frozen và catalog hiện tại. Không tự build/push image mới nếu boot image
đã tương thích; chuẩn bị locks/source/input offline vẫn bắt buộc.

## 1. Mục tiêu và quy tắc bắt buộc

Ưu tiên thời gian từ lúc thuê GPU đến khi có đủ kết quả đã tải về; không tối ưu bằng cách
bỏ chapter, giảm chất lượng, mất visualization, bỏ kiểm tra tính toàn vẹn hoặc báo thành công giả.

- Chuẩn bị source, dependency lock, input manifest, kế hoạch chạy và chọn boot image đã pin **trước khi thuê GPU**; chỉ build/push image mới khi cần thay boot image.
- Khi pod sẵn sàng và đã qua kiểm tra tối thiểu, chạy ngay. Không dùng thời gian GPU trả phí để
  viết tài liệu, phỏng vấn người dùng, chạy toàn bộ test offline hoặc khảo sát phương án không cần thiết.
- Code/env/file nhỏ truy cập thường xuyên ưu tiên filesystem local thực sự của container/host;
  volume dùng cho lưu bền và bàn giao. Xác minh mount trước, không suy luận tốc độ từ tên `/workspace`.
- Giữ đủ JSON, bản dịch, visualization, bank hợp lệ và thông tin tái lập. Dựng review Markdown một lần
  cuối chapter khi đã có cơ chế hỗ trợ; không tự ý tắt visualization của extract.
- Kiểm tra tại ranh giới cần thiết: input nhập vào, checkpoint được commit/tái sử dụng, kết quả bàn giao.
  Không lặp full hash/DAG vì mỗi lệnh status hoặc mỗi file review.
- Một GPU: mặc định một hàng đợi inference; có thể chồng lấp truyền dữ liệu với chapter tiếp theo
  nếu không gây tranh chấp disk/bandwidth, không đổi thứ tự cập nhật bank phụ thuộc.
- Trạng thái `Running`, PID tồn tại, exit code 0 hay `failures: 0` riêng lẻ **không chứng minh** kết quả đúng.
- Backup xác minh xong thì kết thúc pod ngay, nếu người dùng đã cho phép. Không xóa trước khi bàn giao.
- Tuân thủ sandbox, quyền truy cập và skill Runpod/context-mode; chính sách tốc độ không cho phép lách quyền.

## 2. Những vấn đề thực tế đã gặp

Các quan sát dưới đây thuộc phiên Iris Zero/Quintuplets ngày 09/10/2026, không phải benchmark phổ quát.

| Vấn đề / dấu hiệu | Nguyên nhân hoặc bằng chứng | Phương án ưu tiên |
| --- | --- | --- |
| Import RF-DETR/Transformers vượt timeout 120 giây | Env có rất nhiều file nhỏ nằm trên mounted volume chậm | Dùng `MANGA_EXECUTION_ROOT` trên local disk; giữ nguyên lock/signature, không cài lại tùy tiện |
| GPU 0% nhưng stage chưa kết thúc | Có lúc process đang đọc mask/metadata/checkpoint, không chạy kernel GPU | Kiểm tra stage, counter, process và một mẫu I/O; xử lý I/O, không vội đổi GPU |
| Export đã commit nhưng vẫn chậm ở review | `completed()` → `input_hash()` đệ quy DAG, nhiều nhánh kiểm tra lại cùng layout/mask | Memoize **trong một verification pass**, dựng review cuối chapter; chưa được triển khai ở thời điểm viết |
| Analyze báo hoàn thành nhưng nhiều fallback | Context 8192 không đủ; journal có lỗi context budget, Iris 1 ban đầu có 430/434 style `unknown` | Mặc định hiện tại 16384; kiểm tra request đại diện lớn, chặn hoàn thành giả, fork reanalyze và tái dùng visual checkpoint |
| Mô tả “đã chạy GPU” không rõ ràng | `/api/ps` có thể gây hiểu nhầm dung lượng model/draft; tên pod không chứng minh inference | Một phép CUDA + inference thật, đối chiếu worker log; không lặp smoke test mỗi chapter |
| Atomic đổi source bị `Errno 18` | `os.replace` qua hai filesystem/mount khác nhau | Stage/swap trong cùng filesystem; nếu phải copy qua mount, dùng recovery/locking đúng controller, không đổi live tree thủ công |
| Hot-sync source bị hiểu như image mới | Pod boot Docker Hub image cũ rồi nhận source bundle mới | Ghi riêng image tag/digest và source bundle hash; release tiếp theo bake source đã chốt |
| Upload/download nhiều lượt và kiểm tra qua lại | Nhiều SSH/API round-trip, truyền nhiều file nhỏ, quét toàn bộ output nhiều lần | Gói theo corpus/chapter, manifest một lần, checkpoint bất biến; monitor bằng state nhỏ |
| Log cũ có `Interrupted` hoặc state còn “running” | Log append qua nhiều attempt; process đã dừng nhưng record chapter chưa cập nhật | Đối chiếu attempt/run ID + PID/start identity + manifest mới nhất; không đọc một dòng log cũ rồi kết luận |
| Thuê GPU trong khi sửa môi trường/tài liệu | Công việc offline nằm trên đường tính phí | Chuẩn bị trước, tiếp tục job đã sẵn sàng trong lúc làm việc khác; nếu buộc phải chờ, backup và xử lý pod theo quyền đã được cấp |

Docker image và FUSE **không phải hai cách deploy thay thế nhau**: image chứa phần mềm khởi động;
FUSE là đặc tính filesystem có thể nằm dưới volume. Dùng Docker Hub vẫn có thể gặp volume chậm.
Không gọi pod volume của phiên này là “network volume”: phiên quan sát dùng pod-local volume,
không gắn network volume độc lập.

## 3. Phân biệt tính năng hiện có với việc còn phải triển khai

Đối chiếu source tại thời điểm viết; agent phải đọc lại code/help nếu repository đã đổi.

| Hạng mục | Trạng thái hiện tại |
| --- | --- |
| `MANGA_EXECUTION_ROOT` | Đã có trong `manage.py`: chuyển Python/env/uv cache và binary uv/Ollama khỏi runtime volume; mặc định vẫn là runtime nếu không đặt |
| `Dockerfile.runpod.fast` / `setup --prepare-only` | Chuẩn bị dependencies trên CPU trước khi thuê GPU; marker ghi CUDA chưa xác minh. Setup/doctor trên pod vẫn phải kiểm tra CUDA thật |
| Models, input, output | Vẫn ở runtime volume trong cấu hình hiện tại; biến trên **không** tự chuyển toàn bộ workload sang SSD |
| Context mặc định 16384 | Đã đặt trong `configs/pipeline.json`; không áp dụng hồi tố vào config snapshot của run cũ |
| Batch tuần tự/resume + chặn lỗi context budget | Đã có trong `deployment/runpod/batch.py`; thay num_ctx tạo nhánh reanalyze, không sửa checkpoint cũ |
| Monitor gọn | Đã có `watch.py`; đọc state/manifest, không chạy lại pipeline hoặc hash toàn bộ masks |
| Fetch `outputs`, `banks`, `audit` | Đã có trong `sync.py`; ZIP nén mức 1 + SCP, đích mới không ghi đè; hiện fetch theo toàn bộ loại dữ liệu |
| Cache DAG trong một verification pass | Đã triển khai và có regression test: mỗi artifact/mask một lần trong pass; public read mới kiểm tra lại và phát hiện tampering |
| Defer review cuối chapter / `MANGA_DEFER_REVIEWS` | Đã triển khai: batch đặt biến `1`, dựng review một lần sau inference; CLI thông thường không đổi, visualization vẫn giữ |
| Worker/model sống xuyên chapter | **Chưa có bảo đảm**: batch hiện gọi subprocess pipeline cho từng chapter |
| Output fetch chọn chapter / incremental | Đã có `fetch outputs BASE --story KEY --chapter KEY` và `collect.py`: tải/xác minh chapter đã commit trong lúc chapter sau chạy. SCP archive hiện tại vẫn không resumable |
| Mirror toàn bộ input/output sang SSD rồi checkpoint bền | **Chưa triển khai tự động**; cần giữ đúng source path, manifest và recovery contract |
| Tự cảnh báo/teardown sau 10 phút | Chính sách cho agent; **không phải watchdog tự động hiện có** |

Thứ tự tối ưu: DAG validation một-pass + defer review (đã có) → truyền theo chapter/resume
→ local working-set có checkpoint bền → worker/model reuse. Không dừng batch khỏe chỉ để làm refactor.

## 4. Chuẩn bị trước khi thuê GPU

1. Lập plan bất biến: story/chapter, source folder, số trang, bytes, hash, ngôn ngữ, thứ tự chạy,
   output bắt buộc và backup destination. Không dedup ảnh hay chọn bản dịch khác khi chưa được yêu cầu.
2. Kiểm tra auth/SSH, quyền Docker Hub, model gated license nếu có và quyền teardown. Không in token/key.
   Chuẩn bị quyền network/sandbox cần thiết trước; chỉ xin prefix có phạm vi hẹp, không xin shell tùy ý.
3. Khi được phép test, chạy kiểm tra liên quan; chốt source/config và boot image đã ghim. Chỉ build/push image khi cần, trên CPU trước khi thuê; không dùng GPU thuê để build Docker.
   Image ổn định nên bake source + Linux dependencies tương thích; khóa Python/CUDA và env visual riêng.
4. Chuẩn bị input archive/manifest trên PC. Ảnh JPG/WebP/PNG đã nén: tránh nén lại CPU nặng để tiết kiệm
   lượng bytes nhỏ. Không chuyển định dạng/lossy vì mục tiêu tốc độ nếu chưa được cho phép.
5. Chọn chiến lược model theo tổng thời gian: bake model tăng image pull; cache bền cần storage;
   tải một lần cần bandwidth. Không mặc định nhét tất cả model vào image hoặc lập thêm dịch vụ trả phí.
6. Xem trước dung lượng: env + model + input + peak output + archive tạm + copy checkpoint + headroom.
   Đo free space thực tế trước khi copy. Container 30 GB của phiên này từng chỉ còn khoảng 9 GB;
   không thể vì “SSD nhanh” mà sao chép mọi thứ vào đó một cách mù quáng.
7. Chốt GPU/region theo VRAM và **thời gian hoàn tất**, không theo giá/giờ đơn lẻ. Giá/capacity/billing
   phải đọc từ công cụ hiện tại trước khi tạo tài nguyên; dữ liệu ngày 09/10 không phải giá cam kết.
8. Báo giá/giờ, storage liên quan, ETA có khoảng sai số và điều kiện kết thúc. Nếu chưa có ngân sách
   hoặc quyền chuyển region/tăng cấu hình, hỏi trước khi mở rộng tài nguyên trả phí.

## 5. Storage và băng thông: giảm số lượt, giảm file nhỏ

### Đường chạy chính

- Xác định local disk bằng mount/filesystem và một phép đo nhỏ có giới hạn; không chạy benchmark dài.
- Chạy Python/env trên local disk. Ví dụ đã dùng: `MANGA_EXECUTION_ROOT=/tmp/manga-execution`.
  Phải truyền **cùng biến** vào setup, doctor và detached batch; env tạo ở một root không tự có ở root khác.
- Dữ liệu cần sống qua terminate phải được đưa ra ngoài pod. `/tmp` và container disk không phải backup.
- Khi local mirror được triển khai, hash tại ingress rồi đọc local; checkpoint commit phải đồng bộ bền
  trước khi thông báo chapter hoàn thành. Không sửa `source.folder` hay di chuyển run bằng tay để lách hash.
- Masks/JSON/file nhỏ là working-set ưu tiên SSD. Đừng tải toàn bộ model/cache về PC như “output”.

### Truyền dữ liệu

- Nếu đã có toàn bộ corpus ở pod, không upload lại. So sánh manifest, chỉ gửi phần thiếu/thay đổi.
- Upload toàn bộ input sớm; nếu upload dài, bắt đầu chapter đầu đã đầy đủ và tiếp tục nạp chapter sau.
  Không dùng GPU xử lý folder đang upload dở; dùng archive hoàn chỉnh + ready marker/atomic publish.
- Gộp file thành archive theo chapter/corpus; tránh mở một SSH/SCP session cho mỗi ảnh hoặc JSON.
- Giữ connection/driver lâu sống khi có thể. Chỉ log bytes đã truyền, tốc độ trung bình và ETA, không dump filelist.
- Mặc định một transfer lớn để tránh tranh chấp; chỉ tăng lên hai khi đo thấy tổng throughput tăng
  và GPU không bị đói I/O. Concurrency 4–8 cho API/metadata độc lập không đồng nghĩa 8 luồng tải file lớn.
- Tải chapter đã commit trong lúc GPU chạy chapter kế tiếp nếu reader không tranh chấp volume đáng kể.
  Archive phải chụp tập artifact bất biến; không archive file đang ghi hoặc bank mutable khi chưa lock/snapshot.
- Dùng filtered fetch hoặc `collect.py` cho từng chapter đã commit. Không chạy unfiltered
  `fetch outputs` sau mỗi chapter vì sẽ đóng gói/tải lại cả cây nhiều lần.
- Transfer lâu phải detached/background có PID/log và kiểm tra exit; timeout của tool không được giết transfer.
  Retry có giới hạn; ưu tiên cơ chế resume đã xác nhận có, không mặc định SCP sẽ tiếp tục file dở.
- Không thêm object storage, CPU pod, volume mới hay region mới khi chưa có quyền/chi phí đã chốt.
  Nếu đã có đích ngoài pod được phép và rẻ hơn để staging, có thể dùng nó để rút ngắn thời gian giữ GPU.

## 6. Kiểm tra ít nhưng đúng vị trí

### Trước chạy: một preflight gọn

Gộp thành một lượt: SSH đúng pod, source bundle/config đúng, input count/hash ingress,
env signature, assets cần thiết, CUDA thật và request inference đại diện. Không lặp cùng doctor
sau mỗi sync chỉ thay tài liệu hoặc sau mỗi chapter khi env/model không đổi.

Request đại diện phải bao gồm đoạn nhiều context: input + reserve đầu ra phải vừa num_ctx.
Không đợi chạy cả chapter mới thấy tất cả reply bị fallback. 16384 là mặc định đã giải quyết lỗi
quan sát, không phải bảo đảm cho mọi prompt tương lai; nếu phải tăng thêm, kiểm tra VRAM và latency.

### Khi chạy: checkpoint và monitor, không audit liên tục

- Source hash ingress và artifact hash/receipt được giữ nguyên. Stage commit và resume phải kiểm tra dependency.
- Refactor validator theo cache **chỉ trong một pass** trên cùng snapshot/lock: mỗi artifact/mask xác minh
  một lần, nhánh DAG dùng lại kết quả. Public read/pass mới phải phát hiện dữ liệu bị sửa sau pass trước.
  Không cache vĩnh viễn và không tắt checksum để “chạy nhanh”.
- Review đọc cùng pass rồi render một lần cuối chapter; status chỉ đọc state/counter/timing gần nhất.
- Dùng counters theo page/batch/utterance khi có; tránh quét tất cả attempts/PNG mỗi vài giây.
- Lỗi context budget/provider hệ thống không được coi là semantic `unknown` hợp lệ. Journal và coverage
  cần được xét trước khi đánh dấu chapter complete. `unknown` thật có evidence/review status vẫn giữ nguyên.

### Bàn giao: một verification đầy đủ, không ba lần cùng kiểm tra

Gộp audit cuối: tất cả chapter/run ID đúng plan; tổng số trang; stages complete; provenance Koharu;
speech style/evidence/source/status/source_boxes còn trong JSON; đủ natural/localized translations;
không provider/context failure bị che; bank phù hợp và archive/copy toàn vẹn.
ZIP CRC hỗ trợ phát hiện hỏng archive nhưng không thay manifest/provenance hoặc chứng minh đủ chapter.

## 7. Chạy batch và khôi phục nhanh

- Một detached driver có plan/state/log/PID identity, một GPU inference queue, locks chống chạy trùng.
- Bắt đầu ngay khi preflight tối thiểu đạt; không cần đợi nhiều bảng chẩn đoán và báo cáo đẹp.
- Giữ model/server nóng khi đã có cơ chế; worker reuse xuyên chapter là refactor riêng, không tăng nhiều
  process MAGI/Koharu/Gemma cùng lúc để cố dùng 100% GPU nếu VRAM/bank chưa chứng minh an toàn.
- Crash/network disconnect: tìm checkpoint mới nhất của đúng plan rồi resume. Không xóa output hoặc chạy
  lại cả truyện. Run cũ còn được giữ làm audit, không tính nhầm nó thành kết quả hợp lệ.
- Config thay đổi làm analysis cũ không hợp lệ: fork reanalyze theo controller, tái dùng visual checkpoint
  tương thích; không sửa trực tiếp snapshot/manifest để đánh dấu completed.
- Source sync khi job giữ lock phải dừng ở ranh giới an toàn hoặc chờ chapter; không thay code dưới process.
- Không restart/recreate pod như phép thử đầu tiên. Chẩn đoán ngắn có bằng chứng; cần quyền đúng tài nguyên
  khi restart/recreate, tăng giá, đổi GPU hoặc xóa dữ liệu ngoài phạm vi đã được người dùng cho phép.

## 8. Tiến độ mỗi 2 phút và ngưỡng 10 phút

Một mẫu đọc gọn trả: pod/job identity, chapter hiện tại, stage, số chapter hoàn thành, page/utterance
counter nếu có, elapsed, thời điểm tiến độ thực tế gần nhất, transfer bytes/ETA nếu đang tải.
Agent cập nhật người dùng mỗi **2 phút**; lỗi thực tế thì báo ngay. Không gọi API/SSH dày để tạo cảm giác hoạt động.

Tiến độ thực tế là counter tăng, checkpoint mới commit, batch inference hoàn tất hoặc bytes transfer tăng.
Heartbeat, mtime log chỉ in lại status, PID sống và state “running” không đủ. Stage dài còn tăng counter
không bị coi là stalled; nếu thiếu counter, lấy một mẫu process/worker evidence có giới hạn rồi cải thiện instrumentation.

Sau **10 phút không có tiến độ đo được**:

1. Chẩn đoán một lượt: process identity, stage/lock, log mới của đúng attempt, GPU/VRAM, disk free,
   I/O/transfer delta. Không rerun full doctor/hash toàn corpus hoặc cài lại package theo phản xạ.
2. Chỉ ra nhóm nghẽn: storage/file nhỏ, network, CPU, VRAM/model, context/provider, lock/driver.
3. Sửa tối thiểu trong quyền đã cấp và resume checkpoint; thông báo thời gian/chi phí tác động.
4. Nếu phải chờ người dùng/quyền hoặc công việc offline dài, đừng giữ GPU vô thời hạn. Backup phần đã
   commit, rồi stop/terminate theo quyền và đặc tính storage đã xác minh; nếu chưa được phép, xin quyết định ngay.

10 phút là ngưỡng cảnh báo/chẩn đoán, **không phải lệnh xóa pod tự động**. Không terminate vì GPU 0% một mẫu.

## 9. Chi phí và kết thúc phiên

Tối ưu tổng thời gian thuê: startup + setup/pull + upload + inference + I/O/validation + packaging/download.
Chi phí ước tính = giá GPU × giờ giữ pod + storage/transfer/dịch vụ theo giá hiện hành.
Đừng công bố con số này như hóa đơn cuối cùng nếu chưa đọc billing.
GPU rẻ nhưng env/import/download kéo dài có thể tốn hơn GPU nhanh; tăng GPU không chữa được latency filesystem.

Trước teardown:

1. Tải output + bank + audit đến folder mới; không ghi đè backup cũ. Không backup token, SSH key hoặc cache model
   như dữ liệu đầu ra. Lock/signature của execution root local phải được lưu riêng nếu audit collector chưa gồm chúng.
2. Xác minh local đủ chapter và coverage, checksum/CRC và manifest inventory phù hợp. Ghi base image,
   source bundle hash, dependency locks, effective config, run IDs và checkpoint superseded.
3. Chỉ khi copy ngoài pod đã đạt, thông báo phần storage sẽ mất và terminate **đúng pod task sở hữu**,
   nếu đã được cho phép. Không xóa volume chung hoặc pod khác. Xác minh resource đã biến mất bằng readback.
4. Tắt Ollama hoặc dừng process không phải kết thúc phí pod. Lệnh delete có response khó parse phải
   readback trước khi retry; không tự tạo lại tài nguyên vì một lỗi hiển thị response.
5. Bàn giao link output/backup/report và trạng thái pod; không nói “xong” khi chỉ mới xếp hàng download.

Nếu backup thất bại, giữ tài nguyên cần khôi phục, báo lỗi và chi phí tiếp diễn; không xóa dữ liệu để đạt mục tiêu rẻ.

## 10. Quy tắc ưu tiên chạy hết, sửa lỗi sau (chỉ thị 2026-10-09)

Khi chạy workload Runpod, không tự sửa source code, pipeline, cấu hình thuật toán,
ngưỡng hoặc prompt để chữa lỗi giữa phiên. Không hot-sync bản sửa, không build/push
image mới và không lặp retry vô hạn. Chỉ sửa khi người dùng cấp yêu cầu riêng sau đó.

Ưu tiên thử chạy toàn bộ chapter đã yêu cầu trước. Nếu batch driver dừng ở một chapter
lỗi, dùng các CLI hiện có để chạy lần lượt những chapter còn lại, không sửa driver
và không đánh dấu chapter lỗi thành completed. Giữ nguyên checkpoint, journal và
manifest của mỗi chapter, kể cả partial/failed. Không bỏ target lỗi để làm đẹp coverage.

Ghi lỗi vào một file Markdown trong workspace: story/chapter/run ID, bước lỗi,
exit code, số target lỗi, thông báo nguyên văn, đường dẫn log và phần kết quả còn thiếu.
Ghi rõ **lỗi sẽ được sửa sau khi chạy hết toàn bộ**, không sửa trong phiên chạy này.
Các thay đổi đã có trước chỉ thị này được giữ nguyên và ghi provenance; không rollback
hoặc thêm bản sửa khi workload đang chạy.

Sau khi thử hết chapter, tải cả output hoàn chỉnh lẫn partial/failed, bank và audit.
Người dùng xác nhận partial là đủ để tải xuống: tải mỗi chapter đã ngừng ghi ngay
trong lúc chapter sau chạy, không chờ 100% coverage hoặc đợi hết batch mới bắt đầu tải.
Không đóng gói một run vẫn đang được ghi; review bổ sung sau phải được tải riêng nếu
bản sao sớm chưa bao gồm nó.
Xác minh tính toàn vẹn bản sao và đủ dữ liệu thực tế sinh ra; báo coverage thiếu như lỗi,
không coi là thành công. Không để validator đòi coverage 100% chặn việc cứu dữ liệu lỗi.
Sau khi backup ngoài pod được xác minh, terminate đúng pod đã được phép để ngừng phí.
Lỗi hạ tầng, thiếu dung lượng hoặc backup không an toàn vẫn cần báo ngay; không xóa
pod khi dữ liệu chưa được bảo toàn.

Quy tắc này ưu tiên hơn các hướng dẫn trước về sửa lỗi/retry trong khi batch đang chạy.

## 11. Công cụ thực tế trong repository

Các lệnh dưới đây chạy trên PC từ project root; chọn interpreter của venv thực tế.
Không lặp setup/prefetch/doctor nếu signature và assets không đổi. `MANGA_EXECUTION_ROOT` là biến remote,
không được hiểu là chỉ set trên Windows rồi mọi SSH command tự nhận.

```powershell
& '.\venv\Scripts\python.exe' -B deployment/runpod/sync.py --help
& '.\venv\Scripts\python.exe' -B deployment/runpod/sync.py sync
& '.\venv\Scripts\python.exe' -B deployment/runpod/sync.py input 'D:\download\EN-data\<chapter>' --chapter '<input-key>'
& '.\venv\Scripts\python.exe' -B deployment/runpod/watch.py '<batch-job-id>' --wait 45
& '.\venv\Scripts\python.exe' -B deployment/runpod/sync.py fetch outputs 'outputs\<new-backup-folder>'
& '.\venv\Scripts\python.exe' -B deployment/runpod/sync.py fetch banks '.runpod\backups\<new-bank-folder>'
& '.\venv\Scripts\python.exe' -B deployment/runpod/sync.py fetch audit '.runpod\backups\<new-audit-folder>'
```

`<...>` là placeholder, không chạy nguyên văn. Driver Linux `batch.py` nhận plan JSON và `--state`;
launch detached qua cơ chế SSH/controller hiện có, giữ env local execution nhất quán và chống trùng PID.
MCP dùng cho infra nếu đã kết nối, SSH/controller dùng cho job và transfer; không bắt đăng nhập lại
một lane đang hoạt động chỉ để dùng thêm công cụ. Đọc help/schema hiện tại trước khi dùng option mới.

Hướng dẫn setup cũ: [runpod_reusable_setup_vi.md](runpod_reusable_setup_vi.md).
Các khuyến nghị cũ về đặt mọi env trên volume hoặc chạy smoke test lặp phải được đọc trong bối cảnh
chính sách batch nhanh này. Giá/GPU/storage của tài liệu cũ không được coi là quyết định triển khai mới.
