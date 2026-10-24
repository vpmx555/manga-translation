# Bàn giao: sửa ID analyze v6 và kiểm chứng Bocchi 3 chapter trên Runpod

## 1. Mục tiêu và quyền đã có

**Cập nhật phạm vi từ người dùng:** lần chạy mới gồm Bocchi 1–3, Iris Zero
1–3 và Witchriv 1–3 (9 chapter). Dữ liệu vẫn lấy từ `D:/download/EN-data`.
GPU ưu tiên RTX 5090; không có stock thì RTX 4090, rồi GPU thấp hơn đủ VRAM.
Đọc giá/stock hiện tại và báo lựa chọn trước create. Không drop một bộ truyện
hoặc dùng giá/stock cũ. Mỗi story có bank sạch riêng, cập nhật chapter tuần tự.
Tên folder và số trang của Iris/Witchriv sẽ ghi sau khi inventory thực tế.

**Yêu cầu tự vận hành mới:** một lệnh khởi động phải có sẵn queue đủ 9 chapter;
sau khi Runpod chạy không cần agent hay lệnh thêm để chạy chapter/review/backup.
Dữ liệu phải tự đồng bộ về PC bằng công cụ/dịch vụ độc lập, không phụ thuộc
collector do agent gọi. Cần backup cả checkpoint đang sinh ra để giảm rủi ro
khi account hết số dư. PC phải bật và có mạng để nhận; không được hứa không mất
dữ liệu nếu pod dừng trước khi một file được commit/truyền xong. Cơ chế dự kiến:
Syncthing daemon native (PC receive-only, pod send-only), độc lập pipeline;
orchestrator/queue detached và bước verify/teardown được chuẩn bị trước launch.
Đây là yêu cầu triển khai, chưa xác nhận rằng native sync đã được cài/kiểm chứng.

Inventory đã xác định: Bocchi 31/24/21; Iris Zero 44/29/34 ở
`D:/download/EN-data/Iris Zero/EN/Chapter N`; Witchriv 81/60/68 ở
`D:/download/EN-data/Witchriv/EN/Chapter N`. Tổng 392 trang, 139.326.100 bytes ảnh.

Người dùng đã chốt thiết kế qua `$grill-me`, yêu cầu triển khai sửa lỗi ID, sau
đó chạy lại đủ Bocchi chapter 1–3 trên Runpod. Ngày 12/10/2026, người dùng yêu
cầu bản bàn giao này được viết trước khi tiếp tục triển khai/chạy để agent khác
có thể tiếp nhận nếu session bị ngắt. Không cần xin xác nhận lại cho cùng tác vụ.

Authorization này chỉ áp dụng cho tác vụ hiện tại, không cho tác vụ Runpod khác.
Không chạy model/pipeline trên PC hoặc chạy CPU tests dài. Kiểm tra static và
fixture nhỏ phục vụ sửa code; full pipeline/model phải chạy trên Runpod theo
yêu cầu vừa nêu. Không provision trước khi source, locks và ingress đã chuẩn bị.

Đọc đầy đủ `docs/news/runpod_agent_fast_policy_vi.md` trước mọi hoạt động Runpod.
Áp dụng skills Runpod/context-mode. Không in token/private key; không nhúng
credential vào output/handoff. Nguồn ảnh mặc định trên PC:
`D:/download/EN-data/Bocchi/Chapter 1`, `Chapter 2`, `Chapter 3`.

## 2. Thiết kế đã chốt

| Quyết định | Lựa chọn người dùng |
| --- | --- |
| Target lỗi sau repair | Giữ `unresolved`, vẫn có thể đi tiếp để dịch/dàn trang, đánh dấu review; không coi lỗi provider/contract là semantic unknown hợp lệ |
| Kiến trúc analyze | Giữ một request cho role/style/speaker/names; khóa candidate theo từng utterance |
| Cứu kết quả | Giữ target vượt qua validator độc lập; target lỗi chỉ dùng dữ liệu nguồn, không dùng semantic của phản hồi lỗi |
| Retry | Một lượt repair riêng target lỗi sau request đầu; không nhân thêm retry batch và retry target |
| Render unresolved | Render theo vị trí OCR cả khi chưa rõ thoại/SFX; phải lưu review reason và phạm vi ảnh bị tác động |
| Xưng hô unresolved | Dùng ngữ cảnh/thông tin đã xác nhận, không suy diễn thân mật/thứ bậc; không gom `speaker=null` thành cùng một người |
| GPU geometry | Người dùng yêu cầu chuyển compute nặng sang GPU; đọc/ghi ảnh và polygon CPU vẫn phải được báo rõ |

