# Thiết kế nhãn speaker và addressee

Đây là thiết kế lịch sử cho essential-v2. Thiết kế hiện hành essential-v3 nằm trong [dialogue_semantics_redesign.md](dialogue_semantics_redesign.md); đặc biệt narration, group và sửa cặp nhãn đã được thay đổi. Xem [hướng dẫn hiện tại](usage.md).

Trạng thái: người dùng đã xác nhận triển khai. Hợp đồng mới dùng `essential-v2`; các run `essential-v1` tiếp tục dùng hợp đồng cũ.

## Phạm vi và mục tiêu

Áp dụng cho các câu essential của MAGI được đưa vào bước classify. Mục tiêu là phân biệt rõ loại người nghe với danh tính của họ, đồng thời loại bỏ tổ hợp mâu thuẫn như đã có ID người nghe cụ thể nhưng type vẫn là `unspecified`.

Phân loại nội dung, người nói, người nghe và vai trò của tên vẫn nằm trong cùng lượt LLM text-only hiện có. Context gồm 5 câu essential và bộ nhớ người tham gia tối đa 10 câu trước. Không thêm bước reasoning hoặc VLM để xác định speaker/addressee. Các quy tắc VLM của bước nối tên hiện có được giữ riêng.

Các câu không essential tiếp tục được giữ cho nhiệm vụ dịch, không đưa vào phân loại hội thoại; speaker/addressee của chúng không được tự động điền bằng các sentinel bên dưới.

## Speaker

| Giá trị | Ý nghĩa |
| --- | --- |
| ID nhân vật | ID ổn định đã có trong bank và thuộc tập ứng viên hợp lệ của câu |
| `others` | Không gán được ID người nói; bao gồm cả người ngoài tập ID và trường hợp chưa xác định được ai nói |
| `narrator` | Lời kể không xác định được người kể là một nhân vật cụ thể |

Không dùng `unknown` trong `speaker_id` của policy mới. `others` là sentinel chung, không phải danh tính của một người cố định. Hai câu có speaker `others` không được tự động coi là cùng người nói. Sentinel không tạo nhân vật mới hoặc được nối tên vào bank.

Nếu xác định được nhân vật kể chuyện, giữ ID nhân vật đó, không thay bằng `narrator`.

## Addressee

`addressee_ids` luôn là danh sách không rỗng đối với câu essential đã classify thành công.

| `addressee_type` | Các dạng `addressee_ids` hợp lệ | Ý nghĩa |
| --- | --- | --- |
| `single` | `[3]` hoặc `["unknown"]` | Hướng tới một người; có thể chưa biết danh tính |
| `group` | `[1, 3]`, `[3, "unknown"]`, hoặc `["unknown"]` | Hướng tới nhiều người trong truyện; chỉ giữ thành viên có căn cứ, `unknown` biểu diễn phần chưa xác định |
| `audience` | `["public_audience"]` | Hướng tới độc giả/người xem, không dùng cho đám đông trong truyện |
| `self` | `[speaker_id]` nếu speaker là ID nhân vật; `["self"]` nếu speaker là `others` | Tự nói hoặc suy nghĩ nội tâm |
| `unknown` | `["unknown"]` | Chưa xác định được câu hướng tới ai hoặc loại đối tượng tiếp nhận |

Quy tắc cho nhóm:

- Một nhóm có đầy đủ danh tính cần ít nhất hai ID nhân vật khác nhau.
- `[3]` riêng lẻ không phải dạng hợp lệ của `group`. Nếu rõ còn người khác nhưng chưa xác định được, dùng `[3, "unknown"]`.
- Chỉ dùng `group` khi có căn cứ hướng tới nhiều người. Nhiều ứng viên trong context hoặc bộ nhớ không tự tạo thành nhóm người nghe.
- Chỉ đưa ID vào nhóm khi có căn cứ ID đó là thành viên được nói tới. Không điền toàn bộ ứng viên hoặc nhân vật xuất hiện trong panel.
- `unknown` trong danh sách nhóm có thể đại diện cho nhiều thành viên chưa xác định; không dùng số lượng phần tử JSON để suy ra tổng số người nghe.

Các sentinel viết thường. ID nhân vật giữ nguyên kiểu dữ liệu và giá trị của bank. Không có `others` trong `addressee_ids`, không dùng `self` hoặc `public_audience` như ID nhân vật thật. Danh sách ID không có phần tử trùng nhau.

## Quan hệ với loại nội dung

LLM phân loại nội dung bằng context, không chỉ dựa vào ngôi kể hoặc thì quá khứ.

| `content_type` | Speaker | Addressee |
| --- | --- | --- |
| `narration` | ID nhân vật kể đã xác định, nếu không thì `narrator` | `audience` + `["public_audience"]` |
| `thought` | ID nhân vật đã xác định, nếu không thì `others` | `self` + `[speaker_id]` hoặc `["self"]` |
| `dialogue` | ID nhân vật đã xác định, nếu không thì `others` | Theo bằng chứng trong hội thoại: `single`, `group`, `audience`, `self`, hoặc `unknown` |

