# Vì sao OCR-only replay chưa tái hiện chênh lệch chất lượng với thanhdat?

## Điều đã xác nhận

Lượt local vừa chạy giữ nguyên 283 box MAGI của extraction ngày 2026-10-21. Nó chỉ thay engine gọi Hayai, batch 8→16 và chuẩn hóa khoảng trắng. Model Nova, revision, processor và cách lấy ảnh grayscale→RGB giữ nguyên. Toàn bộ 283 kết quả có cùng nội dung với artifact cũ sau chuẩn hóa khoảng trắng. Đây là kết quả của phép thử cùng crop, không phải phép so hai pipeline đầy đủ và không chứng minh hai pipeline thực tế cho chất lượng bằng nhau.

Điểm thiếu trong lần tích hợp trước: chỉ áp dụng phần nhận dạng trên crop của adapter thanhdat; chưa xác minh rằng tập crop, model và cách chạy này trùng với run thanhdat mà người dùng đang đánh giá là tốt hơn.

## Ví dụ thực tế trong Iris Zero

P22 T2/T3 chia chồng một cụm chữ viết tay. Box T3 `[106.79,183.32,170.49,268.34]` chỉ giữ phần trái một số dòng; text mới là `away Koyu You' an`. Crop không chứa toàn bộ các từ/dòng nên đổi wrapper, chuẩn hóa khoảng trắng hay tăng batch không khôi phục được chữ nằm ngoài box. T2 cũng trả kết quả sai trên chữ viết tay; không quy toàn bộ lỗi T2/T3 cho một nguyên nhân duy nhất nếu chưa thử crop đầy đủ.

- [Ảnh trang 22 và box](../../outputs/diagnostics/iris-zero-ch1-ocr-core-batch16-local-20261022/pages/022.jpg)
- [Crop T3 đã đưa vào OCR](../../outputs/diagnostics/iris-zero-ch1-ocr-core-batch16-local-20261022/crops/p022-t003.png)
- [So sánh OCR toàn chương](../../outputs/diagnostics/iris-zero-ch1-ocr-core-batch16-local-20261022/review.html)

## Cần phân biệt hai cách gọi trên thanhdat

Đã đọc `origin/thanhdat` commit `2fb3b9a`. Adapter v6 trên nhánh truyền cùng Hayai v2.5 Nova và lấy crop MAGI như main. `core_text_regions` Koharu chỉ được lưu làm bằng chứng, không thay thế box OCR. Tài liệu `docs/core_v6_integration_vi.md` của chính nhánh cũng xác nhận điều này.

Lớp `MangaOcrEngine` standalone lại khai báo default Hayai v2, 256 patches, thư mục model local riêng; hàm predict_batch nhận ảnh RGB do caller cung cấp. Adapter v6 ghi đè các default đó bằng Nova/384 và ảnh grayscale→RGB. Chưa có run thực tế của người dùng để xác định họ dùng adapter, standalone, checkpoint hay cấu hình nào; không suy rằng kết quả tốt hơn nhất định đến từ một trong các default này.

Ở lượt kiểm tra ban đầu chưa có output thực tế. Sau đó người dùng cung cấp `C:/Users/pxv23/Downloads/output_folder/output_folder` và yêu cầu chạy OCR After School để đối chiếu. Đã hoàn tất diagnostic local, không dùng Runpod và không đổi mặc định pipeline.

## Cập nhật từ output After School thực tế

Đã đọc lại 60 JPEG crop tham chiếu, 33 crop MAGI cũ và các biến thể bbox/lề trên 7 ảnh gốc có SHA256 khớp. OCR hiện tại trên 33 crop MAGI giống main cũ toàn bộ; trên đúng JPEG được cung cấp chỉ 41/60 câu khớp bản tham chiếu sau chuẩn hóa khoảng trắng. Đây là số câu khớp, không phải tỷ lệ đúng.

P03 D7 là đoạn thoại đầy đủ và đọc được bằng mắt: dùng đúng JPEG tham chiếu vẫn bị Nova nhận dạng sai. P11 D2 lại cải thiện từ `MAKEES` thành `MAKES` khi đổi crop. Batch đơn ở 384 patches không đổi kết quả cả 4 crop đối chứng; 256 patches không khắc phục đoạn dài P03. Do đó crop là một phần nguyên nhân, còn việc đổi wrapper chưa tái tạo toàn bộ workflow thực tế. JSON cung cấp không lưu cấu hình/model, nên chưa thể xác nhận nguyên nhân phần còn lại.

- [Báo cáo và giới hạn thử nghiệm](../../outputs/diagnostics/after-school-thanhdat-ocr-compare-20261022/COMPARISON.md)
- [Review ảnh/câu cạnh nhau](../../outputs/diagnostics/after-school-thanhdat-ocr-compare-20261022/review.html)
