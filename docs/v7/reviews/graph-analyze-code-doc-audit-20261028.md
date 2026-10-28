# Đối chiếu code với docs graph/analyze — 2026-10-28

> Cập nhật sau lần đối chiếu: prompt analyze đã bổ sung trình tự phân tích nhãn hỗ trợ trước khi chọn speaker/addressee, và chỉ yêu cầu đọc các câu thực sự được gửi cùng tóm tắt/memory. Vì vậy điểm 1 bên dưới đã được xử lý bằng cách thu hẹp yêu cầu của prompt, không phải bổ sung transcript toàn scene. Schema/bộ kiểm tra người nhận lời chưa đổi; điểm 2 vẫn còn. Các nhận xét và số dòng bên dưới ghi nhận phiên bản trước thay đổi prompt này; chưa có lần chạy model mới để đánh giá.

## Kết luận

**Luồng chính đã được cài đặt đúng như docs mô tả:** graph chia scene/event và nhóm tên; analyze gửi tóm tắt scene, tóm tắt event trước và ghi nhớ tích lũy; các scene có thể chạy song song, còn event trong một scene chạy tuần tự. Nhân vật giả định theo tên chưa bị gán vào MAGI.

Tuy nhiên, chưa nên kết luận mọi yêu cầu đều đã được bảo đảm: có một điểm không thống nhất giữa prompt và dữ liệu được gửi, cùng một khoảng thiếu kiểm tra kết quả người nhận lời.

Phạm vi: kiểm tra tĩnh code hiện tại, đối chiếu `05_graph.md`, `06_analyze.md` và đọc thêm packet r11 đã lưu. Không chạy test, pipeline, model hoặc truy cập Runpod. Không thay đổi code vận hành hay sửa lại dữ liệu lịch sử.

## Những phần đã khớp

