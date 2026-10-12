# Chẩn đoán từng bước tái tạo chữ

[Tổng quan v6](../README.md) · [M9 dàn chữ](../translation/m9-typesetting.md) ·
[Giới thiệu source bubble speech](../../../src/bubble_speech_pipeline_vi.md)

[Thử nghiệm OCR-area có mở rộng giới hạn](../translation/experimental-ocr-area-typesetting.md)
dùng vùng OCR gốc thay cho toàn body; chạy probe riêng trước khi cân nhắc bật mặc định.

Mặc định chung: `min_body_area=2500`, `body_margin_px=10`,
`body_margin_ratio=0.0`, font `assets/fonts/astro-city-teddy-bear.ttf`.
Margin và diện tích tính theo ảnh gốc. Ngưỡng diện tích áp dụng cho lõi sau
opening/component; nếu không có lõi đủ lớn, geometry vẫn giữ body dự phòng.

## Input và các bước chạy local

Input là một v6 run đã hoàn tất `layout` và `extract`, gồm manifest/source hashes,
Koharu candidates/masks và các bbox/text OCR. Không cần analyze hoặc translate.

[probe_bubble_ocr_inpaint.py](../../../scripts/diagnostics/probe_bubble_ocr_inpaint.py)
với `--source-run` tạo `geometry-run` riêng trong output chẩn đoán. Nó kiểm tra
cache, sao chép detection/OCR đã xác minh, áp dụng ngưỡng hiện tại, tính lại
geometry/normalize rồi erase/inpaint. Source run cũ giữ nguyên. Chỉ sử dụng
`--recorded-config` khi muốn tái hiện baseline cũ. `--plan-only` không chạy AOT
và chưa đủ input cho bước vẽ chữ.

```powershell
& '.\venv\Scripts\python.exe' -B 'scripts/diagnostics/probe_bubble_ocr_inpaint.py' --source-run '<source-run>' --device cpu --probe-output '<new-output>'
& '.\venv\Scripts\python.exe' -B 'scripts/diagnostics/restore_ocr_typeset.py' --probe-dir '<new-output>'
```

CPU chỉ dùng cho chẩn đoán từ cache. Detection/OCR mới yêu cầu CUDA theo guard
trong script. Lệnh này không tạo, mở hoặc sử dụng Runpod.

[restore_ocr_typeset.py](../../../scripts/diagnostics/restore_ocr_typeset.py)
dùng trực tiếp `safe_parts()` và `fit()` của production. Text lấy từ OCR gốc;
chỉ chuẩn hóa NFC/whitespace, không dịch, sửa OCR hoặc rút gọn. Font có kiểm tra
cmap tiếng Việt. Callback `fit(trace=...)` ghi từng lần thử thực tế và không đổi
thuật toán chọn plan/glyph.

## Output

- `ocr-typeset-result.json`: config/font coverage, số trang/OCR đã vẽ, issues,
  checksum cho các artifact từng trang.
- `typeset-gallery.html`, `ocr-typeset-review.md`: mục lục từng group.
- `pages/page-N/ocr-restored/utterance-K-trace/`: `README.vi.md`, `index.html`,
  `trace.json`, contact sheet và visual từng bước nhỏ.
- `typeset-plan.json`, các `*-safe.png`/`*-glyph.png`, `rendered.png`,
  `before-after.png`: plan thật, masks và kết quả toàn trang.

Visual tuần tự: OCR trước group → từng detection candidate → observed OR →
inferred repair → refined mask → opening cores và ngưỡng diện tích → các body,
bridge, tail → mapping OCR → clean sau inpaint → margin/safe mask/anchor → slots
ở mọi font size đã thử → phân bổ từ và trạng thái thử → line boxes → glyph →
composite từng part → so sánh khi toàn trang hoàn tất.

`trace.json` giữ các chuỗi OCR trước/sau group, bbox, word tokens/ranges, capacity,
font size, slots, dòng đặt chữ và lý do từ chối từng lần thử. Mọi tọa độ là tọa độ
ảnh gốc; ảnh crop chỉ để quan sát và có trường `crop` để đối chiếu.

[review_text_reconstruction.py](../../../scripts/diagnostics/review_text_reconstruction.py)
kiểm tra checksum, token order, glyph trong safe mask, composite chỉ đổi pixel
glyph và inpaint chỉ đổi pixel erase mask. Với chẩn đoán After School hiện tại,
lệnh này tạo báo cáo và ảnh so sánh gốc/baseline/mới cho trang 4, utterance 3.

## Vì sao bubble đôi chưa giữ bố cục gốc?

Group thoại và vùng dàn vật lý là hai khái niệm riêng. Nếu opening vẫn cho một
lõi vì phần nối quá rộng, geometry xuất một body, bridge mask rỗng. Hai OCR
được nối thành một câu và cùng map vào body đó. Renderer dùng điểm neo trung
bình; phân bổ từ chỉ nhìn thấy một part. Nó ưu tiên ít dòng trước độ gần neo và
không giữ ranh giới đoạn OCR làm ràng buộc. Chữ có thể nằm trong safe mask nhưng
vẫn chạy qua phần nối và không giống hai khối trong ảnh gốc.

Đổi font/margin hoặc tăng min area không tự giải quyết lỗi này. Hướng tiếp theo
là giữ group chung, phát hiện nhiều vùng dàn bằng hình dạng/distance transform
và bằng chứng chỗ thắt, rồi sử dụng các OCR bbox làm neo riêng. Bridge được giữ
trong topology group nhưng không thuộc vùng chữ. Các thay đổi thuật toán này
chưa được triển khai trong lượt cập nhật ngưỡng và chẩn đoán.
