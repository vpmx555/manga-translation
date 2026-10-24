# Thiết kế đã chốt: lọc normalize và dịch tiếng Việt

Run mới chạy extract → normalize → scan → analyze → export → translate. Translate cũng chạy riêng từ export đã commit của run cũ, không sửa config/snapshot/kết quả nguồn. Lần kiểm chứng đầu dùng export cũ trực tiếp nên chưa hưởng bộ lọc normalize mới.

Normalize dùng rule, không thêm model: chỉ loại cả box là SFX/âm thanh/nhãn hành động, watermark/quảng cáo hoặc UI không liên quan. Giữ câu có nghĩa, bài đăng/bình luận trong truyện và trường hợp chưa chắc; lưu review cho text nghi ngờ. Rule chung có bổ sung/ngoại lệ theo truyện; lưu text gốc, ID, vị trí và lý do loại. Rules được snapshot cho run mới; run cũ không tự đổi policy.

Dịch cả utterances và translation_only còn giữ lại. Ollama chạy hai phong cách độc lập: natural (tự nhiên, giữ nghĩa) và localized (khẩu ngữ/tiếng lóng linh hoạt). Hai JSON/review giữ cùng ID và text nguồn. Giữ hậu tố -san/-kun/-chan và speaker/addressee nguồn như kết luận bắt buộc. OCR mơ hồ được phép dịch phỏng đoán nhưng phải đánh dấu review. Lỗi kỹ thuật để partial, resume xử lý phần thiếu/lỗi; mỗi phong cách có checkpoint riêng.

Glossary theo truyện chỉ áp dụng mục đã khai báo/xác nhận. Đề xuất mới của model hiện trong review, không tự ghi thành sự thật cho chapter sau. Cho phép sửa từng câu theo phong cách; bản sửa thủ công có ưu tiên khi chạy lại. Thay glossary/chỉ dẫn tạo phiên bản công việc dịch mới, không sửa kết quả model cũ.

Bản hiện tại chưa dùng hồ sơ hoặc đồ thị nhân vật. Chừa một context-provider interface nhận story/chapter, vị trí/source ID và stable character IDs. Sau này provider có thể cấp node nhân vật và edge quan hệ/sự kiện có ảnh hưởng, giới hạn dữ kiện theo thời điểm câu. Context trả về phải được snapshot và nằm trong hash request để không làm sai resume/cache. Không dựng đồ thị tự động, không suy ra hồ sơ lâu dài ở bản đầu.

Rủi ro: rule có thể bỏ sót biến thể; metadata speaker sai có thể gây xưng hô sai; model có thể phỏng đoán sai OCR hoặc hai bản chưa khác biệt rõ. Review và kiểm chứng model thật cần đánh giá nghĩa, không chỉ JSON/schema. Việc mở rộng hồ sơ/đồ thị, detector text nhiễu và chèn chữ lên ảnh được hoãn.
