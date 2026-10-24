# So sánh OCR thanhdat với main pipeline hiện tại

Ghi chú sau review: hành vi OCR của thanhdat đã được tích hợp vào main; xem `thanhdat-ocr-integration-20261022.md`. Nội dung dưới đây là đối chiếu trước khi tích hợp.

## Phạm vi

Đã fetch riêng `origin/thanhdat`, HEAD `2fb3b9a5d2b084850845bfa5bc986715d39e58da` (`update`), rồi đọc tree bằng Git; không checkout/merge và không thay code pipeline. Đối chiếu với working tree main hiện tại, gồm các sửa local chưa commit; commit nền main `81027d9`. Không chạy OCR hoặc benchmark chất lượng.

## Kết luận

Nhánh thanhdat bổ sung engine `core` cùng lớp adapter layout/OCR/inpaint/typeset. Nhưng khi bật core trong pipeline v6, OCR vẫn dùng Hayai v2.5 Nova cùng revision, processor, crop, preprocessing và generation parameters như main. Chưa có bằng chứng từ code rằng model nhận dạng tốt hơn. Các khác biệt trực tiếp là xử lý khoảng trắng, xử lý kiểu trả về một chuỗi, profile batch 1 và cách đóng gói engine. Khác biệt layout đáng khảo sát, nhưng vùng text Koharu chưa thay thế box MAGI trong luồng OCR tích hợp hiện tại.

## Các phần giống nhau

| Thành phần | Main hiện tại | thanhdat với core được bật |
|---|---|---|
| Model OCR thực sự trong v6 | Hayai v2.5 Nova | Cùng model, truyền từ config |
| Revision | `2803d0dc005fa4396ab1e7c5a472d2c450a247f7` | Giống |
| Processor | SigLIP2 base patch16 naflex, revision `b53b807d3a2d5e2b3911292f2d69e5341cdc064c` | Giống |
| Nguồn bbox để OCR | `results[page]['texts']` từ MAGI | Giống |
| Hàm crop | Floor góc đầu, ceil góc cuối, clip vào ảnh | Nội dung hàm giống hệt sau chuẩn hóa line ending |
| Input ảnh trong worker | Grayscale rồi convert RGB | Giống |
| Max patches / tokens | 384 / 128 | Adapter truyền cùng giá trị, không dùng default 256 của lớp standalone |
| Generate | pixel values, attention mask, spatial shapes, tokenizer, repetition penalty 1.0 | Giống |
| Môi trường OCR | Worker riêng, requirements-hayai | Giống; lock requirements-hayai.txt giống nhau |
| Kiểm tra asset | Pinned model/processor, assets.json và SHA256 khi có receipt | Hàm local_assets giống hệt |

Lưu ý: `core_ocr.py` có default standalone là `hayai-ocr-v2`, nhưng `recognize_core()` truyền model và thư mục của Nova từ config. Không kết luận pipeline core dùng v2 chỉ dựa vào default của class.

## Các khác biệt có ý nghĩa

### 1. Chuẩn hóa khoảng trắng trong đầu ra

Main: `text.strip()` tại `src/manga_pipeline/extraction/hayai.py:137`, chỉ bỏ khoảng trắng hai đầu.

thanhdat: `core_ocr.py:134–141` dùng `" ".join(text.split())`, gộp khoảng trắng/newline bên trong thành một space. Ví dụ `" One  two\nThree "` trở thành `"One two Three"`. Đây là cải thiện định dạng, không thay chữ model nhận dạng được.

### 2. Profile batch 1, không phải mặc định toàn nhánh

`origin/thanhdat:configs/core-v6.json` bật `core.enabled=true`, đặt `ocr.batch_size=1`; `configs/dev-stages.json` cũng dùng batch 1. Tuy nhiên DEFAULT_CONFIG trong hayai.py vẫn batch 8 giống main. Cần phân biệt profile chạy thực tế với mặc định engine. Chưa có benchmark cho thấy batch 1 cải thiện nhận dạng; khác batch có thể làm kết quả số học/padding khác và cần thử cùng crop mới kết luận.

### 3. Engine độc lập và xử lý singleton

