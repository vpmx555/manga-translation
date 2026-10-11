# Hướng dẫn đọc `05_export/result.json`

Tài liệu này dành cho LLM hoặc chương trình cần đọc bản xuất hội thoại của truyện để dịch, kiểm tra hoặc phân tích. Nó mô tả `kind: "structured_dialogue"`, `schema_version: 1` trong pipeline v4; ví dụ kiểm tra là `outputs/runpod-4090-20261005/bocchi-the-rock/chapter-001/20261005T154833Z-61352b66/05_export/result.json`. Các phiên bản khác có thể thiếu một số trường; luôn kiểm tra trường có tồn tại trước khi sử dụng.

**Bản chất dữ liệu:** Đây là văn bản nguồn tiếng Anh đã được trích xuất, chuẩn hóa và phân tích hội thoại, **chưa phải bản dịch tiếng Việt hoặc trang ảnh hoàn chỉnh**. Trong ví dụ có 291 `utterances`, 123 `translation_only`, 82 `name_links`; số lượng này không cố định.

## Quy tắc đọc quan trọng

1. Cả `utterances[]` **và** `translation_only[]` đều là văn bản cần dịch. `excluded[]` không phải mục tiêu dịch mặc định.
2. ID mục tiêu dịch là `utterances[].id` hoặc `translation_only[].source_text_id`. Không ghép bản dịch với vùng ảnh bằng vị trí phần tử trong mảng.
3. `speaker_id` nguyên là ID nhân vật trong ngân hàng nhân vật của truyện; các chuỗi đặc biệt như `"others"`/`"narrator"` không phải ID nhân vật. `null`, `"unknown"` và danh sách ứng viên không cho phép tự ý đoán danh tính.
4. Một phát ngôn có thể gộp nhiều vùng nguồn. Dùng `source_text_ids[]`/`source_boxes[]` để truy về vùng ảnh; `utterances[].id` là ID của phát ngôn sau chuẩn hóa.
5. `reading_order` chỉ mô tả thứ tự của `utterances` trong bản xuất; nếu cần xếp chung hai loại mục tiêu dịch, ưu tiên `(page, text_index, ID)` như bộ đọc của bước dịch.

## Các trường cấp gốc

| Trường | Kiểu | Ý nghĩa |
| --- | --- | --- |
| `schema_version` | integer | Phiên bản cấu trúc JSON; ví dụ hiện tại là `1`. |
| `kind` | string | Loại tài liệu; `"structured_dialogue"` nghĩa là bản xuất hội thoại có cấu trúc. |
| `document_id` | string | ID ổn định của cặp truyện–chương, làm tiền tố cho ID trang/văn bản. |
| `story_id` | string | ID truyện; phạm vi của ngân hàng nhân vật dùng qua nhiều chương. |
| `chapter_id` | string | ID chương đang xử lý. |
| `reading_direction` | string | Hướng đọc trang, chẳng hạn `"rtl"` (phải sang trái); không phải hướng viết của ngôn ngữ dịch. |
| `raw_fingerprint` | string | Dấu vân tay của tài liệu trích xuất nguồn, dùng nhận diện đầu vào; không phải nội dung để dịch. |
| `raw_document_id` | string | ID tài liệu ở bước trích xuất gốc. |
| `utterances` | array<object> | Lượt thoại/văn bản đã được chuẩn hóa và phân tích người nói–người nghe. |
| `translation_only` | array<object> | Vùng văn bản nguồn vẫn cần dịch nhưng không có phân tích hội thoại đầy đủ. |
| `excluded` | array<object> | Vùng văn bản bị loại khỏi tập mục tiêu dịch theo phân loại/chuẩn hóa. |
| `merge_candidates` | array<object> | Đề xuất có thể ghép các vùng nguồn; **không phải** kết quả ghép đã xác nhận. |
| `name_links` | array<object> | Kết quả liên kết tên được nhắc trong văn bản với nhân vật. |
| `analysis_policy` | string | Tên bộ quy tắc phân tích hội thoại đã dùng, ví dụ `"essential-v3"`; không đồng nghĩa với `schema_version`. |
| `pipeline_version` | integer | Phiên bản luồng xử lý tạo bản xuất, ví dụ `4`. |
| `limitations` | array<string> | Giới hạn suy luận mà hệ thống tự công bố; phải giữ khi diễn giải kết quả. |
| `failures` | array | Lỗi tác vụ được ghi nhận tại bước xuất; mảng rỗng **không** chứng minh mọi suy luận đều đúng. |

