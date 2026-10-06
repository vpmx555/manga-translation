# Sửa lỗi validation và cache của essential-v2

## Nguyên nhân

Run `20261004T115947Z-569a1a27` có 49 câu hoàn thành và 3 target lỗi. P01/T01 và P01/T02 được model trả về group nhưng chỉ có một ID người nghe. P01/T07 được trả về narration với speaker others thay vì narrator. Schema trước đây chỉ giới hạn giá trị của từng trường, nên model vẫn sinh được những tổ hợp mâu thuẫn. Kiểm tra ngữ nghĩa phát hiện lỗi sau inference nhưng phản hồi đó đã được lưu vào model cache; resume có thể lặp lại cùng phản hồi lỗi.

## Thay đổi

- Schema essential-v2 dùng các nhánh object đầy đủ để ràng buộc content type, speaker, addressee type và IDs cùng nhau, với tập ID riêng của từng target.
- Narration chỉ dùng ID người kể hoặc narrator và audience/public_audience. Self giữ đúng ID người nói hoặc self khi chưa có ID. Speaker không xuất hiện trong nhóm người nghe của chính mình.
- Một nhóm chỉ xác định được một thành viên phải có unknown cho phần chưa xác định, hoặc chọn group/unknown khi chưa biết thành viên nào. Không tự thêm một ID nhân vật để đủ số lượng.
- Callback của batch phân biệt coverage và khả năng lưu cache: phản hồi live có một target sai vẫn được trả về để commit các target tốt; chỉ batch hợp lệ hoàn toàn mới vào model cache. Cache cũ sai ngữ nghĩa bị bỏ qua để gọi inference mới, không chặn retry.
- Feedback khi repair gắn lỗi với ID ngắn của đúng target. Schema/prompt của essential-v1 được giữ nguyên; các kết quả đã commit vẫn được dùng lại.

## Kiểm tra

75 test offline đã qua, gồm tổ hợp schema gây lỗi thực tế, giới hạn ID riêng của target, feedback ID, cache sai ngữ nghĩa, batch có cả target tốt/xấu, interrupt và resume.

Đã resume chính run trên đến classify: 52/52 câu hoàn thành, danh sách failures rỗng. So sánh hash trước/sau xác nhận 49 target hoàn thành trước đó không đổi cả nội dung file và kết quả; plans, config, extract/normalize/scan và bank không đổi.

| Target được chạy lại | Content type | Speaker | Addressee type | IDs |
| --- | --- | --- | --- | --- |
| P01/T01 | dialogue | 1 | single | unknown |
| P01/T02 | dialogue | 1 | single | unknown |
| P01/T07 | narration | narrator | audience | public_audience |

Đây là kết quả mới của LLM, không phải nhãn tham chiếu đã xác nhận. Câu P01/T01 “Hello, everyone!” vẫn được model chọn single dù lời gọi gợi ý nhiều người; cần đánh giá lại chất lượng phân loại nhóm. Sửa schema/cache loại bỏ mâu thuẫn cấu trúc, không tự bảo đảm model hiểu đúng người nghe. Không dùng code để âm thầm đổi kết luận này thành group.

Mở [review classify của run](../../outputs/after-school-we-do/chapter-001/20261004T115947Z-569a1a27/04_classify/review.md). Link/export chưa được chạy trong lần sửa lỗi này.
