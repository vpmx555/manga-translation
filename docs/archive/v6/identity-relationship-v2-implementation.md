# Báo cáo triển khai identity–relationship v2

Đã triển khai theo yêu cầu và các quyết định trong
[bản thiết kế](../plans/identity-relationship-v2-design.md).

## Kết quả

Pipeline mới tách `identity` thành:

1. **`build_identity`**: bảo vệ identity đã xác nhận, liên kết theo bằng chứng gọi
   tên hoặc min-crop `< 0.38`, sau đó tạo/cập nhật profile nhân vật.
2. **`relationship`**: suy luận các loại quan hệ xã hội và hai cạnh xưng hô độc lập,
   sử dụng profile đã đóng băng và giao tiếp trực tiếp.

`export` và `translate` sử dụng kết quả đã commit. Translate không tự xây graph
đối với flow v2. Run cũ không có marker v2 vẫn giữ stage và số thứ tự cũ.

## Các hành vi đã thực hiện

- Nhánh ảnh chỉ so với Magi ID chính của mỗi identity, chọn theo số lần xuất hiện
  trong toàn bộ lịch sử bank. Chọn ứng viên có min-crop thấp nhất trong ngưỡng.
- Không tự gộp hai identity đã xác nhận; không gộp dây chuyền qua thành viên phụ.
- Bank MAGI, vector và crop nguồn không bị chỉnh sửa bởi hai stage ngữ nghĩa.
- Profile dùng tối đa 10 crop hợp lệ, không trùng, gần prototype của Magi ID chính.
- Profile không có trường thói quen dùng từ hoặc sắc thái. Phân biệt thuộc tính
  xác nhận, giả thuyết từ ảnh và unknown; giữ bằng chứng của từng thuộc tính.
- Mặc định yêu cầu 8 detection riêng biệt trong chapter để tham gia graph;
  giữ các identity đã có trong graph v2 trước đó.
- Cập nhật profile chỉ khi có lời thoại hoặc mô tả nhắc tên mới, không chỉ vì
  đổi crop, đổi Magi ID chính hoặc đổi số scene.
- Một request cho một profile; một request cho một cặp có giao tiếp trực tiếp.
- Mỗi chiều xưng hô có nguồn, đích, tự xưng, cách gọi, trạng thái và bằng chứng
  riêng. Kiểm tra tên được gọi phải thuộc identity đích; không chấp nhận đại từ
  tiếng Nhật thay cho bảng xưng hô tiếng Việt.
- Quan hệ xã hội có loại chuẩn, vai trò hai bên, tính đối xứng/hướng và evidence.
- Lưu phiên bản profile/quan hệ, khóa khi công bố và giữ quyết định xác nhận khi
  có mâu thuẫn; ghi cả đề xuất bị giữ lại để review.
- Mỗi stage tối đa 8 request đồng thời, không retry worker/provider/scheduler.
  OOM chỉ hạ admission cho công việc tiếp theo, không chạy lại request lỗi.
- Đóng gói và tokenize cả ảnh, prompt, schema trước khi gửi; giữ nguyên segment
  được chọn và ghi các segment bị bỏ. Nếu phần bắt buộc vượt context, không POST.
- Resume sử dụng receipt/kết quả hợp lệ, không gửi lại request thất bại hoặc chưa
  rõ đã hoàn thành. Receipt/crop bị hỏng là lỗi integrity, không được dùng như
  một kết quả thành công hoặc che đi bằng fallback.
- Lỗi suy luận được ghi vào `inference_failures` để review; không làm mất kết quả
  của các request khác và không chặn export khi fallback hợp lệ/unknown đã được ghi.
- Không dùng graph v1 làm seed hoặc fallback cho v2. Dữ liệu v1 được giữ nguyên.

## Tích hợp và cấu hình

Các module chính:

- [identity_v2.py](../../src/manga_pipeline/v6/identity_v2.py): liên kết identity và
  bảo vệ registry.
- [identity_profiles.py](../../src/manga_pipeline/v6/identity_profiles.py): profile,
  điều kiện cập nhật và lịch sử phiên bản.
- [identity_runtime.py](../../src/manga_pipeline/v6/identity_runtime.py): đóng băng
  dữ liệu, giữ crop, đóng gói token và hàng đợi không retry.
- [relationship_v2.py](../../src/manga_pipeline/v6/relationship_v2.py): graph,
  validation, xưng hô từng chiều, fallback và biểu diễn Mermaid.

Đã cập nhật DAG/RunStore, CLI theo danh sách stage, review đơn giản và review chi
tiết, export/translation, checkpoint integrity, verifier và importer của Runpod.
Import qua `inpaint` giữa hai hợp đồng stage cũ/mới bị từ chối; cần một run mới
với boundary upstream (`scan` hoặc `normalize`). Không chỉnh batch đã đóng băng.