## `utterances[]`: một lượt văn bản đã phân tích

### Định danh, thứ tự và tọa độ

| Trường | Kiểu | Ý nghĩa |
| --- | --- | --- |
| `id` | string | ID phát ngôn sau chuẩn hóa và ID mục tiêu dịch; thường có hậu tố `:u`. |
| `page_id` | string | ID trang chứa văn bản. |
| `page` | integer | Số trang, bắt đầu từ 1. |
| `text_index` | integer | Chỉ số văn bản nguồn trong trang, bắt đầu từ 0. |
| `reading_order` | integer | Thứ tự của phát ngôn trong danh sách đã chuẩn hóa, bắt đầu từ 0; không phải thứ tự chung đã trộn với `translation_only`. |
| `bbox` | array<number>[4] | Hộp bao `[x_min, y_min, x_max, y_max]` theo tọa độ pixel ảnh nguồn; không phải tỷ lệ 0–1. |
| `panel_index` | integer \| null | Chỉ số khung truyện trong trang, bắt đầu từ 0; `null` nếu không xác định. |
| `source_text_ids` | array<string> | ID của các vùng văn bản gốc cấu thành phát ngôn; có thể có nhiều phần tử khi gộp. |
| `source_boxes` | array<object> | Vị trí từng vùng văn bản gốc, cùng vai trò truy vết với `source_text_ids`. |

Mỗi phần tử `source_boxes[]` có `source_text_id` (ID vùng nguồn), `page` (số trang), `page_id` (ID trang), `bbox` (tọa độ pixel) và `panel_index` (khung chứa vùng, có thể `null`). `bbox` của phát ngôn mô tả phát ngôn đã chuẩn hóa; dùng `source_boxes` khi cần ánh xạ chính xác từng vùng ảnh gốc.

### Văn bản và phân loại

| Trường | Kiểu | Ý nghĩa |
| --- | --- | --- |
| `text` | string | Văn bản tiếng Anh hiện hành dùng làm đầu vào dịch. |
| `text_original` | string | Văn bản trước chuẩn hóa, để đối chiếu với `text`. |
| `is_essential_text` | boolean | Cờ từ bước trích xuất về tính thiết yếu của vùng văn bản; không thay thế quyết định dịch ở cấp mảng. |
| `content_type` | string | Chức năng nội dung: `dialogue`, `narration`, `thought` hoặc `unknown`. `narration` là kiểu nội dung, không tự xác nhận danh tính người kể. |
| `content_review_status` | string | Trạng thái rà soát ở bước chuẩn hóa, ví dụ `provisional`/`needs_review`; không phải độ tin cậy của bản dịch. |
| `normalization_reasons` | array<string> | Các lý do/cờ dẫn đến quyết định chuẩn hóa hoặc cần rà soát. |

### Người nói, người nghe và nguồn suy luận

