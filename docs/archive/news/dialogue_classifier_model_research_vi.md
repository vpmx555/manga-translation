# Research model thay thế `is_this_text_a_dialogue`

Ngày khảo sát: 2026-10-10. Phạm vi: tài liệu, model card và mã nguồn công khai; chưa chạy benchmark hoặc thay đổi pipeline.

## Kết luận

Có model cung cấp chức năng gần tương đương, nhưng chưa xác minh được model độc lập có cùng định nghĩa nhãn, đầu ra và chất lượng để thay trực tiếp head `is_this_text_a_dialogue` của MAGI v2. Với pipeline đã dùng Koharu, nên thử kết hợp tín hiệu layout hiện có với một VLM cho các trường hợp khó trước khi thêm detector khác.

## Chức năng gốc

Tên chính xác trong mã nguồn MAGI v2 là `is_this_text_a_dialogue`, không phải `is_this_a_dialoguage`.

`_get_text_classification` nhận visual object tokens của các vùng text, gọi head phân loại và tùy chọn áp dụng sigmoid. Pipeline so sánh score với `text_classification_threshold` để tạo `is_essential_text`. Đây là phân loại từ đặc trưng thị giác; không phải một hàm phân loại câu OCR chỉ dựa vào văn bản.

Bài báo định nghĩa mục tiêu là phân biệt text cần cho transcript với text không cần như SFX và chữ trên vật thể. Vì vậy cần thống nhất cách xử lý thoại, suy nghĩ, lời kể, tiêu đề và chú thích trước khi đánh giá model khác. Không được đồng nhất `text_inside_bubble` hoặc `not_sfx` với `dialogue`.

Nguồn: [mã nguồn MAGI v2](https://huggingface.co/ragavsachdeva/magiv2/blob/main/modelling_magiv2.py), [Tails Tell Tales, ACCV 2024](https://openaccess.thecvf.com/content/ACCV2024/html/Sachdeva_Tails_Tell_Tales_Chapter-wide_Manga_Transcriptions_with_Character_Names_ACCV_2024_paper.html).

## Các ứng viên đã xác minh

| Model | Đầu ra liên quan | Mức tương đương và giới hạn |
|---|---|---|
| `mayocream/koharu-layout-rfdetr-seg-2xl-1152` | Box/mask: `text`, `onomatopoeia`, `bubble`, `panel` | Phù hợp để phát hiện SFX và quan hệ hình học với bubble. Nhãn `text` chứa cả thoại, caption, title và credits nên không thay trực tiếp classifier ngữ nghĩa. |
| `ogkalu/comic-text-and-bubble-detector` | `bubble`, `text_bubble`, `text_free` | Gần với câu hỏi chữ có nằm trong bubble không. Thoại ngoài bubble và chữ ngoài bubble không phải thoại vẫn nằm chung một lớp. |
| `ebhon/YOLO-manga-bubble-detector` | `bubble`, `narration`, `other`, `text`, `ui` | Có phân biệt narration container; hữu ích về layout. Chưa có bằng chứng từ tài liệu đã đọc về chất lượng tương đương MAGI trên essential/non-essential. |
| `Qwen/Qwen3-VL-4B-Instruct` | Sinh văn bản từ ảnh và prompt | Ứng viên thử phân loại nhiều nhãn theo ngữ cảnh ảnh + OCR. Model card xác nhận visual understanding và OCR; không công bố benchmark riêng cho dialogue/non-dialogue manga. Cần prompt, parse/validate output và đo chất lượng. |

Nguồn cho từng hàng: [Koharu model card](https://huggingface.co/mayocream/koharu-layout-rfdetr-seg-2xl-1152), [ogkalu model card](https://huggingface.co/ogkalu/comic-text-and-bubble-detector), [YOLO repository](https://github.com/ebhon/YOLO-manga-bubble-detector), [Qwen3-VL model card](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct).

Koharu ghi rõ không thực hiện OCR hoặc dự đoán reading order. Model ogkalu được tác giả mô tả là RT-DETR-v2 r50vd fine-tune trên khoảng 11.000 ảnh manga, webtoon, manhua và western comics; con số này là quy mô dữ liệu tác giả công bố, không phải bằng chứng về độ chính xác phân loại thoại.

## MAGI v3 và các model OCR

Đã kiểm tra model card MAGI v3: có detection/association, OCR và character grounding. Chưa xác minh có API/head tương đương `is_this_text_a_dialogue`, nên không kết luận bản v3 thay được chức năng này chỉ vì mới hơn. [MAGI v3](https://huggingface.co/ragavsachdeva/magiv3).

Comic Text Detector được công bố cho text detection, không nên coi là classifier dialogue. Tương tự, khả năng đọc chữ của một model OCR không tự chứng minh khả năng phân loại vai trò của text. [Comic Text Detector](https://github.com/dmMaze/comic-text-detector).

## Đề xuất cho dự án

1. Giữ MAGI v2 làm baseline; giữ nguyên detector và OCR khi so sánh để tránh trộn lỗi detection/OCR với lỗi classification.
2. Thử baseline dùng Koharu hiện có: độ chồng lấp với vùng SFX và mức nằm trong bubble. Các tín hiệu này chỉ là đặc trưng hỗ trợ, không phải luật quyết định tuyệt đối.
3. Thử Qwen3-VL-4B-Instruct cho vùng mơ hồ, cung cấp crop có lề ngữ cảnh hoặc panel, vùng text cần xét và OCR. Các nhãn đề xuất: dialogue, thought, narration, sfx, scene_text, editorial, unknown. Đây là thiết kế thử nghiệm, chưa được chứng minh tốt hơn MAGI.
4. Chỉ thử detector ogkalu riêng nếu cần một baseline độc lập về text trong/ngoài bubble; vì dự án đã có Koharu, lợi ích thêm phải được đo.

## Cách kiểm chứng

Gán nhãn một tập vùng text đại diện từ dữ liệu EN-data: thoại trong/ngoài bubble, suy nghĩ, narration, SFX, biển hiệu, credits và chú thích. Giữ tập đánh giá tách khỏi dữ liệu tinh chỉnh threshold/prompt. Quy định bảng ánh xạ sang nhãn nhị phân essential/non-essential trước khi chấm.

Đo precision/recall/F1 theo lớp, confusion matrix, tỷ lệ bỏ sót thoại, latency và VRAM trên cùng phần cứng. Báo cáo riêng trường hợp unknown và lỗi OCR. Với VLM, không coi confidence tự khai là xác suất đã hiệu chỉnh. Không so trực tiếp detection mAP của Koharu với F1 classification của MAGI.

Chưa đo được model nào tốt hơn hoặc nhanh hơn MAGI v2 trong dự án. Khuyến nghị Koharu + VLM là hướng thử nghiệm dựa trên sự phù hợp chức năng và khả năng tái sử dụng pipeline, không phải kết quả benchmark.
