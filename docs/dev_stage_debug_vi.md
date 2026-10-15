# Chạy và debug từng stage trên nhánh thanhdat

Chạy các lệnh bên dưới từ thư mục `manga-translation`. Bắt đầu bằng thư mục chỉ chứa một trang để quan sát lỗi và thời gian chạy. Giữ nguyên ảnh nguồn trong suốt một run.

## Chuẩn bị model

```powershell
.\.venv-layout-local\Scripts\python.exe scripts\setup_visual_models.py --only layout
.\.venv-hayai-local\Scripts\python.exe scripts\diagnostics\prefetch_v6_ocr.py
.\.venv\Scripts\python.exe scripts\setup_visual_models.py --only inpaint
```

MAGI tải model khi extract khởi động. Các bước scan/analyze/translate cần model NER và dịch vụ Ollama theo cấu hình. Bộ PyTorch hiện tại là CPU; các stage model có thể chạy chậm.

## Chạy lần lượt

`configs/dev-stages.json` bật inpaint từ lúc tạo run, giữ visualization, dùng Hayai batch size 1. Cấu hình này được hợp nhất với mặc định của pipeline.

```powershell
$py = '.\.venv\Scripts\python.exe'
& $py run.py run 'outputs/debug-thanhdat/input' --story debug-thanhdat --chapter sample --config configs/dev-stages.json --output-root outputs/debug-thanhdat --bank-root banks/debug-thanhdat --until layout --no-progress
```

Sao chép đường dẫn `Run directory:` vừa in ra:

```powershell
$runDir = 'ĐƯỜNG_DẪN_RUN_VỪA_TẠO'

# Chỉ extract còn thiếu; layout đã hoàn thành được dùng lại.
& $py run.py resume --run-dir $runDir --until extract --no-progress

# Chạy đúng một stage, yêu cầu các stage đầu vào đã hoàn thành.
& $py run.py bubble_geometry --run-dir $runDir --no-progress

# Chuẩn bị các phụ thuộc cho inpaint: normalize, scan, analyze, identity, export.
& $py run.py resume --run-dir $runDir --until export --no-progress
& $py run.py inpaint --run-dir $runDir --no-progress

# Chuẩn bị bản dịch rồi dàn chữ.
& $py run.py resume --run-dir $runDir --until translate --no-progress
& $py run.py typeset --run-dir $runDir --no-progress
```

Lưu log cho một lần chạy:

```powershell
& $py run.py bubble_geometry --run-dir $runDir --no-progress 2>&1 | Tee-Object -FilePath "$runDir/bubble_geometry-console.log"
```

**Không dùng `extract --run-dir`**: CLI hiện tại dành lệnh `extract` để tạo run mới từ thư mục ảnh và chạy đến bubble_geometry. Dùng `resume --until extract` để dừng đúng sau extract.

**Không dùng `resume --until typeset` khi muốn chỉ đo typeset**: resume có thể chạy tất cả phụ thuộc còn thiếu. Lệnh `typeset --run-dir` chỉ chạy typeset và báo lỗi nếu thiếu phụ thuộc.

## Điểm đặt breakpoint và output cần xem

| Stage | Code chính | Kiểm tra |
| --- | --- | --- |
| layout | `visual/stages.py:layout`, `visual/koharu.py` | Panel/bubble bị thiếu, detection trùng, mask và tọa độ crop |
| extract | `extraction/magi.py:extract`, `extraction/hayai.py` | Text box, OCR, nhân vật, liên kết, tail anchor |
| bubble_geometry v6 | `v6/stages.py:geometry`, `v6/visual.py` | Group/part bị gộp hoặc tách sai, body/bridge/tail, mapping OCR |
| inpaint | `visual/inpaint.py:plan_masks`, `visual/inpaint.py:inpaint`, `visual/aot.py` | Mask có xóa viền/tranh, chữ còn sót, white fill/AOT, ảnh clean |
| typeset | `v6/typeset.py:safe_parts`, `allocate`, `fit`, `typeset` | Safe mask, cỡ chữ, chia chữ giữa body, thiếu chữ, needs_review |

Trong workspace `main`, mở Run and Debug (Ctrl+Shift+D), chọn cấu hình stage và F5. `.vscode/launch.json` đã bật `subProcess` để theo dõi worker; `justMyCode=false` cho phép đi vào thư viện khi cần. Nhập thư mục ảnh mẫu hoặc run directory khi VS Code hỏi.

Mỗi run có `manifest.json`, `review.md` và thư mục từng stage. Đọc `result.json`, các ảnh/mask và `typeset-plan.json` nếu được tạo. Số thứ tự thư mục phụ thuộc flow; tra theo tên stage, không hard-code số. `manifest.json` lưu trạng thái và runtime; log console giữ lỗi worker.

## Chạy lại sau khi sửa code

Stage đã completed sẽ được dùng lại, không tự chạy lại chỉ vì code thay đổi. Run lỗi/partial có thể tiếp tục bằng cùng lệnh, nhưng target thành công cũng có thể được dùng lại. Muốn đánh giá lại toàn bộ stage sau sửa code, tạo run thử mới và dùng bank root riêng cho mỗi thí nghiệm nếu cần so sánh độc lập. Không sửa tay manifest/receipt/snapshot hoặc xóa output để ép chạy lại.

Config đã được chụp trong run; đổi tham số cần tạo run mới. Bật inpaint ngay khi tạo run để typeset dùng trang clean và khai báo đúng phụ thuộc.