| Trường | Kiểu | Ý nghĩa |
| --- | --- | --- |
| `speaker_id` | integer \| string | Người nói cuối cùng theo bước phân tích: ID nhân vật hoặc nhãn đặc biệt như `"others"`, `"narrator"`. |
| `source_speaker_id` | integer \| null | ID người nói ban đầu từ bước liên kết thị giác/trích xuất; có thể khác `speaker_id` sau phân tích. |
| `speaker_name` | string \| null | Tên hiển thị của `speaker_id` từ ngân hàng nhân vật tại thời điểm xuất; `null` nếu không có tên phù hợp. |
| `speaker_display_label` | string | Nhãn hiển thị thô từ bước trích xuất, không phải ID hay tên nhân vật đã xác nhận. |
| `speaker_status` | string | Trạng thái liên kết người nói ở bước trích xuất, ví dụ `resolved`, `pending_identity`, `unlinked`; không phải xác suất. |
| `speaker_cluster_id` | string \| null | Cụm nhân vật cục bộ trong trang; **không** phải ID nhân vật dùng xuyên chương. |
| `character_detection_index` | integer \| null | Chỉ số đối tượng nhân vật được phát hiện trên trang; **không** phải ID ngân hàng nhân vật. |
| `tail_index` | integer \| null | Chỉ số đuôi bóng thoại trên trang khi có liên kết; không phải ID nhân vật. |
| `speaker_affinity` | number \| null | Điểm liên kết người nói nếu nguồn trích xuất cung cấp; `null` nghĩa là không có điểm, không phải 0. |
| `speaker_affinity_source` | string | Nguồn hoặc lý do không có `speaker_affinity`. |
| `association_source` | string | Nguồn tạo liên kết văn bản–nhân vật ở bước trích xuất. |
| `addressee_ids` | array<integer \| string> | ID người nghe đã suy luận, có thể chứa nhãn đặc biệt; mảng rỗng không đồng nghĩa với đã biết người nghe. |
| `addressee_type` | string | Phạm vi người nghe: `single`, `group`, `audience`, `self` hoặc `unknown`. Phạm vi có thể được biết dù ID cụ thể chưa biết. |
| `voice_type` | string | Kiểu tiếng nói suy ra từ `speaker_id`, ví dụ `character`, `narrator`, `unknown`, `groups`; không phải nhãn vai trò xã hội. |
| `classification_source` | string | Nguồn kết quả phân tích, ví dụ `model`, `repaired`, `fallback`; `fallback` là đường dự phòng, không phải danh tính. |
| `evidence` | string | Lý giải/bằng chứng ngắn do bước phân tích ghi lại; không nên coi là chứng cứ độc lập đã kiểm định. |
| `speaker_candidates` | array<object> | Các nhân vật ứng viên cho người nói, **không** phải kết luận. |
| `listener_candidates` | array<object> | Các nhân vật ứng viên cho người nghe, **không** phải kết luận. |
| `identity_candidates` | array<object> | Các nhân vật ứng viên để giải tham chiếu danh tính, **không** phải kết luận. |
| `warnings` | array<object> | Cảnh báo/điều chỉnh giá trị khi phân tích hoặc hậu xử lý. |
| `mentions` | array<object> | Các tên/tham chiếu đến nhân vật được phát hiện trong phát ngôn. |

Mỗi phần tử của ba mảng `*_candidates` có `id` (ID ứng viên), `name` (tên hoặc `null`), `aliases` (bí danh) và `sources` (nguồn đưa ứng viên vào danh sách). Không có trường điểm xác suất mặc định cho các ứng viên. Mỗi phần tử `warnings[]` có `code` (loại cảnh báo), `before` (giá trị trước chỉnh sửa) và `after` (giá trị sau chỉnh sửa).

**Giá trị đặc biệt:** `speaker_id: "others"` là giọng/chủ thể chưa gắn với một nhân vật bền vững; `"narrator"` là giọng kể. Trong `addressee_ids`, `"unknown"` là chưa rõ danh tính, `"public_audience"` là hướng đến công chúng, `"self"` là tự nói với bản thân. Không biến các nhãn này thành ID nhân vật. Với `addressee_type: "group"`, danh sách ID có thể chỉ bao gồm thành viên đã biết, không chắc là toàn bộ nhóm.

### `utterances[].mentions[]`

