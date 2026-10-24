# Hayai OCR

Lượt chạy mới dùng `JustANormalTinkerer/hayai-ocr-v2.5-nova`, revision
`2803d0dc005fa4396ab1e7c5a472d2c450a247f7`. MAGI vẫn phát hiện vùng chữ,
nhân vật, thứ tự đọc và liên kết người nói; OCR của MAGI được tắt khi dùng Hayai.
Koharu vẫn cung cấp layout. Hayai đọc crop từng vùng chữ, batch 16,
384 patches và tối đa 128 token mỗi crop. Kết quả giữ nguyên thứ tự vùng chữ.
Main pipeline dùng OCR engine `core` theo cách của nhánh thanhdat: xử lý crop
qua `MangaOcrEngine.predict_batch()`, chuẩn hóa khoảng trắng/newline và hỗ trợ
kết quả dạng một chuỗi khi batch chỉ có một crop. Model/revision và processor
vẫn được ghim; worker tách biệt và recovery OOM có giới hạn được giữ lại.

Hayai dùng môi trường riêng vì MAGI ghim Transformers 4.44.2, chưa có SigLIP2.
Local: tạo venv riêng, cài `requirements-hayai.txt`, rồi đặt `ocr.python` trong
config hoặc biến môi trường `MANGA_HAYAI_PYTHON` tới Python của venv đó.
Runpod: `manga setup` chuẩn bị venv riêng và khóa dependencies có hash;
`manga prefetch` tải model và processor trước inference. Đồng bộ code mới trước
khi dùng các lệnh này. Không bật pod chỉ để chờ chuẩn bị tài liệu hay test offline.

Manifest cũ không có `ocr` tiếp tục dùng MAGI khi resume. Muốn đổi OCR phải
chạy lượt mới; không dùng reanalyze để coi kết quả OCR cũ là Hayai.
`result.json` extract ghi cấu hình OCR vào trường `ocr`.

Kiểm thử offline kiểm tra crop, validation và alignment của worker tách biệt.
Chưa kiểm chứng inference với weights thật hoặc chất lượng nhận dạng trên GPU.
Nguồn model: https://huggingface.co/JustANormalTinkerer/hayai-ocr-v2.5-nova
