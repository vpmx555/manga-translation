# Iris Zero chapter 1 — kiểm chứng Runpod tới analyze

Người dùng trực tiếp yêu cầu chạy test bằng Runpod, sau đó giới hạn vào Iris Zero chapter 1 tới analyze. Quyền này thay trạng thái chưa được phép chạy trong kế hoạch kiểm chứng trước.

- Input: `D:\download\EN-data\Iris Zero\EN\Chapter 1`, 44 ảnh, 10.178.430 bytes ở lượt kiểm kê ban đầu. Manifest hash được đóng băng bằng controller trước upload.
- Scope: pipeline v6 tới `analyze`, gồm các stage phụ thuộc; không chạy dịch/typeset sau analyze.
- Engine: vLLM 0.31.0, `cyankiwi/Qwen3.5-27B-AWQ-4bit`, revision `5cba9b5f1bca69fab9b75c88ae7817ae22d7d898`, context 32K, RTX 4090 duy nhất, không CPU offload.
- Quy trình: [local working-set + Syncthing native](runpod-v6-agent-operational-plan.md), dùng `local_v6.py` qua `runpod-v6.ps1`. Source/env/runtime đều ở `/opt/manga-work`; volume chỉ giữ durable archive/state. Dùng boot image tương thích đã pin, không chờ build/push image lớn làm điều kiện mặc định.
- Chuẩn bị CPU đang thực hiện chỉ phục vụ kiểm tra môi trường/khóa dependency vLLM. Plan workspace `.runpod/v6/iris-zero-ch1-vllm-20261015` đã bị thay thế trước launch; phải tạo batch local working-set mới sau khi chốt các sửa quy trình. Chưa có Pod mới được tạo ở thời điểm cập nhật này.
- Không sửa/start pod cũ chỉ vì thấy nó trong inventory. Test dùng tài nguyên do task này tạo, trừ khi người dùng chỉ định tài nguyên khác.
- Tạo đúng một pod 4090 sau khi boot image, locks, input, native pairing, SSH và plan sẵn sàng. Đọc lại giá/capacity và CUDA floor của dependency lock trước create, báo giá/giờ trước khi gọi create; không tự đổi GPU.
- Giữ kết quả, JSON, visualization, review tự sinh, bank và audit, kể cả partial/failed. Native sync đưa dữ liệu về folder mới `outputs/runpod-<batch>`; xác minh archive/inventory cùng terminal state trước teardown.
- Theo dõi tiến độ thật; chẩn đoán sau 10 phút không tiến triển. Không sửa code/prompt/config giữa workload hoặc lặp retry vô hạn.
- Sau khi backup ngoài pod được xác minh, terminate pod task này để kết thúc phí theo phạm vi test dùng một lần. Không xóa tài nguyên có sẵn của tài khoản.

Kiểm tra chuẩn bị đã có: 7 regression model-free của `test_vllm_inference_policy.py` đạt trong 0,021 giây. Chưa có bằng chứng GPU/VRAM/analyze thực ở thời điểm tạo plan này.