| Nội dung mô tả | Code hiện tại | Kết quả đối chiếu |
|---|---|---|
| Graph đọc toàn bộ transcript normalize, không gửi ảnh | [`graph.py`](../../../src/manga_pipeline/v6/graph.py#L122) đóng toàn bộ `rows` vào `targets`; lời gọi graph không truyền `images`. | Khớp. |
| Chia scene/event, phủ đúng thứ tự và giới hạn event | [`graph.py`](../../../src/manga_pipeline/v6/graph.py#L51) kiểm tra start marker, chia phạm vi và tạo `continuation_of`. [`config.py`](../../../src/manga_pipeline/v6/config.py#L76) không cho batch vượt 8. | Khớp. |
| Sửa dạng tên, tạo alias và nhân vật giả định chưa gán MAGI | [`graph_names.py`](../../../src/manga_pipeline/v6/graph_names.py#L18) tách sửa OCR khỏi nhóm tên; alias được tổng hợp ở dòng 250. [`story_graph.py`](../../../src/manga_pipeline/v6/story_graph.py#L8) tạo `reference:gN`, không có quan sát ảnh và giữ `story_reference_only=True`. | Khớp. |
| Năm tầng truyện → chapter → scene → event → utterance | [`story_graph.py`](../../../src/manga_pipeline/v6/story_graph.py#L34) giữ chapter, story ID, scene/event và các liên kết; utterance được xác định qua `target_ids`. | Khớp cách trình bày khái niệm trong docs. Artifact không lưu riêng một node story có description; docs đã nói rõ điều này. |
| Mỗi event nhận tóm tắt scene, event trước và memory | [`event_analysis.py`](../../../src/manga_pipeline/v6/event_analysis.py#L175) gửi `scene_initial_description`, `scene_current_description`, `previous_event`, `scene_memory`. | Khớp. Event đầu đã có tóm tắt scene, dù `previous_event=None` và memory còn rỗng. |
| Cập nhật tóm tắt và truyền ghi nhớ sang event sau | [`event_analysis.py`](../../../src/manga_pipeline/v6/event_analysis.py#L206) cập nhật mô tả scene/event, `memory` và `previous` sau kết quả thành công. | Khớp. Code truyền bản memory model trả về; không nối nguyên toàn bộ transcript. |
| Scene song song, event trong scene nối tiếp | [`event_analysis.py`](../../../src/manga_pipeline/v6/event_analysis.py#L160) khởi tạo memory riêng và lặp tuần tự các event; dòng 233 tạo `ThreadPoolExecutor` cho các scene. | Khớp. Cấu hình hiện tại: tối đa 8 scene worker, giới hạn thêm bởi số slot LLM. |
| Text trước, ảnh dùng đối chiếu | [`event_analysis.py`](../../../src/manga_pipeline/v6/event_analysis.py#L23) yêu cầu đặt giả thuyết speaker/addressee từ text, rồi kiểm tra nguồn và ảnh. [`vllm.py`](../../../src/manga_pipeline/providers/vllm.py#L74) gửi chữ và ảnh trong cùng một request. | Khớp ở mức chỉ dẫn prompt. Đây không phải hai giai đoạn thực thi được code tách và kiểm soát riêng. |
| Anchor là bằng chứng mạnh, không khóa cứng người nói | Prompt quy định sức nặng 8/10 ở [`event_analysis.py`](../../../src/manga_pipeline/v6/event_analysis.py#L40). [`merge.py`](../../../src/manga_pipeline/v6/merge.py#L7) giữ lựa chọn của model, ghi xung đột với anchor thay vì ghi đè. | Khớp. |
| Chỉ sử dụng đuôi bubble MAGIv2 đã liên kết | [`event_visuals.py`](../../../src/manga_pipeline/v6/event_visuals.py#L20) lấy trực tiếp `text_tail_associations`, không đoán đuôi theo khoảng cách. Prompt quy định cách xử lý khi không có đuôi. | Khớp phần cung cấp dữ liệu; việc model diễn giải đúng vẫn cần kiểm tra kết quả. |
| Nhân vật chỉ có tên không được làm speaker/addressee | [`story_contract.py`](../../../src/manga_pipeline/v6/story_contract.py#L68) từ chối person có `story_reference_only` trong hai vai trò; dòng 90 chỉ cho mention loại `reference` trỏ tới thực thể này. | Khớp. |
| Event lỗi không tạo tóm tắt mới và không tự gọi lại | [`event_analysis.py`](../../../src/manga_pipeline/v6/event_analysis.py#L217) giữ memory trước đó và ghi khoảng thiếu; lời gọi dùng `oom_retry=False`. [`requests.py`](../../../src/manga_pipeline/v6/requests.py#L94) đặt `retries=0`. | Khớp. Khi trạng thái request không chắc chắn, cơ chế an toàn có thể chặn các POST tiếp theo; không bảo đảm mọi event còn lại đều được gọi model trong cùng lượt chạy. |
| 64K, thinking tắt | [`stage_policy.py`](../../../src/manga_pipeline/runtime/stage_policy.py#L8), [`policy.py`](../../../src/manga_pipeline/v6/policy.py#L26) và cấu hình hiệu lực. | Khớp. |

## Hai điểm cần siết lại

### 1. Prompt yêu cầu lời thoại của scene, nhưng packet chưa có toàn bộ transcript scene

**Mức độ: P2 — thiếu ngữ cảnh nguyên văn so với điều prompt yêu cầu.**

[`event_analysis.py:24`](../../../src/manga_pipeline/v6/event_analysis.py#L24) yêu cầu model đọc lời thoại nguồn của scene theo thứ tự. Nhưng dữ liệu gửi đi chỉ có:

- `targets`: các câu của event hiện tại;
- `context`: các câu khác nằm trong vùng ảnh;
- mô tả scene/event trong `chapter_context`, `previous_event` và `scene_memory`.

[`chapter_context`](../../../src/manga_pipeline/v6/event_analysis.py#L145) không chứa transcript đầy đủ của scene. Provider chỉ đóng đúng dữ liệu này vào request, không tự thêm lịch sử hội thoại hoặc phần transcript còn thiếu.

**Ảnh hưởng:** nếu một lời gọi hoặc câu quan trọng nằm ngoài event và vùng ảnh hiện tại, model có thể chỉ thấy bản tóm tắt của nó. Không nên mô tả rằng model luôn được đọc lại nguyên văn cả scene trước khi xác định vai trò. Docs phần input hiện đã nêu đúng các bản tóm tắt/memory được gửi, nhưng câu yêu cầu trong prompt rộng hơn dữ liệu thực có.

Ví dụ minh họa từ packet r11 đã lưu, không phải chạy thử code hiện tại:

| Event của scene:6 | Utterance trong scene | Target hiện tại | Utterance trong scene không có text riêng ở `targets/context` |
|---|---:|---:|---:|
| Event 1 | 18 | 8 | 8 |
| Event 2 | 18 | 8 | 4 |
| Event 3 | 18 | 2 | 16 |

Số còn thiếu ở đây chỉ nói tới text có ID riêng trong hai bảng, không phủ nhận thông tin có thể xuất hiện trên ảnh hoặc được nhắc lại trong summary.

**Hướng xử lý:** nếu mục tiêu là đọc nguyên văn cả scene, bổ sung bảng transcript scene riêng và kiểm tra budget; chỉ `targets` vẫn cần trả quyết định. Nếu chủ đích chỉ dùng text event cùng tóm tắt scene, thu hẹp câu prompt cho đúng chủ đích đó. Không tự thay đổi giữa hai cách trong lần kiểm tra này.

### 2. Chưa kiểm tra quan hệ giữa loại người nhận lời và danh sách person ID

**Mức độ: P2 — khoảng thiếu kiểm tra chéo output.**

[`row_schema`](../../../src/manga_pipeline/v6/story_contract.py#L39) khai báo `addressee_type` và `addressee_person_ids` độc lập. [`validate_row`](../../../src/manga_pipeline/v6/story_contract.py#L76) chỉ kiểm tra danh sách đúng kiểu, không trùng ID và chỉ chứa person hợp lệ.

Vì vậy, một output có `addressee_type="individual"` nhưng `addressee_person_ids=[]` chưa bị chặn bởi các kiểm tra này. Nếu những trường còn lại hợp lệ, dòng có thể được đánh dấu `semantic_resolution="resolved"` tại dòng 106, dù chưa chỉ ra người nhận lời cá nhân nào.

**Ảnh hưởng:** kết quả có thể đúng cấu trúc JSON nhưng tự mâu thuẫn về người nhận lời. Đây là khoảng thiếu bảo vệ được xác định bằng đọc code, không phải khẳng định đã quan sát model tạo trường hợp này trong ví dụ đang dùng.

**Hướng xử lý:** chốt và kiểm tra ràng buộc chéo cho từng `addressee_type`; trước hết, `individual` phải chọn đúng một person khi được coi là đã xác định. Trường hợp chưa xác định người nhận lời cần dùng trạng thái phù hợp thay vì coi là một lựa chọn cá nhân đã giải quyết.

## Giới hạn của kết luận

Kiểm tra này xác nhận cách dựng request, truyền trạng thái, xếp lịch và kiểm tra cấu trúc. Nó không xác nhận model luôn suy luận đúng người nói/người nhận lời, luôn tuân thủ thứ tự text trước–ảnh sau, hoặc luôn giữ đủ và đúng mọi thông tin trong memory. Các yêu cầu đó có trong prompt nhưng vẫn cần đánh giá output thực tế nếu người dùng yêu cầu kiểm chứng model.

Chưa sửa code cho hai điểm trên, chưa chạy test hay model.
