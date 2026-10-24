# Resume, reuse và khắc phục lỗi

[← Chỉ mục](../README.md)

## Chọn ranh giới tái sử dụng

| Tình huống | Cách tiếp tục |
|---|---|
| Ngắt tiến trình, source/config/contract giữ nguyên | Resume cùng run, dùng stage/target commits hợp lệ |
| Chỉ đổi analyze/identity/graph policy | Run mới, import qua scan nếu scan tương thích |
| Đổi scan/chỉnh tên | Import qua normalize; chạy lại scan và phía sau |
| Đổi OCR/detection/geometry/seed bank | Không reuse stage chịu thay đổi; chuẩn bị extraction/diagnostic phù hợp |
| Run identity legacy sang DAG mới | Không import sau analyze; chọn upstream scan/normalize |
| Analyze chưa có named identity | Run mới bật bootstrap; import upstream qua scan phù hợp. Binding nhận tên chưa confirmed nhưng bỏ mention_type thiếu/unknown; không bịa seed identity |
| Failed/uncertain profile hoặc pair request | Không resend khi resume; giữ review/fallback theo policy |
| Changed config/source trong frozen run | Tạo run/batch mới, không sửa snapshot/hash để qua kiểm tra |

## Ví dụ reuse checkpoint để test hai stage mới

Lệnh thực tế của batch thử ngày2026-10-23 tái sử dụng năm stage đầu, chạy lại
analyze rồi dừng sau relationship:

```powershell
.\venv\Scripts\python.exe deployment/runpod/local_v6.py prepare `
  --batch iris-zero-ch1-identity-relationship-l40s-20261023-r1 `
  --gpu-preset l40s --llm-slots 8 --story-workers 1 `
  --reanalyze-existing `
  --restore-from outputs/runpod-iris-zero-ch1-glm-translate-resume-l40s-20261023-r15 `
  --import-preanalyze --import-through scan --until relationship `
  --story iris-zero --chapter chapter-001 --locks .runpod/v6-locks `
  "D:\download\EN-data\Iris Zero\EN\Chapter 1"
```

Tên batch này là ví dụ lịch sử, không dùng lại để freeze thay đổi mới. Importer
xác minh terminal backup và archive inventory, source/config compatibility,
snapshot lineage; không copy analyze cache vào run mới. `--reuse-runtime` chỉ
dùng khi toàn bộ bank/runtime được đối chiếu đúng inventory; không bật để bỏ
qua restore. `--qualification-from` chỉ reuse cùng pod và config phù hợp,
không dùng proof của máy đã terminate cho pod mới.

`--import-through inpaint` có hỗ trợ cho contract tương thích; không dùng nó
để mang stage identity legacy sang graph v2. Không sửa frozen DAG trong manifest.

## Chẩn đoán theo bằng chứng

| Triệu chứng | Kiểm tra / xử lý |
|---|---|
| API RUNNING nhưng không SSH/runtime | Đọc pod/ports/system logs, thời gian pull/boot; chưa gọi là inference |
| Launch timeout | Kiểm tra native canary, device identity, direct TCP mapping, driver/bound plan trước retry |
| `Driver already active` | Xác minh PID/start ticks và job của đúng batch; không mở driver/pod thứ hai |
| Native không connect | Kiểm tra đúng peer/GUI/home/port, mapping22000, reload pinned address; không dừng daemon khác |
| Sync idle nhưng chưa có result | Đối chiếu stage/driver và publication; idle không chứng minh pipeline hoàn tất |
| Quá context trước POST | Xem packed tokens/output reserve/omitted evidence; sửa packing/config offline ở batch mới |
| Output truncated/schema lỗi | Giữ raw response/receipt và lỗi validator; policy stage quyết định continuation |
| CUDA OOM | Xem engine/admission/ảnh/reserve; hạ slots cho batch mới nếu cần; v2 job lỗi không retry |
| Analyze có thoại nhưng graph rỗng | Đếm named confirmed identities, profile eligibility, resolved direct pairs |
| Nhiều MAGI ID cùng người | So vectors/crops/prototype rồi xem identity link; không renumber bank để che split |
| Crop hash/reference mismatch | Integrity failure; khôi phục artifact đúng hoặc run mới, không cập nhật hash giả |
| PC hết dung lượng | Giải phóng/chuyển vùng lưu có kiểm tra, phục hồi replication; không hạ ngưỡng bảo vệ để ép chạy |
| Verified backup chứa đường dẫn `\\?\D:\...` | Dùng `native_verify.local_path` trong helper tương thích; không kết luận archive missing chỉ vì prefix Windows |

## Zero identity là một kết quả cần báo rõ

Trong checkpoint r15 dùng làm nguồn thử, analyze cũ có260 rows,34 unresolved,
236 provisional persons và **0 named confirmed persons**. Scan có45 candidates
nhưng không đủ để tự cấp identity. Chạy hai stage mới nguyên checkpoint đó sẽ
không kiểm chứng được chất lượng profile/relationship model nếu không có job.

Do đó batch thử mới chạy lại analyze. Đây là tái chạy có chủ đích để tạo đúng
đầu vào semantic, không phải import registry cũ bị nhiễm alias hoặc giả thêm tên.
Khi báo kết quả cần tách số job dự kiến, đã gửi, thành công, failed/uncertain,
fallback, unknown và stage commit.

## Khi cần đổi code vì lỗi

Không hotpatch thuật toán/prompt/model trên pod trong workload đang freeze.
Giữ lỗi và artifacts, cho phép các job độc lập tiếp tục theo contract. Sửa
offline, chọn ranh giới import an toàn và freeze batch mới. Lỗi kết nối/phục vụ
có thể phục hồi theo lane hiện có nếu giữ nguyên contract và không replay một
POST uncertain của stage no-retry.

Không tự chạy nhiều vòng local model để chữa từng câu nội dung. Static review,
unit/integration checks phù hợp và một batch Runpod được người dùng yêu cầu là
các phạm vi khác nhau; giữ phân biệt rõ trong báo cáo.
