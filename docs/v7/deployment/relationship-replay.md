# Chạy riêng relationship từ kết quả đã lưu

Nếu relationship chưa gửi POST vì đầu vào vượt context, sửa phần đóng gói và đóng băng **source bundle mới**. Không sửa source hoặc kết quả của lượt chạy cũ. Không cần chạy lại scan, analyze hay profile.

`identity_runtime.profile_input()` chỉ gửi các thuộc tính `value`, `status`, `basis`, `evidence_refs`. Các trường audit lặp lại như `supplied_evidence_refs`, `model_evidence_refs` vẫn nằm nguyên trong profile gốc để review. Bộ đóng gói tiếp tục lấy mẫu những đoạn hội thoại hoàn chỉnh theo ngân sách context; artifact `packed/*.json` ghi rõ phần giữ và bỏ.

Trên pod đang được người dùng cho phép sử dụng, từ source mới đã xác minh checksum, chạy bằng Python của môi trường pipeline hiện có:

```bash
python -B deployment/runpod/replay_relationship.py \
  --source-run /opt/manga-work/runtime/data/outputs/STORY/CHAPTER/RUN \
  --batch NEW-BATCH-KEY \
  --durable-root /workspace/manga-runtime/local-v6
```

Script kiểm tra commit của toàn bộ bước trước relationship, tạo run mới và sao chép artifact/snapshot đã xác minh. Tên run mới cùng provenance được lưu trong `jobs/state.json`. Identity/profile được tái sử dụng, không đăng ký hoặc gộp identity lần nữa. Chỉ relationship thực hiện inference, tối đa một lần cho mỗi cặp; không qualification hay request thử. Các kết quả và bank được xuất thành archive có checksum để native Syncthing đồng bộ.

`completed_with_errors` có nghĩa là vẫn có `inference_failures`; không được diễn giải cạnh fallback `unknown` là kết quả suy luận thành công. Script không stop/terminate pod. Việc khởi động script vẫn cần quyền Runpod cho tác vụ hiện tại; mặc định dự án là L40S Secure.