| Trường | Kiểu thường gặp | Ý nghĩa |
| --- | --- | --- |
| `id` | string | ID duy nhất của lần nhắc đến tên; dùng nối với `name_links[].candidate_id`. |
| `utterance_id` | string | ID phát ngôn chứa lần nhắc tên. |
| `name` | string | Chuỗi tên được phát hiện. |
| `start`, `end` | integer | Vị trí bắt đầu/kết thúc đoạn tên trong văn bản liên quan; kiểm tra lại với `text` trước khi cắt chuỗi. |
| `key` | string | Khóa tên đã chuẩn hóa để đối chiếu. |
| `source` | string | Phương pháp/nguồn phát hiện tên. |
| `confidence` | number \| null | Độ tin cậy phát hiện tên nếu có; **không** phải độ tin cậy rằng tên khớp đúng nhân vật. |
| `mention_type` | string | Vai trò lần nhắc: `introduction`, `direct_address`, `reference` hoặc `unknown`. |
| `known_id` | integer \| null | ID nhân vật đã biết trước bước liên kết tên, nếu có. |
| `model_name_target_id` | integer \| null | ID đích do mô hình đề xuất, chưa mặc nhiên là liên kết cuối cùng. |
| `name_target_id` | integer \| null | ID đích sau bước xử lý liên kết tên, nếu được gán. |
| `referenced_id` | integer \| null | ID nhân vật được tham chiếu mà bản xuất có thể xác định từ ngân hàng tên; có thể chưa xác định. |
| `state` | string | Trạng thái nội bộ của lần nhắc ở bước phân tích tên. |
| `binding_status` | string | Trạng thái liên kết tên–nhân vật. |
| `binding_source` | string \| null | Nguồn của liên kết tên–nhân vật, nếu có. |
| `skipped` | boolean | Lần nhắc có bị bỏ qua trong bước liên kết hay không. |
| `unresolved` | boolean | Lần nhắc còn chưa giải được danh tính hay không. |

Các trường ID trong `mentions[]` có thể cùng tồn tại nhưng thể hiện **các giai đoạn khác nhau**; không tự coi `model_name_target_id` là liên kết xác nhận. Xem `name_links[]` để biết trạng thái liên kết tổng hợp.

## `translation_only[]`: văn bản vẫn cần dịch

Mỗi mục có `source_text_id` (ID mục tiêu dịch và ID vùng nguồn), `text` (văn bản tiếng Anh), `page`, `page_id`, `text_index`, `reading_order`, `bbox`, `panel_index`. `speaker_id` thường là `null`, `addressee_ids` thường là `[]`; không được suy diễn đây là lời thoại của một nhân vật cụ thể. Một số mục có thêm `content_review_status` và `normalization_reasons`. Vẫn giữ những mục này khi dựng bản dịch trang.

## `excluded[]`, `merge_candidates[]`, `name_links[]`

- `excluded[]`: các mục không được chọn làm mục tiêu dịch. Tùy trường hợp có `source_text_id`, `text_original`, `page`, `bbox`, `panel_index`, `content_type`, `reasons`, `review_status`. Không coi mảng này là văn bản cần dịch chỉ vì có chữ nguồn.
- `merge_candidates[]`: gợi ý ghép vùng văn bản, thường có `source_text_ids` (các vùng được đề xuất), `reason` và `confirmed`. Chỉ xử lý như một phép ghép thật nếu dữ liệu đã xác nhận; các phép ghép thực tế đã phản ánh trong `utterances[].source_text_ids`.
- `name_links[]`: mỗi mục có `candidate_id` (nối đến `mentions[].id`), `name`, `status`, `reason`. Mục đã liên kết có thể thêm `character_id`, `source`, `previous_name`, `current_name`, `alias`, `promoted`, `name_conflict`. `status: "unresolved"` không xác nhận nhân vật; `linked`/`known` có thể mang `character_id` là ID ngân hàng nhân vật.

## Cách nối dữ liệu cho tác vụ dịch

```text
targets = utterances.map(u => (id=u.id, bucket="utterances", text=u.text))
        + translation_only.map(t => (id=t.source_text_id, bucket="translation_only", text=t.text))
sort targets by (page, text_index, id)

Với utterance: id phát ngôn -> source_text_ids[] / source_boxes[] -> vùng ảnh nguồn.
Với translation_only: source_text_id -> chính vùng ảnh nguồn của mục đó.
Với tên nhân vật: utterances[].mentions[].id -> name_links[].candidate_id.
```

Khi dịch Anh–Việt, chỉ dùng `speaker_id`, `addressee_ids`, `addressee_type` và tên nhân vật như **bằng chứng có điều kiện** để chọn xưng hô. Nếu ID chưa rõ, giữ cách diễn đạt trung tính hoặc đánh dấu cần rà soát; không tự tạo quan hệ tuổi tác, giới tính, vai vế hay tên nhân vật. `result.json` không chứa ảnh trang, mặt nạ xóa chữ, bản dịch tiếng Việt hay tọa độ dàn chữ cuối cùng.
