# Áp dụng cách OCR thanhdat vào main pipeline

Đã tích hợp hành vi từ `origin/thanhdat` commit `2fb3b9a`: `MangaOcrEngine.predict_batch()`, chuyển chuỗi singleton về list và chuẩn hóa khoảng trắng bằng `" ".join(text.split())`. Không merge toàn bộ nhánh.

Mặc định `ocr.engine=core`, `ocr.batch_size=16` ở code và bốn cấu hình pipeline, theo yêu cầu tăng batch sau khi tích hợp. Model vẫn là `JustANormalTinkerer/hayai-ocr-v2.5-nova`, revision `2803d0dc005fa4396ab1e7c5a472d2c450a247f7`; processor SigLIP2 và revision giữ nguyên, 384 patches, tối đa 128 token/crop. Tham số model/processor trùng adapter v6 của thanhdat; batch mới là 16 theo lựa chọn người dùng, thay cho profile batch 1 trên nhánh.

Engine mới nằm tại `src/manga_pipeline/extraction/core_ocr.py`; worker `hayai.py` gọi engine này trên các crop MAGI theo thứ tự. Kiểm tra asset và pinned revision của main được giữ lại, hỗ trợ cả asset receipt local và nguồn model qua cơ chế hiện có. Worker vẫn ở môi trường Hayai riêng. Recovery OOM có giới hạn, giảm batch và giữ vị trí đang đọc vẫn hoạt động; engine bỏ tham chiếu input tensor trước khi chuyển lỗi về caller.

Không thay text detection, không chuyển sang vùng text Koharu, không thay layout hoặc LLM; các phần đó không phải cách OCR nhận dạng mà adapter thanhdat đang dùng. Cấu hình bank mới nhất (matching MAGI 0.81, detection 0.3, bank distance 0.55) được giữ nguyên.

13 kiểm tra ngắn liên quan OCR/config đã qua: crop, cấu hình, worker isolated alignment, chuẩn hóa whitespace, output singleton, từ chối output sai/truncated, thứ tự nhiều crop/trang và liên kết nhân vật, bounded OOM recovery, batch 16 với batch cuối thiếu, đồng nhất các cấu hình main. Chưa chạy inference weights thật để đánh giá độ chính xác OCR sau tích hợp.

Kết quả OCR lịch sử không được viết lại. Lượt chạy mới ghi `engine=core` và batch size vào cấu hình; reuse extraction hiện có kiểm tra sự thay đổi cấu hình OCR, không coi OCR cũ là kết quả của engine mới.
