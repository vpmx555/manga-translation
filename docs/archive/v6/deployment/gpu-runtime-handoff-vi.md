# Bàn giao tối ưu CPU/GPU v6 — 2026-10-13

Đã triển khai code theo thiết kế được xác nhận qua grill-me. Chưa chạy test suite, pipeline, model pilot hoặc benchmark; chưa truy cập hay thay đổi tài nguyên Runpod.

## Các thay đổi đã có

- Scheduler chạy song song các truyện độc lập, giữ đúng thứ tự chapter và identity bank trong mỗi truyện.
- Cổng GPU dùng chung giữa process: một visual stage độc quyền hoặc tối đa số slot LLM được cấu hình. Hàng đợi FIFO, visual không bị các request mới vượt qua.
- Chỉ unload LLM khi toàn bộ request đang được cấp slot đã kết thúc; worker của một truyện không unload model của truyện khác.
- Batch analyze/dịch thích ứng theo ngân sách context, gồm dự trữ schema/ảnh/output. Analyze vẫn giới hạn cùng trang. Giữ lịch sử semantic và bản dịch; không dịch đồng thời các batch phụ thuộc lịch sử.
- OOM giảm số request được cấp slot và có retry tài nguyên giới hạn; batch lớn được chia nhỏ. Hayai giảm batch crop khi OOM, giữ độ phân giải/patch/token limit và thứ tự crop.
- Repair receipt phân biệt lỗi tài nguyên với lỗi semantic; lỗi tài nguyên không tự biến thành unknown/unresolved hoặc needs_review.
- Typeset chạy pool theo trang, cả hai style của mỗi trang vẫn theo thứ tự; cache font và metric có giới hạn, khóa ghi target và ghép kết quả đúng thứ tự.
- Telemetry gồm thời gian chờ, thời gian stage, CPU của process chính, thời gian load/prompt/decode và mẫu GPU. Dừng sampler trước khi archive audit.
- Khóa batch trước khi tạo coordinator; khóa global dạng shared cho worker và khóa riêng từng truyện. Setup/update/stop không được chen vào batch đang chạy.

## Bật chế độ mới

Thêm `--gpu-preset a40`, `--gpu-preset rtx4090` hoặc `--gpu-preset rtx5090` vào lệnh **prepare** hiện có của `deployment/runpod/v6.py` hoặc `deployment/runpod/local_v6.py`.

Lệnh prepare đóng băng runtime config vào plan mới và chỉ chuẩn bị local. Không thêm flag vào launch, không chỉnh config của plan đã hash, không tái sử dụng batch cũ để ghi đè kết quả. Khi không chỉ định preset, chế độ tuần tự cũ vẫn có thể dùng.

Mức khởi đầu: 3 story workers, 2 LLM slots; typeset 2 thread/truyện cho A40 và 4090, 3 cho 5090; LLM CPU threads tối đa 8 và thư viện CPU tối đa 4/truyện. Code giảm trần theo số CPU khả dụng. Các mức này chưa được benchmark, không phải khẳng định tối ưu cho từng GPU.

Xem [gpu-runtime.md](gpu-runtime.md) để biết lệnh prepare đầy đủ, trường runtime config, telemetry và recovery.

## Giới hạn và bước cần làm khi được yêu cầu chạy

- Chỉ kiểm tra cú pháp AST, whitespace và tích hợp tĩnh trong lượt này. Đã viết 5 regression test cho slot/FIFO/exclusivity/cleanup/quarantine nhưng chưa chạy.
- Chưa xác nhận mức tăng tốc hoặc GPU utilization trên GPU thật. Pool typeset là thread pool; phần tính toán Python vẫn chịu GIL.
- Giảm client admission không thay đổi allocation KV/slot cố định của server. Nếu một request vẫn OOM, cần chuẩn bị preset mới với ít server slot hơn; code không tự giảm context hay đổi model.
- Visual worker đơn lẻ hoặc model-load OOM không có batch nhỏ hơn để phục hồi; giữ failure/checkpoint để resume. Không tự fallback CPU.
- Mất kết nối có thể để inference còn chạy trên server: coordinator khóa admission, lần resume controller khởi động lại managed server ở ranh giới idle. Phải dừng mọi worker cũ, gồm worker visual mồ côi, trước khi resume.
- Theo lựa chọn Q3=B, nghiệm thu kiểm tra cấu trúc, ID, coverage, dependency và integrity; không có bước đánh giá chất lượng ngữ nghĩa so với bản dịch cũ. Batching mới có thể thay đổi câu dịch.
- Không triển khai worker/model visual sống xuyên chapter hoặc backend GPU typography. Đây là các refactor riêng; không coi chúng đã có chỉ từ scheduler mới.

Khi có yêu cầu kiểm chứng riêng, chạy regression nhẹ trước, rồi benchmark có giới hạn trên GPU được chỉ định, so sánh thời gian/trang và chi phí/trang từ cùng input/model/revision. Không lấy utilization meter cao làm bằng chứng throughput tốt hơn.
