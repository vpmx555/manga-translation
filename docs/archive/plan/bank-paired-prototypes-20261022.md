# Prototype và crop ghép cặp 2–16 — thay thế quy tắc 60%

Theo yêu cầu mới: dùng tối đa 16 embedding gần nhau nhất, lấy trung bình rồi chuẩn hóa L2. Khi có từ 2 đến 16 quan sát, dùng tất cả; khi vượt 16, chọn nhóm 16 compact nhất bằng khoảng cách từng cặp. Không còn chọn 60%. Một quan sát đơn lẻ ở pending; không nhân bản ảnh/vector để đạt mức tối thiểu.

Bank mới lưu cùng thứ tự ba danh sách: ma trận vector `character_<id>` trong vectors.npz, `prototype_crop_paths`, và `prototype_source_keys`. `crop_paths` chính là các crop của vector đã chọn, tối đa 16. Mỗi khóa nguồn dựa trên ảnh/trang/bbox, tránh đưa lại cùng quan sát vào pool khi replay. Sau cập nhật hoặc merge, vector và crop luôn được chọn bằng cùng một tập chỉ số. Disable crop ghép cặp cũng disable vector tương ứng.

Bank cũ không có ánh xạ vector–crop: không tự suy ánh xạ từ thứ tự preview. Vector cũ được giữ tạm với đường dẫn null; khi có đủ hai mẫu ghép cặp thực, xây lại bộ hỗ trợ từ các mẫu có nguồn rõ ràng. Các artifact/bank lịch sử không được sửa trực tiếp bởi diagnostic mới.

Giới hạn này áp dụng cho các crop được bank tham chiếu. Các file preview cũ không còn được chọn có thể vẫn tồn tại trên đĩa; không tự xóa file lịch sử. Hệ thống không chấm độ rõ mặt; “gần nhau” chỉ là tính nhất quán embedding.

Bocchi chapter 1 đã chạy lại MAGI với character_detection_threshold 0.3, dùng panel Koharu đã kiểm tra hash, không chạy OCR/analyze. Detection mới có 245 khung, extraction cũ có 224. Diagnostic bank mới dùng distance 0.5 và 0.55, margin 0, pending gap 1 và ưu tiên ID thường gặp trong panel.
