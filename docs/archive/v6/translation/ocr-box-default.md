# Dàn chữ trong khung OCR là mặc định

Từ 2026-10-13, cấu hình mặc định cho run v6 mới là `v6.typeset_mode="ocr-box"`, bao gồm preset Runpod và preset tối ưu GPU khi prepare.

- Mỗi source OCR bbox tạo một vùng dàn; glyph nằm trong bbox OCR và safe mask của bubble, không mở rộng bbox để lấp bubble.
- Với utterance có nhiều OCR box, giữ một câu dịch chung và chia các từ liên tiếp theo diện tích usable. Không dịch riêng từng mảnh, không đảo hoặc mất token.
- Dùng font chung lớn nhất fit mọi vùng của utterance. Giữ cơ chế rút gọn có giới hạn; overflow hoặc vùng OCR không hợp lệ được ghi needs_review, không tự chuyển sang dàn cả bubble.
- Cả natural và localized, chế độ tuần tự và pool typeset đều dùng lựa chọn đã đóng băng trong config. Receipt ghi policy `ocr-box-typeset-v6.1`, phân bổ diện tích, OCR bbox/source ID và safe/glyph masks.
- Có thể chủ động chọn `v6.typeset_mode="bubble-body"` cho run mới. Run cũ không có trường mode vẫn dùng bubble-body khi resume để giữ cấu hình/checkpoint cũ.

Đã kiểm tra cú pháp và tích hợp tĩnh; chưa chạy tests, pipeline hoặc Runpod, chưa tái tạo các output cũ.
