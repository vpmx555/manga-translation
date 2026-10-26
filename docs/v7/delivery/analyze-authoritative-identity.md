# Triển khai analyze-authoritative identity — 24/10/2026

Đã áp dụng [thiết kế đã chốt](../identity/analyze-authoritative-build-identity-design.md)
vào defaults và bốn config pipeline được cung cấp trong repository.

## Hành vi

- Chỉ analyze bật thinking: context65536, output12288 token gồm thinking + JSON,
  tối đa8 request đồng thời, timeout1200 giây. Stage khác dùng context32768 và
  thinking tắt. vLLM có ceiling65536 và reasoning parser qwen3.
- build_identity đăng ký tên bằng code từ owner do analyze xác nhận và alias
  group do scan tạo. Không gọi model gán lại tên/owner, không dùng panel.
- Mỗi MAGI chọn nhóm có nhiều occurrence khác nhau nhất. Hòa thì xét lần lượt
  introduction, direct_address, reference, rồi occurrence xuất hiện sớm nhất.
  Nhóm cạnh tranh được giữ để review, không bị union thành một nhóm.
- Gate tạo mới giữ nguyên: >=8 crop thật trong chapter và có tên đã gán owner.
  Giữ bảo vệ identity có sẵn, cannot-links và visual linkage tới prototype chính.
- Profile vẫn dùng bank crops và thoại. Relationship sử dụng mapping identity
  bổ sung; không sửa speaker/addressee/mentions/owner gốc của analyze trong
  export hoặc kết quả dịch. Bản nhìn dành cho downstream là một bản sao.
- Review HTML/JSON/Markdown hiển thị đăng ký, phiếu, nhóm cạnh tranh và mục
  chưa đạt gate. Các mục chưa đủ điều kiện không hiển thị như đã xác nhận.

## Vận hành

Các tham số mới nằm trong `llm`: `analyze_thinking`, `analyze_num_ctx`,
`analyze_num_predict`, `analyze_timeout`. Policy đăng ký là
`v6.identity_name_binding_policy=analyze-authoritative-v3`.
Cache request/tokenizer và journal phân biệt profile stage.
File cấu hình lịch sử thiếu các khóa context/thinking mới giữ hành vi cũ.

Phải freeze batch mới khi triển khai nguồn/config mới. Không sửa batch cũ.
Chứng nhận concurrency32k trước đây không đủ: chuẩn bị qualification mới cho
analyze64k/thinking/8 request cùng ceiling admission hiện hành. Test offline
không chứng minh L40S đủ KV cache hoặc chất lượng suy luận của model.

## Kiểm tra

Bộ regression liên quan: **205 test, 203 đạt, 2 skipped**. Không gọi model.

Đã kiểm tra tích hợp không gọi model từ identity qua profile, relationship,
export và translate; xác nhận analyze không bị sửa, name-binding không có POST
và profile không nhận panel. Kiểm tra provider giả lập xác nhận thinking,
tokenizer, output reserve và timeout theo stage.

Replay tĩnh trên analyze đã lưu của Iris Zero tạo đăng ký riêng cho MAGI2
(Toru), MAGI5 (Koyuki), MAGI12 (Hijiri); MAGI15 không bị tự gán tên khi analyze
chưa xác nhận owner. Đây là kiểm tra logic, không phải kết quả inference mới.

Không tạo/kết nối pod hoặc chạy model cho lượt triển khai này.

## Cập nhật kiểm thử Runpod sau triển khai

Người dùng đã yêu cầu kiểm thử trên Runpod, sau đó yêu cầu tiếp tục và giữ pod.
Batch r8 dùng sampler riêng cho analyze thinking: temperature1.0 và presence
penalty1.5 theo model card Qwen3.5. Các stage không thinking giữ sampler cũ.
Provider, request fingerprint và concurrency qualification đều dùng cùng profile.
28 test liên quan provider/identity/qualification đạt sau thay đổi sampler.

Sửa vận hành: coordinator preflight được tách theo MANGA_BATCH_ID để lease của
batch bị ngắt không chặn batch sau. Các gate live-worker, nguồn đóng băng và
qualification vẫn giữ nguyên. Test thật chưa hoàn tất; không suy diễn từ test
giả lập rằng cấu hình GPU hoặc chất lượng thinking đã đạt.
