# Năm stage đã tích hợp vào pipeline v6

Nhánh `thanhdat` chạy các engine đã tích hợp bằng profile [`configs/core-v6.json`](../configs/core-v6.json). Pipeline v6 điều phối stage, quản lý checkpoint/receipt, kiểm tra hash, tạo review và ghi lỗi.

**Thư mục `core/` ở root đã được xóa.** Các engine nằm trong `src/manga_pipeline`; pipeline không cần khôi phục thư mục cũ. Tên profile `core-v6.json`, khóa cấu hình `core.enabled` và file adapter `integrations/core.py` được giữ để nhận diện backend đã tích hợp.

Phạm vi cải tiến gồm đúng năm stage: **layout, extract, bubble_geometry, inpaint, typeset**. Stage translate và provider dịch/rút gọn hiện có của v6 được giữ nguyên.

## Phân công code

Các đường dẫn dưới đây tính từ `src/manga_pipeline`:

| Stage | Khi bật profile tích hợp |
| --- | --- |
| layout | `visual/core_detector.py:MangaDetector` tìm panel/bubble theo panel crop, sắp thứ tự và loại trùng. Adapter chuyển mask crop sang mask toàn trang, giữ text detection để đối chiếu trong `core_text_regions`. Visualization nằm trong `visual/core_visualizer.py`. Checkpoint Koharu vẫn dùng model/revision/hash của v6. |
| extract | MAGI giữ text box, nhân vật, tail anchor và liên kết text–character. `extraction/core_ocr.py:MangaOcrEngine` nhận crop từ các text box này trong worker Hayai riêng, dùng OCR theo batch thật. |
| bubble_geometry | Thuật toán group/body/bridge/tail và mapping OCR của v6 nhận panel/mask từ core layout. Không thay hình học bằng bbox của core. |
| inpaint | v6 chọn nội dung và lập white/AOT mask; `visual/core_inpaint.py` cùng `visual/core_aot_model.py` xử lý phần AOT. v6 vẫn bảo vệ pixel ngoài mask và lưu clean/mask/checkpoint. |
| typeset | `v6/typeset_geometry.py:largest_rectangle` chọn vùng chữ trong safe body mask. V6 vẫn phân bố từ, đo glyph, kiểm tra không mất/đảo từ, không tràn mask và giữ needs_review. Đọc bản dịch từ output translate có sẵn; rút gọn câu dùng provider hiện có của v6. |

Text detection của detector tích hợp được lưu làm bằng chứng, **chưa thay text box MAGI**, để không gán lại liên kết người nói bằng suy đoán overlap. `scan/analyze` và `translate` tiếp tục dùng các backend hiện có của v6. Typeset sử dụng bản dịch đã hoàn thành từ stage translate.

Import `manga_pipeline.extraction.core_ocr` không kéo theo OpenCV/RF-DETR; adapter không ép Transformers 5 vào môi trường MAGI. [`integrations/core.py`](../src/manga_pipeline/integrations/core.py) chỉ nạp module bên trong package:

```text
src/manga_pipeline/
├── integrations/
│   ├── core.py                 # Adapter và ánh xạ module nội bộ
│   └── core_config.py          # Kiểm tra cấu hình core.enabled
├── extraction/
│   ├── magi.py                 # Text box, nhân vật và liên kết
│   ├── hayai.py                # Worker OCR riêng
│   └── core_ocr.py             # Engine OCR theo batch
├── visual/
│   ├── core_detector.py        # Detect panel/bubble
│   ├── core_visualizer.py      # Visualization của detector
│   ├── core_inpaint.py         # AOT inference với model đã xác thực
│   └── core_aot_model.py       # Kiến trúc AOT
└── v6/
    ├── stages.py               # Điều phối v6, gồm bubble_geometry
    ├── visual.py               # Group/body/bridge/tail và mapping
    ├── typeset.py              # Dàn chữ và kiểm tra glyph
    └── typeset_geometry.py     # Largest rectangle trong safe mask
```

## Cấu hình và chạy

1. Dùng ba môi trường đã thiết lập: `.venv`, `.venv-layout-local`, `.venv-hayai-local`. Năm stage tích hợp không yêu cầu cài llama-cpp-python hay cấu hình GGUF.

2. Chuẩn bị model theo các script v6 trong [hướng dẫn debug stage](dev_stage_debug_vi.md). OCR tích hợp dùng bản **Hayai v2.5 Nova đã pin của v6**. Các adapter dùng model được chuẩn bị và kiểm tra hash theo cơ chế v6; không dùng đường dẫn model của ứng dụng standalone cũ.

3. Giữ cấu hình provider phân tích/dịch của v6. Typeset cần output translate đã hoàn thành; đây là phụ thuộc đầu vào, không phải phần cải tiến stage translate.

4. Tạo run mới trên thư mục mẫu một trang:

```powershell
.\.venv\Scripts\python.exe run.py run outputs/debug-thanhdat/input --story core-debug --chapter sample --config configs/core-v6.json --output-root outputs/core-debug --bank-root banks/core-debug --until layout --no-progress
```

Sau đó lấy `Run directory:` và dùng lệnh từng stage trong [hướng dẫn debug](dev_stage_debug_vi.md). Để đi đến inpaint cần normalize/scan/analyze/identity/export; typeset cần translate và inpaint đã bật trong profile.

Không đổi backend bằng cách sửa manifest của run cũ. Profile mặc định không bật core; run cũ tiếp tục backend cũ. Với core, layout/inpaint ghi engine trong request checkpoint. Thay model hoặc code để đo lại cần run thử mới.

## Phạm vi kiểm tra

Đã đạt **29 kiểm tra** khi chặn hoàn toàn import package `core` ở ngoài. Kiểm tra import layout/visualizer và OCR cũng đạt trong hai môi trường worker tương ứng; worker OCR không kéo theo OpenCV/RF-DETR. Việc xóa thư mục gốc không làm mất các module phục vụ năm stage.

Kiểm tra hợp đồng bằng fixture/fake model: tọa độ mask, bảo vệ pixel ngoài mask, OCR batch/order, giữ liên kết MAGI, giữ provider v6 và rectangle không vượt safe mask; chạy kèm regression v6/visual/Hayai. Chưa đo chất lượng hay tốc độ bằng model thật trên chapter. Vùng chữ nhật core có thể nhỏ hơn vùng mask cong của bộ dàn chữ mặc định, do đó có thể làm tăng số mục cần review.
