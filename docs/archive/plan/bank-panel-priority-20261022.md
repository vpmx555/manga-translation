# Gán bank theo tần suất và distance trong trang

Thay cách xử lý lần lượt cluster theo thứ tự detection bằng kế hoạch trên cả trang:

1. Chụp điểm các cluster với bank và số lần xuất hiện đã ghi nhận trước trang hiện tại. Không cập nhật prototype giữa lúc lập kế hoạch.
2. Mỗi cluster đề xuất ứng viên còn khả dụng có distance thấp nhất, phải đạt cả distance và margin.
3. Xét các đề xuất theo số lần xuất hiện của ID giảm dần, sau đó distance tăng dần. Các nhóm cạnh tranh cùng một ID luôn ưu tiên distance thấp nhất. Nếu cùng điểm, dùng thứ tự cluster để kết quả xác định.
4. Giữ chỗ ID trên tất cả panel của cluster. Nhóm khác chung panel phải xét ứng viên còn lại; cùng ID vẫn có thể dùng ở panel không giao nhau. Các nhóm ambiguous chỉ qua postprocessor sau các match tự động.
5. Khi kế hoạch xong mới cập nhật bank, xử lý pending hoặc tạo hồ sơ mới. ID/prototype tạo trong trang được dùng làm bằng chứng từ trang kế tiếp.

Tần suất đếm số detection riêng biệt đã được gán danh tính. Khóa quan sát dùng chapter/page key + hash pixel ảnh + bbox. Chạy lại cùng ảnh/bbox không cộng lại; promote pending mang theo quan sát trang trước. Bank cũ chưa có lịch sử khóa quan sát khởi đầu với phần lịch sử đã ghi nhận, không suy số lần xuất hiện từ số crop preview hoặc vector. Số liệu này vẫn phụ thuộc các quyết định nhận diện trước đó.

Gallery diagnostic xếp hồ sơ có nhiều lần xuất hiện trước và hiển thị số lần. JSON chứa kế hoạch gán từng cluster, điểm trước/sau loại ứng viên bị chặn, tần suất ID và kết quả cuối, hỗ trợ truy lại các trường hợp tranh chấp.

23 kiểm tra nhỏ đã qua: match mạnh thắng dù đến sau, thử ứng viên kế tiếp, cluster nhiều panel, ưu tiên ID thường gặp, không tăng đếm khi replay, promote pending giữ lịch sử, cùng các kiểm tra prototype/assignment/window trước đó. Thay đổi này không bảo đảm tăng threshold sẽ luôn giảm số hồ sơ vì còn cập nhật bank qua trang và ràng buộc cạnh tranh.
