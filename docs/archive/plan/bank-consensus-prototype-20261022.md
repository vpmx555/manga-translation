# Prototype trung bình từ nhóm vector gần nhau

Đã triển khai `closest-majority-mean-v1` trong `src/manga_pipeline/bank/prototypes.py`.

- Chuẩn hóa L2 từng vector crop.
- Với n vector, chọn nhóm k = ceil(0.6 × n), tối thiểu 2 nếu có, tối đa n. Xét các nhóm k láng giềng gần nhất quanh từng vector; chọn nhóm có trung bình khoảng cách từng cặp nhỏ nhất.
- Lấy trung bình nhóm đã chọn và chuẩn hóa L2 lần nữa. Đây là prototype dùng khi tính distance, thay cho công thức 0.6 nearest + 0.4 mean-nearest-three trước đây.
- Áp dụng cùng cách tổng hợp cho cluster quan sát và các vector bank/pending. Nhóm chỉ có hai vector vẫn dùng cả hai: chưa có đủ bằng chứng để chọn vector nào là ngoại lệ.
- Bank vẫn lưu tối đa 8 vector hỗ trợ để có thể cập nhật hoặc disable từng vector. Prototype trung bình được tính từ các vector còn hiệu lực khi match; không thay ma trận bằng một vector duy nhất vì sẽ mất bằng chứng cập nhật.
- Khi vượt giới hạn 8 vector hỗ trợ, giữ nhóm compact nhất thay vì chọn các vector xa nhau nhất. Vector gần nhau vẫn được bổ sung làm bằng chứng; chỉ bỏ trùng gần như chính xác (distance <= 1e-6).
- Crop preview vẫn giữ tối đa 10 và được thu thập độc lập. Không đổi ngưỡng distance/margin/pending từ yêu cầu trước.

Lưu ý: gần nhau là độ nhất quán embedding, không bảo đảm crop rõ mặt hoặc đúng nhân vật. Một nhóm nhận diện nhầm nhưng chiếm đa số vẫn có thể tạo prototype sai. Một centroid có thể giảm khả năng bao phủ nhiều kiểu ngoại hình khác nhau. Chưa thêm bộ chấm chất lượng ảnh hay huấn luyện lại MAGI.

18 kiểm tra nhỏ của prototype, assignment, merge và pending window đã qua. Đã cập nhật fixture cũ để phản ánh cửa sổ hai trang liên tiếp hiện hành. Artifact/bank diagnostic lịch sử không được viết lại; replay mới dùng thư mục riêng. Distance có ý nghĩa khác sau thay đổi công thức, cần đối chiếu diagnostic mới trước khi chốt ngưỡng vận hành.

Diagnostic Iris Zero 44 trang, margin 0, pending gap 1: distance 0.4 có 18 hồ sơ / 210 detection có số / 220 chưa có số; distance 0.45 có 19 hồ sơ / 223 có số / 207 chưa có số. Đây là số liệu gán số, chưa chứng minh nhận diện chính xác hơn. Review tại `outputs/diagnostics/iris-zero-ch1-consensus-prototype-gap1-20261022/review.html`.
