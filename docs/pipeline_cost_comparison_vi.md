# Chi phí pipeline cũ — cơ sở quyết định Q6

Phân tích tĩnh code và log đã lưu; không chạy thêm inference. Dữ liệu chapter: 13 trang, 107 utterance, model `gemma4:e4b-it-q4_K_M`, context 8192, tối đa 6 targets/request, overlap 2 và một ảnh/request. Q6 chưa được chốt.

## Kết luận phục vụ quyết định

**Trong pipeline cũ, mỗi bước 3A/3B/3C có quy mô request gần bằng lượt phân boundary đầu tiên.** Chúng không phải các bước rẻ chỉ vì nằm sau phân scene: mỗi bước lại gửi ảnh, context và danh sách ứng viên cho cùng model.

Nếu gọi “phân scene” là cả mô tả panel và phân boundary, tổng phần này nặng hơn một bước 3A hoặc 3B hoặc 3C. Tuy nhiên, tổng ba bước 3A–3C vẫn có thể ngang hoặc vượt phần phân scene. Chưa có lượt chạy toàn chapter đến hết 3A–3C để đo kết luận này trực tiếp.

Đối với pipeline mới, hai thay đổi tiết kiệm đáng kể là bỏ phần scene và gộp suy luận text thành một task theo batch. Với batch 6, ba bước cũ có khoảng **132 request ban đầu**, còn một lượt text tích hợp có mức cơ sở **18 request** cho 107 câu. Đây là chênh lệch số request, **không phải cam kết nhanh hơn 7,3 lần**: prompt, output và việc chia batch vẫn ảnh hưởng thời gian. Phát hiện tên, phân loại mention và ghép tên bằng panel là các chi phí bổ sung riêng.

## Số đo có sẵn

### Chapter thực tế

`outputs/chapter1-stage3-20261001/check.json` đang ở trạng thái paused sau scenes, chưa có output Stage 3 hoàn chỉnh. Snapshot `steps.failed.output.events` của một lần chạy trước đó ghi:

| Task | Số request/event | Thời gian inference được ghi |
| --- | ---: | ---: |
| Visual observations | 53 event, trong đó 51 cache hit | 339 giây cho hai request mới |
| Boundary classification | 50 request mới | 10.578 giây, khoảng **176 phút** |

Không dùng 339 giây để suy ra visual rẻ: 51 kết quả đã được lấy từ cache. Snapshot này cũng chưa đại diện toàn bộ thời gian hoàn thành scenes, vì có lỗi checkpoint và resume sau đó.

Cache thành công trong `outputs/chapter1-stage3-20261001/internal/cache/` có:

| Task | Số kết quả cache | Trung bình/request | Trung vị/request | Trung bình prompt processing | Trung bình generation |
| --- | ---: | ---: | ---: | ---: | ---: |
| Visual | 55 | 73 giây | 65 giây | 49 giây | 24 giây |
| Boundaries | 69 | 193 giây | 190 giây | 157 giây | 35 giây |

Các entry này tích lũy từ các lần chạy/request khác nhau; **không cộng chúng để tuyên bố thời gian của một lượt chạy duy nhất**. Chúng hữu ích để hình dung mức chi phí request trên máy này. Riêng boundary, khoảng **82% tổng thời gian** trong cache nằm ở prompt processing (`prompt_eval_duration`).

### Smoke test nhỏ: so sánh cùng loại request

Cache trong `runs/reasoning/` có một mẫu thành công cho mỗi task, cùng model và có ảnh:

| Task | Targets trong request | Thời gian | Tỷ lệ với request boundary |
| --- | ---: | ---: | ---: |
| Boundary | 1 gap giữa hai câu | 85,9 giây | 1,00× |
| 3A: speaker | 2 câu | 87,6 giây | 1,02× |
| 3B: identity/name | 2 câu | 88,2 giây | 1,03× |
| 3C: addressee | 2 câu | 102,9 giây | 1,20× |

Đây là **một mẫu/task**, không đủ để khẳng định các tỷ lệ sẽ giữ nguyên trên toàn chapter. Gap boundary cũng liên quan hai câu; số target giữa các task không hoàn toàn cùng đơn vị. Nhưng số đo cho thấy 3A–3C cũ không có lợi thế tốc độ lớn ở mức một request so với boundary.

## Ước tính theo cách chia request của code cũ

Mô phỏng quy tắc chia group theo `max_targets=6` và giới hạn `max_images=1`, dùng chính output chapter hiện có. Chưa tính chia thêm do budget, retry hoặc response không hợp lệ.