Bốn cấu hình đã được kiểm tra bằng `load_config`:

- `configs/pipeline-v6-runpod.json`
- `configs/pipeline-visual-runpod.json`
- `configs/pipeline-visual.json`
- `configs/pipeline.json`

Mặc định mới:

| Trường | Giá trị |
|---|---|
| `v6.identity_relationship_policy` | `identity-relationship-v2` |
| `v6.identity_visual_min_crop` | `0.38` — so sánh nghiêm ngặt `<` |
| `v6.profile_crops` | `10` |
| `v6.profile_concurrency` | `8` |
| `v6.relationship_concurrency` | `8` |
| `v6.profile_output_tokens` | `4096` |
| `v6.relationship_output_tokens` | `4096` |
| `vllm.max_images_per_prompt` | `10` |

Giới hạn ảnh được đưa vào command vLLM, engine signature và qualification
fingerprint. Cú pháp đã đối chiếu với
[tài liệu vLLM 0.31.0](https://docs.vllm.ai/en/v0.31.0/cli/serve/).

Mỗi stage có `review.md`, frozen plan, packed evidence, `requests/`, `decisions/`,
`request-summary.json` và ảnh tham chiếu. Review hiển thị số MAGI; graph có sơ đồ
Mermaid hướng riêng A→B và B→A. Review không phải approval gate.

## Kiểm tra

**21 test mới đều qua** trong
[test_identity_relationship_v2.py](../../tests/test_identity_relationship_v2.py).
Các test dùng ảnh tổng hợp, vector giữ lại, provider giả và checkpoint RunStore
thật; không tải hoặc chạy model.

Các trường hợp kiểm tra gồm:

- Ngưỡng min-crop nghiêm ngặt, chọn identity gần nhất và chọn Magi chính theo
  lịch sử thay vì số detection chapter hiện tại.
- Bảo vệ identity có tên và không dùng thành viên phụ làm cầu nối.
- Profile nhận đúng tối đa 10 ảnh; điều kiện cập nhật và bảo vệ thuộc tính xác nhận.
- Chỉ có mention không tạo cặp giao tiếp; hai chiều xưng hô có thể khác nhau.
- Loại tên đích sai và đại từ tiếng Nhật không hợp lệ.
- OOM, output lỗi, context quá dài và request uncertain không gây retry.
- Flow thật vẫn commit hai stage và export khi suy luận lỗi; resume không gọi lại.
- Phát hiện crop/decision bị sửa; không nhận dữ liệu hỏng làm kết quả thành công.
- Giới hạn ảnh vLLM có trong signature; không import lẫn hợp đồng stage cũ/mới.

Bộ kiểm tra mở rộng cuối cùng chạy **103 test trong khoảng 17 giây**:
**100 qua, 3 thất bại**. Ba test còn lại thuộc scene/anchor và đều tái hiện khi
loại cấu hình v2 khỏi fixture:

| Test | Khác biệt hiện có |
|---|---|
| `SceneTests.test_truncation_retry_is_durable_and_reserve_grows` | Test chờ reserve `4096 → 8192`; scene hiện dùng `8192 → 8192`. |
| `IsolationTests.test_exhausted_scene_retry_does_not_reset_on_resume` | Test chờ 2 lượt; scene hiện có tối đa 3 lượt. |
| `IsolationTests.test_known_name_owner_and_anchor_cannot_be_changed` | Một kỳ vọng khóa speaker anchor không khớp cơ chế inference-first hiện tại. |

Không sửa scene hoặc quy tắc anchor trong lần triển khai này. Bộ kiểm tra mở rộng
bao gồm pipeline v6, translation/relationship cũ, review, CLI progress,
storage/resume/timing, independent batches và mixed concurrency receipt.
Kiểm tra cú pháp bổ sung đã qua cho 15 file Python được tích hợp.

## Giới hạn và công việc tiếp theo

Chưa chạy vLLM thật, chưa chạy lại Iris Zero/Bocchi và không truy cập hay tạo pod
Runpod trong lần triển khai này. Vì vậy chưa có kết luận về chất lượng profile,
độ chính xác graph, VRAM hoặc throughput thực tế của gói 10 ảnh với 8 request.

Ngưỡng min-crop dựa vào một cặp vector vẫn có thể gộp sai; code giữ đầy đủ ứng
viên và crop để review. Identity xác nhận sai từ trước không tự được tách/sửa.
Lần kiểm tra inference tiếp theo cần run/batch mới và yêu cầu Runpod riêng.