Các chi tiết triển khai thông thường: tách coverage artifact/render và coverage
semantic; không báo resolved hoàn toàn khi còn unresolved. Dữ liệu hỏng,
checksum sai, lỗi CUDA/infra/provider hệ thống vẫn là lỗi thực, không fallback
thành thành công. Giữ validator, thresholds, OCR, font AstroCity, line spacing
1.6 và H-center đã chốt. Không đưa unresolved vào cập nhật danh tính/person bank.

## 3. Lần chạy cũ và bằng chứng cần giữ

- Batch cũ: `bocchi-v6-localdisk-20261012`, 31/24/21 trang, tổng 76.
- Pod cũ của tác vụ: `h0hfjlcram3mxe`, A40 CA-MTL-1, đã terminate (204;
  readback 404). Không mở lại/không giả định pod này còn dữ liệu.
- Backup: `outputs/runpod-bocchi-v6-localdisk-20261012`.
- Run IDs: `20261012T130843Z-131118bd`, `20261012T140128Z-bcde64b7`,
  `20261012T145312Z-34cb9ac6`.
- Cả ba completed layout/extract/bubble_geometry/normalize/scan; analyze partial.
  Thành công 441 target, thất bại 297 target trong 102 nhóm. Chưa identity,
  export, inpaint, translate, typeset.
- 96/102 nhóm lỗi cuối là `Name candidate coverage changed`. Rejected cache có
  211 phản hồi coverage, 7 effect/name, 2 sentinel/suffix và 2 output truncation.
- Root cause: schema mention dùng enum của cả batch trong khi validator yêu
  cầu candidate đúng theo từng utterance; `<no-candidates>` được model xuất ra.
- `banks/bocchi` local là cluster bank đã nhập từ lần cũ; không có person bank
  mới vì identity chưa chạy. **Lần kiểm chứng mới phải dùng bank sạch**, các
  chapter sau chỉ dùng bank sinh ra trong chính lần mới, không upload bank local.
- Báo cáo: `bocchi-v6-analyze-diagnosis-20261012.md` cùng thư mục này.

## 4. Trạng thái code tại lúc viết bàn giao

Đã sửa nhánh GPU geometry, chưa chạy tests/GPU/benchmark:

- `v6/geometry_backend.py`: backend CuPy rõ ràng, không CPU fallback khi yêu
  cầu CUDA; private allocator theo page và kernel preflight nhỏ.
- `v6/visual.py`, `visual/geometry.py`: morphology/label/EDT/mask/edge coordinates
  dùng array backend; polygon, PNG, checksum vẫn CPU.
- `v6/stages.py`: lấy device từ pipeline config, ghi backend receipt, policy
  `visual-groups-v6.2` để không giả định cache CPU cũ là kết quả GPU.
- `deployment/runpod/requirements.in`: thêm `cupy-cuda12x==14.2.0`.
- `manage.py`: `CUPY_CACHE_DIR` local và geometry preflight trong doctor.
- `tests/test_v6_geometry_backend.py`: fixture parity; chưa chạy.
- AST của 6 file sửa và `git diff --check` đã đạt. Chưa xác nhận CUDA parity.

Chưa sửa semantic ID/unresolved lúc tạo bàn giao. Cần cập nhật mục tiến độ ở
cuối file khi đã sửa/test/launch; không diễn giải phần chuẩn bị thành đã chạy.

Workspace đang có nhiều thay đổi từ trước, gồm typeset/translation/config và
diagnostic scripts. Không reset/revert, không ghi đè các thay đổi không thuộc
tác vụ. Kiểm tra `git status` và diff trước khi sửa.

## 5. Các điểm code phải đối chiếu

| File | Trách nhiệm |
| --- | --- |
| `src/manga_pipeline/v6/semantic.py` | Scan, wire schema, batch analyze, validation, target checkpoint, person state |
| `src/manga_pipeline/providers/ollama.py` | JSON inference/cache/rejected responses/retry/token budget |
| `src/manga_pipeline/v6/persons.py` | Name forms, honorific, person registry; giữ MAGI bank bất biến |
| `src/manga_pipeline/pipeline.py` | Stage dispatch/export/commit/dependency |
| `src/manga_pipeline/v6/translation.py` | Translation payload, context/xưng hô, output review |
| `src/manga_pipeline/v6/typeset.py` | Selection/render, needs_review, provenance |
| `deployment/runpod/v6_contract.py` | Verification; phân biệt unresolved và coverage thiếu thật |
| `deployment/runpod/batch_v6.py` | Tuần tự 3 chapter, review, publication và final state |
| `deployment/runpod/local_v6.py` | Offline prepare, ingress, bootstrap, launch/watch/collect |
| `src/manga_pipeline/storage/checkpoints.py` | Stage archive, immutable publication, inventory/restore |

