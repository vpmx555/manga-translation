# Cấu hình nhân vật mặc định cho main pipeline

Đã áp dụng cho code mặc định và bốn cấu hình `pipeline.json`, `pipeline-v6-runpod.json`, `pipeline-visual.json`, `pipeline-visual-runpod.json`.

| Tham số | Mặc định |
|---|---|
| Character detection | 0.3 |
| MAGI character-character matching | 0.81 |
| Bank max distance | 0.55 |
| Bank margin | 0 |
| Pending maximum page gap | 1: chỉ hai trang liên tiếp |
| Prototype | Trung bình L2 của tối đa 16 vector gần nhau nhất; không dùng quy tắc 60% |
| Embeddings/crop | 2–16 khi có đủ quan sát; lưu cùng thứ tự vector, crop và khóa nguồn |
| Gán trong trang | Điểm cố định, ưu tiên ID nhiều lần xuất hiện rồi distance thấp nhất, giữ ràng buộc panel |

`bank/config.py` là nguồn mặc định cho chính sách bank. `pipeline.load_config()` nạp và kiểm tra section `character_bank`; giá trị được lưu trong manifest. Worker MAGI gọi `bank.apply_config()` trước khi gán để bank cũ không giữ nhầm margin/window của các cấu hình thử trước. Reuse extraction kiểm tra cả section bank và ngưỡng detection/clustering, yêu cầu extraction mới nếu cấu hình thay đổi.

Các prototype/crop lịch sử thiếu ánh xạ không bị tự gán nhầm cặp. Khi đủ hai quan sát có mapping thật, bộ hỗ trợ được xây lại theo cách ghép cặp. Artifact và manifest lịch sử không được chỉnh sửa trong tác vụ này. Đây là thiết lập cho lượt chạy mới, không phải kết quả inference đã chạy ở matching 0.81.

Kiểm tra: 31 unit/integration checks ngắn đã qua, gồm nạp cả bốn cấu hình, chính sách bank mặc định, áp dụng lên bank cũ, phát hiện cấu hình không hợp lệ, giữ ngưỡng implicit của manifest lịch sử và các regression về prototype/panel/pending. Không chạy model hoặc dùng Runpod trong tác vụ này.
