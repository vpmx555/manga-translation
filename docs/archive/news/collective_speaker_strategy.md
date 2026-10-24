# Speaker groups theo vai trò hội thoại

Trạng thái: hoãn đề xuất mở rộng groups sau phản hồi P03/T01 và P03/T07. Run v3 mới dùng [individual-v1](speaker_policy_individual_v1.md), tạm ngừng groups; chỉ triển khai bảo vệ narrator yêu cầu narration. Phân biệt we/us, cải thiện bằng chứng và các mục mở rộng groups bên dưới chưa được triển khai. Kết quả completed giữ nguyên; P03/T01 và P03/T07 chưa được cam kết thành groups.

## Vấn đề đã đối chiếu

| Câu | Kết quả hiện tại | Kết quả người dùng xác định |
| --- | --- | --- |
| P03/T01: Tell us! How do you feel after becoming famous overnight? | dialogue, speaker 1 theo gợi ý MAGI, single [unknown] | Speaker groups: phía tập thể đang hỏi |
| P03/T07: So, how does it make you feel? | dialogue, speaker narrator, single [3] | Speaker groups: câu hỏi tiếp nối của cùng phía tập thể |

Giữa hai câu có lời đáp của ID 3 và một caption giới thiệu Madoi Ayame. Câu P03/T07 không nằm cùng cửa sổ text 5 câu với P03/T01, nhưng P03/T01 nằm trong memory 10 lượt trước. Vì vậy không cần tăng context toàn chương hoặc chạy scene segmentation để truy ra liên hệ này.

MAGI là nguồn gợi ý ID, không xác nhận vai trò người hỏi. Output hiện tại có cặp dialogue/narrator ở P03/T07; bằng chứng chỉ chép lại câu hỏi, không mô tả quan hệ hỏi–đáp. Nhãn groups vừa bổ sung chưa xuất hiện trong lần chạy chapter cũ và prompt đang giới hạn nó vào lời nói đồng thời.

## Định nghĩa groups cần dùng

Groups là phía tập thể đang phát ngôn trong một cuộc trao đổi, khi không tách được một cá nhân làm người nói. Bao gồm lời đồng thanh, nhiều lời hỏi từ một đám đông, hoặc lời tiếp nối đại diện cho phía tập thể đã được context xác lập.

Một người được xác định rõ đang nói thay mặt nhóm vẫn dùng ID của người đó. Các từ we/us hoặc việc nói với nhiều người không tự đủ để chọn groups. Khi chưa xác lập được cá nhân hay phía tập thể, dùng others. Narrator dành cho giọng kể ngoài cuộc trao đổi, cần bằng chứng tích cực về chức năng kể; không dùng thay cho speaker chưa rõ.

Groups không là danh tính cố định cho mọi đám đông trong chapter. Chỉ nối vai trò trong context/memory gần khi cùng đối tượng được nói tới và cùng mạch hỏi–đáp. Không gán tên, không tạo ID bank, không đưa groups vào addressee_ids hoặc name_target_id.

## Cách thực hiện với luồng hiện tại

1. Sửa hướng dẫn của request analyze hiện có để LLM xác định các phía hội thoại theo ngữ nghĩa: ai hỏi, ai trả lời, câu nào là tiếp nối, câu nào là caption xen giữa. Đây là cơ sở chọn speaker, không phải một lượt gọi model riêng.
2. Với một phía tập thể đã xác lập, câu hỏi tiếp nối có thể giữ speaker groups dù không còn từ we/us. Dựa vào quan hệ với câu hỏi trước và lời đáp của phía còn lại; không chỉ sao chép speaker của câu ngay trước.
3. Đọc memory 10 lượt trước như lịch sử vai trò. Memory đã có content_type, speaker_id, addressee_type, addressee_ids và evidence. Yêu cầu evidence là tín hiệu quan hệ ngắn, chẳng hạn collective questioners resume after the answer, thay vì chỉ lặp nguyên văn câu hỏi. Không cần thêm schema lớn hoặc đưa toàn bộ transcript vào memory.
4. Caption giới thiệu không có người phát ngôn không chiếm lượt hỏi–đáp; nhóm đang hỏi vẫn có thể quay lại sau caption. Lời giới thiệu thực sự do một người nói vẫn được xem là lượt thoại bình thường. Không bỏ qua toàn bộ narration hoặc introduction.
5. Chọn ID cá nhân chỉ khi có bằng chứng phù hợp với vai trò đó. Một ID xuất hiện trong panel/context/MAGI chưa đủ để biến phía đang hỏi thành nhân vật đó. LLM vẫn được dùng các gợi ý MAGI, tên và lượt thoại đã xác nhận; không hardcode trang hoặc câu.
6. Khi chuyển sang cuộc trao đổi khác, có đối tượng/nhóm khác hoặc mất bằng chứng nối tiếp, đánh giá lại groups. Nếu chưa xác định, giữ others; không tự tiếp tục nhóm gần nhất hoặc chuyển sang narrator.
7. Bảo vệ một cặp đơn giản: speaker narrator cần content_type narration. Nếu model trả dialogue/narrator, đưa riêng speaker vào repair, giữ nguyên dialogue và người nghe đã hợp lệ. Narration vẫn có thể do nhân vật hoặc groups nói; không ép mọi narration thành narrator. Nếu repair không xác định được người nói thì fallback speaker others, giữ các kết luận độc lập. Quy tắc này không phát hiện được mọi trường hợp model cùng đánh sai cả loại câu và speaker, nên vẫn cần kiểm chứng ngữ nghĩa bằng context.

Các cặp speaker/listener vẫn độc lập. Nếu nhóm hỏi một nhân vật đã xác định là ID 3, speaker groups đi với single [3]. Khi ID 3 đáp lại nhóm chưa định danh thành viên, speaker 3 có thể đi với group [unknown]. Không cần liệt kê đủ nhóm hoặc dùng groups làm token người nghe.

Tất cả dùng một request text cho mỗi batch như hiện tại, think:false, không ảnh, context 5 câu và memory 10 lượt. Cơ chế tối đa một repair và resume theo target/batch được giữ nguyên. Không thêm quy tắc từ khóa để ép nhãn cho Tell us hoặc So.

## Kiểm chứng cần thực hiện khi triển khai

- Hai case đã báo và các biến thể thay tên/ID/cách diễn đạt.
- Phỏng vấn, lớp học, họp nhóm và đám đông phản ứng; có câu tiếp nối không nhắc nhóm.
- Caption giới thiệu xen giữa; lời giới thiệu có người nói; câu trả lời ngắn; chuyển lượt hỏi giữa hai cá nhân đã có ID.
- Một người dùng we/us hoặc nói thay mặt nhóm vẫn giữ ID cá nhân nếu được xác định.
- Câu chưa rõ speaker giữ others; câu hỏi trong một cuộc trao đổi không được chọn narrator chỉ vì thiếu MAGI ID.
- Hai nhóm khác nhau trong memory không bị coi là một danh tính liên tục.
- Thử có MAGI hint sai, thiếu hint, thứ tự ID thay đổi và context thiếu bằng chứng; kiểm tra cả kết quả lẫn evidence.

Text-only không luôn phân biệt được một người đại diện và nhiều người phát ngôn. Định nghĩa groups theo phía hội thoại giúp biểu diễn phần biết được từ ngữ nghĩa; khi context không đủ vẫn phải giữ others. Các test model thật cần báo riêng kết quả, không coi việc schema chấp nhận groups là bằng chứng model đã suy luận đúng.
