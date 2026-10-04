# Có cần bật reasoning để xác định người nói/người nghe?

**Không bắt buộc bật chế độ thinking. LLM vẫn có thể nhận text và trả trực tiếp người nói/người nghe dưới dạng JSON.** Nếu ý bạn là bỏ hẳn LLM, người nói có thể lấy từ MAGI, còn người nghe chỉ xác định được một phần bằng quy tắc và mapping tên–ID.

## Hai khái niệm đang dễ bị lẫn

| Lựa chọn | Còn suy luận người nói/người nghe không? | Khả thi trong pipeline mới? |
| --- | --- | --- |
| LLM với thinking bật | Có, có thể sinh thêm thinking trace tùy model | Không phải yêu cầu bắt buộc của bài toán |
| LLM với thinking tắt, trả JSON trực tiếp | Có; model vẫn phân tích ngữ cảnh để tạo câu trả lời | Có; phù hợp để thử nghiệm luồng text tích hợp |
| Không gọi LLM cho hội thoại | Chỉ dùng MAGI, quy tắc và mapping có sẵn | Có, nhưng nhiều người nghe và trường hợp speaker sai phải để chưa rõ |

`think: false` yêu cầu không sinh thinking output nếu model hỗ trợ điều khiển đó; không có nghĩa model mất khả năng phân loại hay hiểu text. Việc hỗ trợ on/off phụ thuộc model, theo [tài liệu thinking của Ollama](https://docs.ollama.com/capabilities/thinking).

## Pipeline cũ của bạn đã yêu cầu tắt thinking

- `configs/reasoning.chapter-cpu.json:15` đặt `think: false`.
- `src/reasoning_client.py:172` truyền `think` vào request `/api/chat`.
- Snapshot cấu hình trong lượt chạy chapter đã lưu cũng ghi `think: false`.

Đây là xác nhận về config và request của client, không phải kiểm chứng mới rằng server/model thực thi mọi tùy chọn như mong muốn. Tên module `reasoning_*` trong code không đồng nghĩa thinking đang được bật.

**Vì vậy, chỉ đổi nút thinking sẽ không giải quyết nguyên nhân chậm của lượt chạy cũ.** Theo log đã phân tích, boundary mất khoảng 82% thời gian ở prompt processing. Các lượt gọi lặp lại, ảnh và context dài là các phần cần giảm; output giải thích trong JSON cũng có chi phí sinh token riêng dù thinking đã tắt.

## Nếu bỏ LLM khỏi bước người nói/người nghe

### Người nói

MAGI đã có liên kết text–nhân vật và character ID. Có thể dùng nó làm kết quả mặc định. Những câu như tự giới thiệu tên có thể hỗ trợ sửa/gắn tên bằng quy tắc hoặc bộ phân loại text.

Nhưng khi MAGI gán nhầm speaker, text có thể không đủ bằng chứng để sửa. Lời kể tường thuật là trường hợp phải tách rõ: nhân vật được nhắc đến không mặc nhiên là người phát ngôn của lời kể. ID MAGI gán nhầm có thể là ứng viên để kiểm tra, không tự nó chứng minh mapping tên–ID.

### Người nghe

Có thể giải quyết một số trường hợp rõ ràng:

- “Zoro, lại đây!”: tên gọi trực tiếp, nếu đã có mapping thì có ứng viên người nghe.
- Nhãn nội dung đã được xác định chắc chắn là narration: người nghe của lời thoại có thể là `not_applicable`, đồng thời vẫn lưu người được nhắc đến riêng.
- Các câu không có dấu hiệu rõ: để `unspecified` hoặc unknown phù hợp schema.

Những câu như “Cậu đi đâu?”, “Đưa nó cho tôi”, hay lời nói hướng đến một nhóm cần ngữ cảnh. Người nói kế tiếp không đủ để chứng minh là người nghe. Quy tắc đơn giản không thể bảo đảm bao phủ các trường hợp này.

Vì vậy, **bỏ LLM hoàn toàn khả thi nếu chấp nhận kết quả thiếu và abstain**, nhưng không đáp ứng cùng mức bao phủ với việc suy luận text cho toàn chapter. LLM cũng có thể sai; không ép model gán ID khi text không đủ.

## Đề xuất cho luồng mới

1. MAGI cung cấp speaker ID ban đầu; giữ thứ tự đọc nguồn.
2. Model nhỏ tìm tên và bước text phân loại `mention_type` như đã thống nhất.
3. Nhánh VLM chỉ ghép tên–ID cho trường hợp cần panel, ứng viên giới hạn ở detection của MAGI.
4. Nếu chọn xử lý hội thoại bằng LLM: dùng **một task text tích hợp, thinking tắt, trả JSON ngắn**, thay cho ba lượt riêng 3A/3B/3C. Không yêu cầu đoạn giải thích dài cho từng câu; vẫn giữ ID nguồn và trạng thái chưa rõ để đối chiếu.

Có thể chọn xử lý toàn chapter hoặc chỉ các câu cần sửa/thiếu thông tin. Phương án chọn lọc rẻ hơn nhưng cần quyết định rõ liệu người nghe chưa xác định có được phép giữ lại hay không. Không suy ra tốc độ hay độ chính xác của lượt text mới từ timing VLM cũ; cần một pilot nhỏ so với nhãn đã duyệt trước khi đặt mặc định.

**Q6 vẫn mở:** bạn chưa cần chọn giữa “thinking bật/tắt”, vì đề xuất là tắt thinking. Quyết định thực sự là mức bao phủ: suy luận text cho toàn bộ câu, hay chỉ một tập câu và chấp nhận phần còn lại chưa có người nghe.