Một nhân vật kể lại sự kiện cho nhân vật khác trong cuộc đối thoại vẫn là `dialogue`; không tự chuyển thành `narration` chỉ vì câu nói kể chuyện. `narration` ở đây là lời trần thuật của truyện, còn `thought` là suy nghĩ nội tâm.

Gán `public_audience` cho narration là quy ước đã chọn của pipeline. Giá trị này không khẳng định đã tìm được người nghe là một nhân vật trong truyện.

## Bằng chứng và kiểm tra nhất quán

- LLM chọn ID từ ứng viên hợp lệ, dựa trên lượt đối đáp, tên/alias đã nối ID, lời gọi trực tiếp và ngữ cảnh được cung cấp.
- Nếu speaker là `3` và chỉ có hai ứng viên `[1, 3]`, không tự động chọn `1` làm người nghe.
- Không bắt model điền một ID nhân vật khi thiếu căn cứ. Dùng sentinel theo đúng loại nội dung và loại người nghe.
- Loại bỏ `unspecified`, `individual`, `not_applicable` và các sentinel cũ khỏi đầu ra của policy mới.
- Kiểm tra schema và quan hệ type/ID bằng code; dùng cơ chế sửa target không hợp lệ hiện có. Không thêm lượt LLM cho các target đã hợp lệ.
- Không chuyển đầu ra `unknown` có ID cụ thể thành kết quả hợp lệ một cách im lặng. Model cần trả về tổ hợp nhất quán.
- Bước nối tên tiếp tục chỉ ghi vào ID nhân vật ổn định theo bằng chứng được phép; sentinel và pending không nhận tên.

## Resume, cache và review

Policy mới được version hóa trong cấu hình run, prompt/schema và cache key. Run đã bắt đầu giữ hợp đồng nhãn của policy đã ghi trong run; resume tiếp tục thừa nhận các kết quả đã commit hợp lệ theo policy đó.

Muốn áp dụng nhãn mới cho một chương đã chạy, tạo run reanalyze mới và tái sử dụng extraction đã hoàn thành. Không sửa trực tiếp các artifact đã commit của run cũ.

Review ngắn gọn hiển thị loại nội dung, speaker, addressee type, addressee IDs, ứng viên tiềm năng và bằng chứng ngắn. Phân biệt rõ ứng viên với người nghe thực sự được chọn. ID nhân vật và metadata/crop/embedding trong bank không được thay đổi bởi việc đổi taxonomy.

## Kiểm tra dự kiến và giới hạn

Kiểm thử các trường hợp: single có ID; single chưa biết ID; nhóm đầy đủ; nhóm chưa đủ thành viên; nhóm không biết ID; đám đông trong truyện; narrator; narration của nhân vật có ID; thought có/không có ID; đối thoại chỉ có hai ứng viên nhưng không đủ căn cứ; đầu ra mâu thuẫn type/ID; resume theo policy cũ và reanalyze theo policy mới.

So sánh trên mẫu hội thoại thực tế, xem riêng speaker, loại người nghe và danh tính người nghe. Không dùng tỷ lệ có ID làm độ chính xác. Chỉ báo độ đúng khi có nhãn tham chiếu được kiểm tra; trường hợp chưa có mẫu nhóm thực tế phải ghi là chưa đánh giá, không suy rộng từ kiểm thử schema.

Quan sát trước thay đổi: run `20261004T094606Z-d9c0b8f5` có 47 câu classify hoàn thành, 5 target lỗi. Cả 47 câu có hai ứng viên; 41 câu có ID người nghe, không câu nào có nhiều ID người nghe. Có 36 câu `unspecified`, trong đó 30 câu đã có ID người nghe. Các số này mô tả đầu ra, không đo độ chính xác.

Giới hạn còn lại: text không luôn cung cấp danh tính của người được gọi; tên/alias hoặc association sai có thể làm lựa chọn sai; `others` không cho phép truy xuất một danh tính ổn định; quy ước narration hướng độc giả phụ thuộc vào việc phân biệt đúng narration với dialogue/thought. Đổi nhãn giải quyết sự nhất quán của hợp đồng dữ liệu, không tự bảo đảm tăng độ đúng của model.

## Các phương án đã loại

- VLM bổ sung cho speaker/addressee trong thay đổi này.
- Tự chọn người còn lại chỉ vì có hai ID ứng viên.
- Dùng `audience` cho đám đông trong truyện.
- Thay ID nhân vật kể chuyện đã biết bằng `narrator`.
- Dùng `unknown` trong speaker, hoặc thêm `none`/`not_applicable` cho addressee của policy mới.
- Tự gán toàn bộ ứng viên thành người nghe của nhóm.

## Xác nhận

Người dùng đã yêu cầu triển khai bản thiết kế. 70 test offline đã qua, gồm hợp đồng nhãn và interrupt/resume của cả hai policy. Smoke Ollama hoàn thành 3/3 câu với nhãn single, reasoning tắt và không có ảnh; chưa đánh giá độ đúng trên toàn chương hoặc hội thoại nhóm. Đánh giá độ đúng trên mẫu có nhãn tham chiếu vẫn cần thiết để kết luận về năng lực suy luận của model. Xem [hướng dẫn hiện hành và kiểm tra](essential_dialogue.md).
