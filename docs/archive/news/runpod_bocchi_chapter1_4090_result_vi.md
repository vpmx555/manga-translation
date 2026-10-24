# Kết quả Bocchi chap 1 trên RTX 4090 Secure

Ngày 05/10/2026, Pod `8zvngth4tjdrz2` đã chạy toàn bộ pipeline với 31 ảnh, bank mới và `min_margin=0.02`. Kết quả và bank đã tải về PC; trạng thái Pod được kiểm tra lại là **EXITED (Stop)**. Bank cũ trên PC vẫn giữ nguyên.

## Kết quả

| Bước | Thời gian | Lỗi kỹ thuật |
| --- | --- | --- |
| extract | 134,99 giây | 0 |
| normalize | 0,13 giây | 0 |
| scan | 49,05 giây | 0 |
| analyze | 259,43 giây | 0 |
| export | 0,40 giây | 0 |
| translate | 504,12 giây | 0 |

Pipeline mất khoảng **15,80 phút**, chưa gồm setup và tải model lần đầu. Phân tích có 291 mục; dịch có 414 mục mỗi kiểu vì bao gồm cả chữ ngoài hội thoại. Natural và localized đều đủ 414 mục, không có lỗi kỹ thuật; mỗi kiểu có 40 mục được đánh dấu cần review nội dung. Chưa thực hiện đánh giá thủ công toàn bộ chất lượng dịch.

- [Review natural](../../outputs/runpod-4090-20261005/bocchi-the-rock/chapter-001/20261005T154833Z-61352b66/translations/vi/904571db28b28973dff9/review_natural.md)
- [Review localized](../../outputs/runpod-4090-20261005/bocchi-the-rock/chapter-001/20261005T154833Z-61352b66/translations/vi/904571db28b28973dff9/review_localized.md)
- [Run manifest](../../outputs/runpod-4090-20261005/bocchi-the-rock/chapter-001/20261005T154833Z-61352b66/manifest.json)
- [Bank mới đã sao lưu](../../.runpod/backups/banks-4090-20261005/bocchi-the-rock/metadata.json)

Bank mới có **17 ID**, so với 38 ID ở lần cũ. Hai detection thuộc cluster 0 trang 3 hiện dùng ID 4, thay vì tạo ID 7 như lần cũ. ID 7 trong bank mới có thể chỉ một nhân vật khác; số ID được cấp lại nên không so trực tiếp toàn bộ ID giữa hai bank. Số ID giảm chưa chứng minh hết lỗi gán nhân vật.

Đã xác nhận bản sao bank có đủ 115 file, không thiếu crop được metadata tham chiếu, có embeddings và SHA-256 metadata trùng bản trên Pod.

## GPU và CPU

PyTorch 2.8.0+cu128 kiểm tra CUDA thành công trên RTX 4090 24 GB. Ollama 0.35.1 trả lời `OK` trong bài kiểm tra suy luận. `ollama ps` báo **100% GPU**; log ghi offload **43/43 lớp** của model chính. Mẫu đo trong khi chạy có khoảng 5,3 GB VRAM sử dụng, GPU sử dụng 21–59%, tốc độ sinh khoảng 148 token/giây ở một lượt phân tích.

CPU bận chủ yếu ở runner Ollama; runner mở 32 luồng trong khi Pod được cấp 16 vCPU. Các tác vụ chạy tuần tự và các batch nhỏ, nên không kỳ vọng GPU luôn đạt 100%. Chưa benchmark ảnh hưởng của số luồng CPU hoặc xử lý song song; không thay cấu hình giữa run.

## Chi phí và dùng lại

Pod được tạo lúc 15:31:25 UTC và yêu cầu Stop hoàn tất lúc 16:06:32 UTC: khoảng **35,10 phút**, gồm setup, tải model, kiểm tra, chạy pipeline và tải kết quả. Với giá GPU 0,74 USD/giờ, phần GPU **ước tính 0,433 USD**, chưa gồm lưu trữ. API billing chưa trả bản ghi tại thời điểm kiểm tra; đây không phải số tiền hóa đơn đã chốt.

Stop ngừng tính phí GPU. Volume Disk 50 GB vẫn giữ Python, thư viện và model để dùng lại; mức stopped là **10 USD/tháng** nếu giữ cả tháng, tính tương ứng thời gian lưu. [Giá và vòng đời ổ lưu trữ Runpod](https://docs.runpod.io/pods/storage/types). Không xóa Pod vì yêu cầu giữ setup dùng lại; xóa Pod sẽ mất ổ này.

Khi cần chạy tiếp, Start Pod hiện có, kiểm tra lại địa chỉ/port SSH trong Console nếu thay đổi, rồi dùng `runpod.ps1`. Code được đồng bộ riêng; sửa code không cài lại dependency. Tải kết quả bằng `fetch` hiện gom file vào ZIP trước khi truyền, giảm thời gian tải nhiều file nhỏ.