| Bước | Khối lượng | Request cơ sở | So với boundary lượt đầu |
| --- | --- | ---: | --- |
| Stage 1: MAGI + bank | 13 ảnh, extraction và embedding | Không cùng loại request Ollama | Chưa có timing riêng đủ dùng để so sánh |
| Stage 2: normalization | 107 utterance, quy tắc Python | 0 request model | Không có chi phí inference LLM |
| Visual trước scene | 53 block panel/page | 53 | Chi phí cộng thêm của nhánh scene |
| Boundary lượt đầu | 106 gap | 46 | Mốc so sánh |
| Boundary review | 17 gap đã có review | 14 | Cộng thêm khoảng 30% số request lượt đầu |
| 3A | Toàn bộ 107 câu | 44 | Gần bằng lượt boundary đầu về số request |
| 3B | Toàn bộ 107 câu | 44 | Gần bằng lượt boundary đầu về số request |
| 3C | Toàn bộ 107 câu | 44 | Gần bằng lượt boundary đầu về số request |
| 3D | Block cùng page/scene và seam | 1 trên partition hiện có | Con số hiện tại không đại diện partition hợp lý |
| Local rerun sau 3D | Vùng quanh các câu đổi thứ tự | Phụ thuộc thay đổi | Có thể gọi lại speaker/addressee và đôi khi identity |

Vì sao batch 6 không chỉ tạo 18 request cho từng bước cũ? Khi một group có target nằm ở nhiều trang nhưng chỉ được gửi một ảnh, runner chia đôi group liên tục. Budget/schema errors có thể chia thêm. Nhánh text-only mới sẽ không có ràng buộc chia group theo số ảnh này.

**Lưu ý với 3D:** partition đã lưu có 102 scene cho 107 câu, nên gần hết group bị vụn thành singleton và ordering hầu như không còn việc. Không lấy một request đó để kết luận 3D luôn rẻ. Khi scene dài hơn, ordering có nhiều block/seam và còn có thể kích hoạt local rerun.

## Hai kịch bản thời gian để hình dung quy mô

Không phải benchmark hoàn chỉnh hoặc khoảng tin cậy. Đây chỉ là phép nhân số request cơ sở với hai mức thời gian/request đã có quy mô tương ứng trong log.

- Với 44 request/bước và giả định 90 giây/request: mỗi 3A/3B/3C khoảng **66 phút**; ba bước khoảng **198 phút**.
- Với 44 request/bước và giả định 190 giây/request: mỗi bước khoảng **139 phút**; ba bước khoảng **418 phút**.
- Nếu dùng trung bình cache để dự toán nhánh scene không cache: `53 × 73,5 giây + (46 + 14) × 192,6 giây ≈ 258 phút`, khoảng **4,3 giờ**, chưa tính retry/chia thêm và các overhead khác.

Mức 90 giây được dùng để minh họa quy mô smoke test; mức 190 giây minh họa quy mô boundary chapter. Chúng **không phải số đo 3A–3C chapter** hay hai cận chắc chắn. 3C có thể sinh output dài hơn, và model có thể xử lý prompt khác nhau.

## Ý nghĩa cho Q6

**A — Suy luận toàn bộ câu bằng text tích hợp:** vẫn xử lý người nói/người nghe cho câu không có tên; tránh ba lượt riêng và tránh ảnh trong lượt hội thoại chung. Cơ sở batch 6 là 18 request, nhưng thời gian cần đo bằng pilot text-only. Không được lấy thời gian VLM cũ chia theo một hệ số cố định để dự đoán.

**B — Chỉ xử lý câu có tên và câu lân cận:** ít target hơn, nhưng các câu không được chọn giữ người nói từ MAGI và người nghe chưa rõ. Số câu được chọn là hợp của các cửa sổ lân cận, nên cần deduplicate; không phải cứ có K câu chứa tên là luôn chỉ có K request.

Khuyến nghị vẫn là A nếu mục tiêu gồm người nói/người nghe cho toàn chapter, nhưng **gộp thành một lượt text theo batch**, thay vì giữ ba lượt 3A–3C cũ. Model nhỏ tìm tên và nhánh VLM ghép tên–ID chỉ chạy trên trường hợp được chọn; không tạo visual observations cho mọi panel nữa.

Q4 đã chốt: `mention_type` cho từng tên, tách khỏi `addressee_type` của câu. Q5 đã bổ sung: ứng viên ghép tên phải là nhân vật MAGI detect sẵn trong panel; ưu tiên ID có liên kết speaker đáng ngờ ở câu tường thuật. Chính sách xác nhận/lưu mapping và xử lý xung đột vẫn cần làm rõ ở vòng thiết kế sau. Q6 đang chờ quyết định sau khi đọc so sánh này.