`origin/thanhdat:src/manga_pipeline/extraction/core_ocr.py:110–134` có `predict_batch()`, kiểm tra alignment và hỗ trợ trường hợp model trả một string khi chỉ có một crop. Main không có bước chuyển string thành list trước kiểm tra. Đây là cải thiện khả năng tương thích API trả về, không phải mô hình OCR khác.

### 4. Layout core thay đổi nhưng text boxes chưa đi vào OCR

`origin/thanhdat:src/manga_pipeline/visual/core_detector.py:298` phát hiện panel rồi detect text/bubble trong panel có context padding `max(64, image.height*0.08)`; có phân loại text, gán bubble, reading order và suppression theo điều kiện panel/bubble.

Main `visual/koharu.py:59` cũng đã có full-page + panel-crop passes; padding mặc định 24 px (`visual/config.py:13`), giữ bubble/effect/panel, không xuất lớp text. Không nên mô tả panel-crop là tính năng chỉ thanhdat có.

Adapter `integrations/core.py:32–62` xuất `text_regions`; `visual/stages.py:62` lưu vào `core_text_regions`. Git grep trên tree thanhdat chỉ thấy trường này được lưu, không có nơi đọc nó để thay `results['texts']`. `hayai.py:134–146` vẫn lấy crop từ MAGI. Thay đổi layout có thể ảnh hưởng panel, thứ tự, bubble mapping và typeset, nhưng không tự chứng minh chất lượng đọc ký tự tốt hơn.

### 5. Core OCR là opt-in

`integrations/core_config.py` mặc định `enabled=False`. `extraction/magi.py:32–34` chỉ thêm `engine='core'` khi run là v6 và core được bật. Nếu không dùng profile bật core thì vẫn chạy hàm Hayai cũ. Phải xem manifest run cụ thể để biết engine nào đã chạy.

## Những phần cần giữ của main nếu tích hợp

- Main có recovery OOM bằng giảm batch và retry có giới hạn trong hayai.py:108–140. Cả core path và Hayai path trên thanhdat đang đọc chưa có cơ chế này; thay cả file có thể mất recovery.
- Core engine yêu cầu asset là thư mục local thật. Nếu `local_assets()` không tìm receipt và trả Hub model ID, core loader sẽ coi đó là Path và báo thiếu file; main AutoModel còn có thể nạp từ Hub/cache. Muốn dùng core phải chuẩn bị và kiểm chứng asset đúng hợp đồng.
- Branch thanhdat vẫn có orchestration/config Ollama/Gemma và các policy cũ trong pipeline.py:34–71; main hiện tại dùng vLLM/Qwen và policy mới. Tách các phần OCR cần thiết thay vì thay toàn bộ pipeline bằng nhánh này.
- Test core trên nhánh chủ yếu mock model/processor, kiểm tra batch/alignment/layout; không phải phép đo CER/WER trên dữ liệu thật.

## Đề xuất bước kế tiếp

Để xác định vì sao kết quả người dùng thấy tốt hơn: đối chiếu manifest và crop đầu vào của hai run. So sánh trên cùng box/ảnh và cùng model/revision trước, lần lượt đổi batch 8→1 và chuẩn hóa khoảng trắng. Nếu khác biệt nằm ở thiếu/cắt sai chữ, cần kiểm tra kết nối text regions Koharu với OCR; thay lớp wrapper đơn thuần không giải quyết được vùng chữ bị MAGI bỏ sót. Đây mới là đề xuất, chưa thay code hoặc chạy thử.

## Nguồn code

- Main local: `src/manga_pipeline/extraction/{hayai,magi}.py`, `src/manga_pipeline/visual/{koharu,config}.py`.
- `origin/thanhdat:src/manga_pipeline/extraction/core_ocr.py`, `extraction/hayai.py`, `extraction/magi.py`.
- `origin/thanhdat:src/manga_pipeline/integrations/{core,core_config}.py`.
- `origin/thanhdat:src/manga_pipeline/visual/{core_detector,stages}.py`.
- `origin/thanhdat:tests/test_core_integration.py`, `configs/core-v6.json`, `configs/dev-stages.json`.
