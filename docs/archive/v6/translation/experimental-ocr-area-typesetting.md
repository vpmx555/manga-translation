# Thử nghiệm: vùng OCR, quota diện tích và mở rộng có giới hạn

[M9 mặc định](m9-typesetting.md) · [Chẩn đoán tái tạo chữ](../delivery/text-reconstruction-diagnostics.md)

Từ 2026-10-13, pipeline mới mặc định `v6.typeset_mode="ocr-box"`, dùng vùng OCR và phân bổ diện tích ở mục 1–3. Phần **mở rộng bbox** ở mục 4 vẫn chỉ dùng trong công cụ thử nghiệm; full pipeline không tự mở rộng ra ngoài khung OCR. Run cũ thiếu trường mode giữ cách dàn bubble-body khi resume. Module:
[experimental_ocr_typeset.py](../../../src/manga_pipeline/v6/experimental_ocr_typeset.py).
Không dùng model mới, OCR mới, translation, analyze hoặc LLM.

## 1. Tạo vùng dàn vật lý

**Input:** ảnh `W×H`, utterance/group, các `source_boxes` với `source_text_id`,
bbox float `[x1,y1,x2,y2]`, reading order, part mapping; body/bridge/tail masks.

**Phương pháp:** một vùng dàn cho mỗi OCR bbox. Raster dùng ceil góc đầu và floor
góc cuối để không vượt bbox float. Mask vùng là giao của bbox với safe body mask
đã erosion margin **10 px** và loại bridge/tail. Hai vùng usable bị overlap sẽ
được báo review. `min_body_area=2500` thuộc geometry; không xóa một OCR region
nhỏ chỉ vì diện tích nhỏ hơn 2500.

**Output:** danh sách region gồm original OCR bbox, raster bbox, mask bool H×W,
diện tích OCR rectangle/usable, anchor là tâm OCR gốc và source ID. Group thoại
không bị tách thành nhiều câu hay nhiều người nói.

## 2. Chia chữ theo diện tích

**Input:** một chuỗi chung đã NFC/chuẩn hóa whitespace, các vùng theo reading
order và diện tích usable ban đầu `A_i`.

**Phương pháp:** đếm ký tự Unicode không gồm whitespace, gồm cả dấu câu. Mục
tiêu vùng i là `C × A_i / sum(A)`. Dynamic programming chia các đoạn từ liên tiếp
để tối thiểu hóa tổng bình phương sai lệch tỷ lệ ký tự so với tỷ lệ diện tích.
Không cắt giữa từ, không đảo thứ tự, không mất/nhân đôi token. Nếu đủ từ, mỗi vùng
được ít nhất một từ. Nội dung OCR nguồn không là ràng buộc bắt buộc cho điểm chia:
một vài từ có thể chuyển sang vùng kế tiếp để đạt quota.

**Output:** `word_range`, đoạn text, area share, target/allocated characters và
sai lệch quota. Phân bổ này được chốt **trước khi chọn font** và giữ nguyên kể cả
khi vùng dàn được mở rộng.

## 3. Tìm cỡ font chung

**Input:** các đoạn đã chia, region masks, font AstroCity và khoảng font 48→10 px.

**Phương pháp:** dùng các horizontal slots và font metrics của renderer hiện
tại, đo từng dòng và raster glyph thật. Mỗi lần thử dùng cùng một font size cho
mọi vùng của group. Chọn cỡ lớn nhất fit toàn bộ đoạn được giao và có mọi glyph
thuộc mask. Ban đầu chỉ thử bên trong OCR bbox gốc.

**Output:** một `font_size_px` cho group, lines với text/x/y/width/height, glyph
masks; hoặc overflow. Trace lưu mọi lần thử và lý do không fit từng vùng.

## 4. Mở rộng khi overflow

**Trigger:** không fit trong OCR gốc ngay cả ở font tối thiểu đã cấu hình.

**Phương pháp:** chỉ các vùng còn overflow tăng bbox từng vòng **2 px** về mỗi
hướng khả dụng. Hướng không thêm được pixel safe bởi viền/margin, image edge
hoặc vùng láng giềng sẽ bị khóa; vẫn tiếp tục các hướng khác. Glyph luôn bị giới
hạn bởi safe body mask có margin 10px. Không nhảy qua một dải hoàn toàn bị cấm
để tìm thêm chỗ ở body/panel khác.

Hai vùng mở rộng không được chạm nhau. Probe dùng khoảng cách Euclidean tối
thiểu **10 px giữa bbox vùng dàn**, có thể cấu hình bằng `--neighbor-gap-px`.
Nếu hai vùng gốc đã gần hơn ngưỡng này, chúng không mở rộng; vẫn thử fit trong
vùng gốc. Vì vậy vùng gốc cách nhau 4px được giữ nguyên, không được kéo sát hơn.

Mỗi vòng kiểm tra ở min font để tránh thử lặp toàn bộ dải font. Khi đã vừa, tìm
lại font lớn nhất chung trên vùng sau mở rộng. Nếu mọi hướng bị khóa mà vẫn
overflow, trả `needs_review`, không vượt margin/khoảng cách để ép chữ vào.

**Output:** final masks/bboxes, lịch sử từng hướng/vòng, blocked reasons và plan
chung hoặc review. Quota vẫn tính theo diện tích OCR usable ban đầu.

## 5. Probe trước khi bật mặc định

[probe_ocr_area_typeset.py](../../../scripts/diagnostics/probe_ocr_area_typeset.py)
đọc inpaint và baseline typeset đã hoàn tất, kiểm tra checksum, reuse geometry,
OCR và ảnh sạch. Output đặt vào folder riêng và không thay đổi source run.

```powershell
& '.\venv\Scripts\python.exe' -B 'scripts/diagnostics/probe_ocr_area_typeset.py' --source-probe '<completed-inpaint-typeset-probe>' --output '<new-output>'
```

**Output:** `result.json`, `README.vi.md`, `gallery.html`; mỗi trang có
`typeset-plan.json`, rendered và so sánh gốc / body layout / OCR-area layout.
Mỗi group có safe/glyph masks, quota, từng font trial và mọi vòng mở rộng phát
sinh. So sánh cuối được tạo khi toàn trang đã hoàn tất.

Kiểm tra glyph thuộc region và safe body, glyph không đè nhau, vùng không overlap,
bbox đã mở rộng giữ khoảng cách bảo vệ và composite chỉ đổi pixel glyph. Các
[unit checks nhỏ](../../../tests/test_experimental_ocr_typeset.py) bao phủ quota,
giữ thứ tự từ, common font, bbox float, overlap, hướng bị chặn và overflow/
mở rộng/gần vùng khác. Không chạy model hoặc test chapter dài trên CPU.
