# Chạy và review pipeline

## Các lệnh stage

Từ root dự án, dùng Python của môi trường đã cài dependencies. Các lệnh sau có thể gọi model thật; cần endpoint/runtime phù hợp.

```powershell
python run.py run "D:\download\EN-data\Iris Zero\EN\Chapter 1" --story iris-zero --chapter chapter-001 --config configs/pipeline.json --until graph
python run.py resume --run-dir PATH_TO_RUN --until analyze
python run.py reanalyze --run-dir PATH_TO_RUN
python run.py resume --run-dir PATH_TO_RUN --until relationship
```

`reanalyze` hiện là stage đối chiếu chapter. Lệnh cũ tạo revision đã đổi thành:

```powershell
python run.py revise --run-dir PATH_TO_RUN --config configs/pipeline.json --until reanalyze
```

Run cũ dùng contract khác không được resume bằng source mới. Dùng frozen source cũ để đọc/chạy tiếp, hoặc import upstream đã xác minh vào run mới. Không sửa `analysis_contract` trong manifest để vượt kiểm tra.

## Runpod

Controller chuẩn vẫn là `runpod-v6.ps1` → `deployment/runpod/local_v6.py`, local working-set `/opt/manga-work` và native Syncthing. L40S Secure là mặc định dự án. Chỉ thực hiện các bước có truy cập Runpod khi người dùng đã yêu cầu cho tác vụ đó.

Chuẩn bị offline, với dependency locks hiện có:

```powershell
$Batch = 'iris-zero-story-graph-v7-r1'
.\runpod-v6.ps1 prepare 'D:\download\EN-data\Iris Zero\EN\Chapter 1' --batch $Batch --story iris-zero --chapter chapter-001 --locks '.runpod/v6-locks' --until reanalyze
$Plan = ".runpod/v6/$Batch/plan.json"
python deployment/runpod/native_sync.py prepare --plan $Plan
```

Sau khi có pod được cấp quyền sử dụng:

```powershell
.\runpod-v6.ps1 launch --plan $Plan --pod-id POD_ID
.\runpod-v6.ps1 watch --plan ".runpod/v6/$Batch/bound-plan.json"
```

`--until` nhận `graph`, `analyze`, `reanalyze`, `build_identity`, `relationship`. Bỏ `--until` để chạy pipeline đầy đủ theo các stage được bật. `--skip-startup-tests` chỉ dùng khi người dùng chủ động yêu cầu bỏ qualification/preflight; không bỏ kiểm tra checksum.

Import từ backup: mặc định boundary `normalize`. Với output cũ, tái sử dụng extraction/geometry đã xác minh, normalize được dựng lại để áp dụng quy tắc panel mới; graph/analyze/reanalyze chạy mới. Không import graph/analyze của contract độc lập cũ sang contract mới. Những publication identity/profile đã tồn tại không được rollback âm thầm.

## Checkpoint và lỗi

- Event đã có response/target receipt hợp lệ được reuse; không POST lại nội dung đã lỗi hoặc không rõ trạng thái.
- Event lỗi: scene vẫn tiến tới event sau với memory hợp lệ gần nhất, text event lỗi và trạng thái thiếu.
- Reanalyze lỗi: stage lưu fallback snapshot bằng nguyên analyze, cho phép downstream tiếp tục; không báo đã reanalyze thành công.
- Context/ảnh quá giới hạn: báo rõ; không tự bỏ text/panel hoặc thay event.
- Source, config, ảnh bằng chứng và artifact đã commit được kiểm tra checksum.

## Review

Graph, analyze, reanalyze tự tạo `review.md` và `review.html` trong thư mục stage. HTML là file offline, có cây scene/event, tên ngắn, mô tả, semantic/confidence, ảnh event và patch được/chưa được áp dụng. Link ảnh dùng đường dẫn tương đối để replica local vẫn xem được.

Review là đầu ra để người dùng kiểm tra, không phải approval gate của pipeline. Identity/relationship giữ HTML reviewer riêng hiện hành. Không dừng/terminate pod chỉ để chờ đọc docs hoặc review; lifecycle vẫn theo chỉ dẫn của người dùng cho tác vụ đó.