## 6. Thứ tự thực hiện tiếp

1. Sửa wire mention thành object keyed candidate ID **riêng từng utterance**;
   câu không có candidate chỉ trả object rỗng. Normalize về public array sau
   validation. Khóa metadata span/utterance từ input, không để model invent.
2. Validate từng target độc lập, giữ target hợp lệ; repair riêng target lỗi
   một lượt với missing/extra/duplicate IDs cụ thể. Không lấy speaker/style/
   name từ target bị từ chối. Giữ rejected payload và retry receipts.
3. Tạo unresolved record có source/provenance/error/status riêng; bảo toàn số
   target. Không trộn lỗi với semantic unknown hoặc commit danh tính từ chúng.
4. Tích hợp export/translation/typeset và checker/batch statuses để vẫn tạo
   đủ artifacts cho unresolved đã chốt, luôn có review flags. Tránh context
   của nhiều `speaker=null` bị coi là một người.
5. Sửa launcher ingress: **thư mục input tồn tại nhưng rỗng không phải ready**.
   Xác minh receipt/count/hash, giải nén từng folder staging trên local disk,
   publish atomic. Lần trước lỗi này làm cả ba chapter fail trước khi có run.
6. Static check và fixture nhỏ: schema/per-row ID, invalid ID/cross-target,
   valid siblings, repair limit, honorific/effect, unresolved output/render,
   unknown-speaker context, GPU backend guard/parity nếu GPU có sẵn. Không
   chạy full pipeline/model trên PC hoặc thử đi thử lại content cases local.
7. Chuẩn bị Linux dependency locks mới trên CPU, source bundle/config/input
   manifest mới. Không dùng lại main lock của batch cũ: CuPy đổi signature.
8. Đọc giá/stock live, chọn GPU CUDA phù hợp VRAM. Quote cost trước create;
   tạo **pod mới task-owned** với image đã có, không build/push image fast lớn.
9. Preflight gộp rồi chạy ngay cả 3 chapter. Thu output chapter đã ngừng ghi
   trong lúc chapter sau chạy. Nếu lỗi mới, thử hết workload trước, không sửa
   source/prompt/threshold giữa inference.
10. Tải complete lẫn partial, banks, audit, locks, config/source provenance;
    verify một lượt đầy đủ; nhập bank hợp lệ và backup bank local cũ trước swap.
    Terminate đúng pod mới sau backup verified, readback resource đã biến mất.

## 7. Controller và lệnh đã có

Các lệnh dưới đây chạy ở `D:/AIP491/manga-translate-v2`, interpreter
`venv/Scripts/python.exe`. `<...>` là placeholder: thay bằng giá trị đọc từ
plan/infra receipt hiện tại, không chạy nguyên văn.

```powershell
& '.\venv\Scripts\python.exe' -B deployment/runpod/local_v6.py --help
& '.\venv\Scripts\python.exe' -B deployment/runpod/local_v6.py prepare `
  'D:\download\EN-data\Bocchi\Chapter 1' --batch '<new-batch>' --story bocchi `
  --chapter chapter-001 `
  --additional-chapter chapter-002 'D:\download\EN-data\Bocchi\Chapter 2' `
  --additional-chapter chapter-003 'D:\download\EN-data\Bocchi\Chapter 3' `
  --locks '<new-linux-locks-folder>'
& '.\venv\Scripts\python.exe' -B deployment/runpod/sync.py connect `
  --host '<live-direct-ssh-host>' --port <live-port> `
  --key '<existing-private-key-path>' --pod-id '<new-pod-id>'
& '.\venv\Scripts\python.exe' -B deployment/runpod/local_v6.py launch `
  --plan '.runpod/v6/<new-batch>/plan.json' --pod-id '<new-pod-id>'
& '.\venv\Scripts\python.exe' -B deployment/runpod/local_v6.py watch `
  --plan '.runpod/v6/<new-batch>/bound-plan.json'
& '.\venv\Scripts\python.exe' -B deployment/runpod/local_v6.py collect `
  --plan '.runpod/v6/<new-batch>/bound-plan.json' `
  --destination 'outputs/<new-backup>' --poll 45
```

Source/config thay đổi sau prepare thì phải prepare batch ID mới. `prepared()`
kiểm tra bundle/config hash; không bypass guard hoặc sửa plan để lách nó.
Đọc help/code mới nếu controller được sửa. `.runpod/connection.json` hiện có
thể vẫn trỏ pod đã terminate; không dùng như bằng chứng pod đang hoạt động.

Image boot cũ đã dùng thành công: `vpmx555/manga-translate-v2:v5.0.2-fast-code.1`.
Xác minh image/registry hiện tại trước create. Code mới được sync bằng bundle;
ghi riêng image digest và source bundle hash, không gọi hot-sync là image mới.
Working paths: `/opt/manga-work/project`, `/opt/manga-work/execution`,
`/opt/manga-work/runtime`. Volume durability: `/workspace/manga-runtime/local-v6`.
Disk sizing cũ: container 80 GB, pod volume 100 GB; phải đo free space thực tế.

Lượt trước Windows SCP upload chậm. Có helper WSL legacy SCP ở
`.runpod/v6/bocchi-v6-localdisk-20261012/launch_wsl.py`; đây là helper gắn chặt
batch/pod cũ, **không chạy lại nguyên văn**. Nếu dùng lại kỹ thuật, tạo helper
cho batch mới, kiểm tra đích và bảo vệ key WSL bằng file tạm mode 600 rồi xóa.
Không để upload long bị timeout tool giết; detached/background có PID/log/exit.

## 8. Resume khi session ngắt

Trước hỏi người dùng, tìm state/handoff trong context-mode timeline và các file:

- File bàn giao này và mục tiến độ bên dưới.
- `.runpod/v6/<batch>/plan.json`, `bound-plan.json`, `infrastructure.json`.
- Receipt/log/PID bootstrap, driver và collector của đúng batch.
- Remote `jobs/state.json`, `launch.json`; kiểm tra PID + start identity.
- Local `collection.json`, `final-state.json`, `verification.json`.

Nếu driver đang chạy đúng identity: chỉ monitor/collect, không launch trùng hoặc
hot-sync. Nếu driver dừng: tìm checkpoint đúng plan, giữ attempts cũ rồi resume
qua controller. Nếu contract/config thay đổi: fork/reanalyze đúng cơ chế, không
sửa snapshot hay manifest để giả completed.

Monitor nhẹ bằng state/checkpoint/counter/transfer bytes. Báo tiến độ định kỳ;
10 phút không có tiến độ đo được thì chẩn đoán một lượt có giới hạn. GPU 0%,
PID tồn tại hay exit code 0 không tự chứng minh thành công. Không giữ GPU chờ
agent khác, tài liệu hoặc sửa offline dài; backup rồi stop/terminate đúng quyền.

## 9. Tiêu chí bàn giao lần kiểm chứng mới

- Đã thử đủ 9 chapter, 392 trang của Bocchi, Iris Zero và Witchriv; mỗi run đúng source/config/version mới.
- Không còn coverage mismatch do ID cross-target/placeholder trong schema mới.
- Mỗi target resolved hoặc unresolved có receipt rõ ràng; số target không mất.
- Tối đa một repair riêng; valid siblings không bị mất do một target lỗi.
- Mọi output bắt buộc có đủ page/style và review flags; semantic unresolved
  không được trình bày như coverage semantic hoàn chỉnh.
- Geometry receipt CUDA thực; GPU preflight/parity có kết quả và giới hạn rõ.
- Bank sạch ban đầu, cập nhật hợp lệ xuyên chapter, không merge ID khác miền.
- Giữ Markdown review tự sinh; agents không đọc nó làm approval gate.
- Archive/inventory/checksum và JSON/masks/translations/images/bank/audit đã
  verified ở máy; pod task-owned terminated và readback xác nhận.

## 10. Tiến độ thực tế

- Bàn giao đã viết trước khi sửa ID và trước lần chạy Runpod mới.
- ID ownership/schema, recovery theo target, unresolved source-only và flags xuyên
  translation/inpaint/typeset đã triển khai. Fixture semantic 6/6, pipeline 14/14 đạt.
- GPU geometry CuPy đã triển khai. CPU/missing-dependency fixtures đạt; CUDA parity
  và kernel preflight được bắt buộc trong bootstrap trước inference, không cho skip.
- Input fixture 3/3 đạt: publish nguyên tử, checksum ảnh/archive, từ chối thư mục
  input rỗng (lỗi cũ). Native Syncthing hai daemon thực chuyển canary, test đạt.
- Linux main lock mới `ddcff62d805c311c9940`, resolve CPU Docker 65 packages/4.07s.
  Visual `0c1ac629b5561491c648`, Hayai `ad8ce97387fa17b9ea7a` giữ đúng signature.
- Batch mới dự kiến `v6-nine-stories-idfix-20261013`; chưa thuê pod tại mốc cập nhật.
  Source/config được đóng băng khi prepare; không sửa code trong lúc chạy 9 chapter.

## 11. Hàng đợi và sao lưu native độc lập (cập nhật 2026-10-13)

`deployment/runpod/local_v6.py prepare` nhận `--additional-story STORY CHAPTER FOLDER`.
Chuẩn bị 9 ZIP riêng thứ tự 001–009, SHA/size/page receipts. `launch` là một lệnh:
ghép cặp Syncthing, xác minh canary đã tới máy, launch detached bootstrap và verifier.
Bootstrap tự nhận bundle/locks, setup/prefetch, doctor, CUDA parity, rồi toàn bộ queue.
Mỗi chapter chờ ZIP commit, CRC/SHA/per-image kiểm tra, atomic rename; lỗi chapter
được ghi nhận và queue tiếp tục. Chapter cùng truyện dùng bank xuyên chapter;
ba truyện tách bank. Không nhập bank local cũ.

Các folder Syncthing: máy send-only prepared payload → local ingress của pod;
pod send-only local runtime/data → máy `live`; pod send-only durability root → máy
`durable`. Syncthing v2.1.6 được pin/hash verify, không cần agent hay Python collector
để chuyển file. Daemon Windows hidden, đăng ký task lúc đăng nhập; receipt báo rõ
task có đăng ký được hay không. API bind loopback; peer TLS bằng cert pair; key/API
ở `%LOCALAPPDATA%/manga-runpod-sync/<batch>`, không vào bundle/backup. Direct port
22000 nếu pod cung cấp, discovery/relay làm fallback. PC receiver receive-only,
simple versioning giữ 3 bản thay đổi và ignoreDelete chống xóa từ pod; vì vậy không
lấy completion=100% đơn thuần làm bằng chứng cuối khi có xóa. Verifier riêng chỉ đọc
replica, xác minh SHA/size/CRC các publication rồi ghi `verification.json`; nó không
điều khiển chuyển file.

Máy cần còn bật và có mạng. Syncthing chỉ bảo vệ các file đã commit và được truyền;
pod mất điện/hết tiền ngay trước commit hoặc khi máy offline vẫn có thể mất phần
chưa truyền. Vì vậy live checkpoints được chuyển liên tục, không chờ hết cả queue.
Không tuyên bố bảo đảm tuyệt đối đối với dữ liệu chưa ghi ra đĩa.

Lệnh chạy sau khi pod đã cấp và `.runpod/connection.json` đã ghi đúng pod/SSH:

```powershell
python deployment/runpod/local_v6.py launch --plan .runpod/v6/v6-nine-stories-idfix-20261013/plan.json --pod-id <TASK_OWNED_POD>
```

Không cần lệnh khác để queue, reviews hay copy dữ liệu chạy tiếp. Theo dõi bằng
replica máy tại `outputs/runpod-v6-nine-stories-idfix-20261013/durable/<batch>/jobs/`.
`native_sync.py status --plan ...` là đọc trạng thái tùy chọn. Không chạy collector
SCP. Metadata pod/giá/SSH/launch nằm trong `.runpod/v6/<batch>/infrastructure.json`,
`bound-plan.json`, `native-sync.json`, `native-verifier.json`; dùng các file đó làm
nguồn trạng thái mới nhất, không dùng pod cũ. Auto terminate bằng API chưa cấu hình
do không có credential Runpod cho daemon; agent sử dụng MCP terminate đúng pod sau
khi replica verification đạt. Không tuyên bố pod tự ngừng billing nếu agent mất.

Image v6 local chưa có manifest registry; dùng image v5.0.2-fast-code.1 đã từng boot
thành công, cộng bundle/locks mới. Không build/push lại image 46 GB trên đường tới run.
Trên Runpod không re-resolve dependencies; env visual/Hayai riêng và Hayai OCR nova
được setup/prefetch trước inference. Không đọc Markdown review làm approval gate.
