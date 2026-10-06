# Thiết kế lại phân loại thoại và ghép tên

Trạng thái: người dùng đã xác nhận; luồng analyze được triển khai với policy `essential-v3`/pipeline v3. Tài liệu này cập nhật [chiến lược ghép tên](name_binding_strategy.md). [Probe kết hợp](joint_semantic_probe_20261004.md) là khảo sát trước triển khai; xem [hướng dẫn hiện tại](usage.md) để chạy và đọc kết quả.

## Các quyết định đã chốt

- Một lượt LLM cho mỗi batch trả cả loại câu, người nói, người nghe và vai trò tên. Không thêm lượt phân loại riêng. Context tối đa 5 câu/target, bộ nhớ tối đa 10 lượt trước, `think=false`, không ảnh trong suy luận thoại.
- `narration` biểu diễn chức năng chính là kể hoặc trình bày, có thể do nhân vật đang nói thực hiện; không phải mọi câu cung cấp thông tin đều là narration. Không suy ra speaker hay addressee chỉ từ nhãn này. Caption giới thiệu không có người nói thường là `narrator`; ID mà MAGI gắn vào caption có thể là người được giới thiệu.
- `mention_type` gồm `introduction`, `direct_address`, `reference`, `unknown`. Tự giới thiệu thuộc `introduction`; không thêm nhãn tên đầy đủ.
- `group` không yêu cầu liệt kê đủ thành viên. Có thể lưu một ID đã biết, nhiều ID có căn cứ hoặc `[unknown]`. Số phần tử không phải số người trong nhóm; không thêm ID hay `unknown` để đạt số lượng tối thiểu.
- Sau tối đa một lượt sửa kết quả, phần còn mâu thuẫn để chưa xác định, ghi cảnh báo ngắn và tiếp tục chương. Không bỏ các kết luận độc lập đã hợp lệ.
- Sau mỗi batch, ghép các tên đủ điều kiện rồi đưa kết quả đã xác nhận vào bộ nhớ cho batch sau. Không đợi hết chương mới sử dụng tên.
- Gộp `classify` và `link` thành bước `analyze`; checkpoint riêng các phần trong batch để resume mà không suy luận hoặc ghép tên lại phần đã hoàn tất.
- Chuẩn hóa các cặp có giá trị cố định bằng code, giữ nhãn ngữ nghĩa LLM đã chọn và ghi cảnh báo về giá trị đã sửa. Không gọi thêm LLM chỉ để sửa các cặp này.

## Luồng chạy và tổ chức analyze

Luồng chính của run mới: `extract → normalize → scan → analyze → export`. Mỗi bước vẫn có kết quả và review riêng; có thể chạy đến một bước, chạy một bước hoặc resume. `analyze` thay thế hai bước `classify`/`link` trên run mới.

Trong mỗi batch, mặc định giữ ba target như cấu hình hiện tại:

1. Tạo input từ text essential, context tối đa năm câu, snapshot tên/alias và bộ nhớ đã xác nhận từ batch trước. MAGI cung cấp gợi ý ID; không đưa ảnh vào LLM thoại.
2. Một lượt LLM trả ngữ nghĩa và ID của các target cùng lúc.
3. Kiểm tra coverage/enum/ID, chuẩn hóa cặp cố định, rồi sửa tối đa một lượt nếu vẫn còn lỗi cần chọn danh tính hoặc mâu thuẫn ngữ nghĩa. Lưu kết quả text đã chấp nhận hoặc fallback theo từng target.
4. Xét liên kết tên đủ điều kiện: tên đã biết, tên gọi trực tiếp và introduction. Chỉ mở panel khi có tên được scan và vai trò introduction; không gọi VLM nếu panel có không quá một stable ID.
5. Ghi liên kết/bank an toàn khi resume, cập nhật snapshot bộ nhớ và đánh dấu batch hoàn tất. Batch kế tiếp mới sử dụng tên vừa xác nhận.

Thanh tiến trình toàn bộ có năm bước, hiển thị lần lượt. Bên trong analyze hiển thị số batch đã hoàn tất và phần đang chạy: text, sửa cặp, ghép tên/panel hoặc lưu kết quả. Resume khởi tạo tiến độ từ checkpoint. Panel bị bỏ qua theo cổng không được hiển thị là một lượt VLM đã chạy.

## So với luồng hiện tại

| Hiện tại | Thiết kế mới |
| --- | --- |
| Schema liệt kê tổ hợp loại câu, loại người nghe và ID | Schema chỉ kiểm soát trường, enum, ID ứng viên và đủ target/tên; kiểm tra cặp ở code |
| Narration bắt buộc audience/public_audience | Loại câu, người nói và đối tượng tiếp nhận được xác định độc lập |
| Group phải đủ hai ID hoặc thêm unknown | Group có thể chỉ lưu thành viên đã biết; không ép đủ nhóm |
| Tên giới thiệu chỉ được ghép khi qua cổng narration cũ | Mọi `introduction` được scan xác nhận đều được xét ghép, bất kể loại câu |
| Direct address có thể còn target null dù listener single đã biết | Cặp tên gọi trực tiếp–người nghe phải thống nhất trước khi tạo liên kết |
| Retry có thể thay lại toàn bộ target | Chỉ sửa trường/cặp có lỗi, gửi kèm kết quả trước và giữ các kết luận không liên quan |
| Bộ nhớ chủ yếu gộp ID ứng viên | Lưu vai trò của ID, kiểu tiếp nhận và tên đã ghép; không coi mọi ứng viên là người nghe |
| Classify hết chương rồi link | Phân loại → kiểm tra cặp → ghép tên → checkpoint sau từng batch |

Không phân scene, không thay thứ tự đọc bằng reasoning, không mở panel tracking khi scan không có tên. Nội dung không essential vẫn giữ cho dịch nhưng không tham gia suy luận thoại.

## Schema và các cặp cần bảo vệ

Output mỗi target giữ `content_type`, `speaker_id`, `addressee_type`, `addressee_ids`, các mention (`candidate_id`, `mention_type`, `name_target_id`) và bằng chứng ngắn. Không cần model xuất toàn bộ tên/metadata ngân hàng hay liệt kê nhóm đầy đủ.

Schema có tối đa một nhánh cho mỗi target, không nhân nhánh theo tổ hợp nhãn. Nó giới hạn ID và danh sách tên đúng target. Code kiểm tra đủ target/tên, không trùng, đúng enum và ID thuộc tập được cung cấp.

| Cặp | Quy tắc tối thiểu |
| --- | --- |
| Speaker–ID | Run mới `speaker_policy: individual-v1`: stable ID trong tập ứng viên, `others` hoặc `narrator`; others không phải danh tính cố định giữa các câu. groups tạm ngừng |
| Narrator–loại câu | Narrator yêu cầu narration; narration vẫn cho phép stable ID hoặc others. Mâu thuẫn mở riêng speaker cho repair, giữ loại câu và các trường độc lập hợp lệ |
| Audience–IDs | `[public_audience]` |
| Unknown–IDs | `[unknown]` |
| Single–IDs | Một stable ID hoặc `[unknown]`; không chọn chính speaker đã biết |
| Group–IDs | Một hoặc nhiều stable ID có bằng chứng, có thể có `unknown`; hoặc `[unknown]`. Không kiểm tra số thành viên tối thiểu |
| Self–IDs | `[speaker_id]` khi biết stable ID, nếu chưa biết dùng `[self]` |
| Thought–người nghe | Suy nghĩ nội tâm hướng tới self; không suy ra người nói từ người được nhắc đến |
| Direct address–single đã biết | `name_target_id` phải bằng ID người nghe; null là cặp chưa hoàn tất |
| Direct address–group | Khi xác định được ID người được gọi, ID đó phải nằm trong các thành viên đã xác định |
| Tên đã có liên kết duy nhất–ID mới | Không gán sang ID khác; mâu thuẫn phải được xử lý trước khi ghi bank |
| Introduction–speaker | Người được giới thiệu không mặc nhiên là người nói; không ép introduction có một loại câu hay một kiểu người nghe |

Run v3 cũ thiếu `speaker_policy` vẫn dùng hợp đồng cũ với `groups`, kể cả khi đang dở. Token này chỉ dùng cho speaker; không vào addressee_ids, name_target_id hoặc bank. Export/review tiếp tục đọc output cũ. Run v2 giữ nhánh legacy. [Policy individual-v1](speaker_policy_individual_v1.md) được ghi trong config của manifest, snapshot, request của batch plan và request journal; muốn chuyển hợp đồng thì tạo run mới bằng `reanalyze`.

Không dùng bộ kiểm tra cấu trúc để đoán lại ngữ nghĩa của câu. LLM quyết định phạm vi tiếp nhận dựa trên nội dung và context; một câu rõ hướng tới nhiều người không được đổi thành single chỉ vì chưa biết họ là ai. Đây là chỉ dẫn chung, không phải regex cho một câu chào hoặc mẫu giới thiệu cụ thể.

## Retry và fallback

Trước retry, chuẩn hóa các cặp có đáp án cố định từ `addressee_type`:

| Nhãn LLM đã chọn | Giá trị chuẩn hóa |
| --- | --- |
| `audience` | `[public_audience]` |
| `unknown` | `[unknown]` |
| `self` | `[speaker_id]` khi speaker là stable ID; nếu chưa xác định stable ID thì `[self]` |

Chỉ sửa khi giá trị khác quy ước; lưu giá trị trước/sau và mã cảnh báo ngắn. Không tự đổi type để giữ ID model đã trả, không chuyển ID bị loại thành liên kết tên. `single` và `group` không được điền thêm danh tính bằng bước chuẩn hóa này. Nếu speaker cần sửa thì tính lại cặp self sau khi có kết luận speaker cuối.

Lượt sửa nhận kết quả trước, cặp bị lỗi và dữ kiện liên quan; các trường độc lập đã hợp lệ được giữ lại. Thiếu ID nhóm không phải lỗi và không gây retry. Một lượt sửa bao gồm cả lỗi JSON/coverage và lỗi cặp; tránh vòng retry trong provider nhân thêm vòng retry ở caller. Giới hạn một lượt sửa cho mỗi batch; các target đã hợp lệ không được đưa vào để suy luận lại. Khi `content_type=thought` nhưng `addressee_type` khác `self`, hoặc tên gọi trực tiếp không thống nhất với người nghe, cần giải quyết theo cặp thay vì tự đoán danh tính.

Nếu vẫn lỗi, giữ phần hợp lệ và vô hiệu hóa phần mâu thuẫn:

- Speaker không xác định dùng `others`; không thêm `unknown` vào enum speaker đã thống nhất.
- ID người nghe chưa xác định dùng sentinel tương ứng với loại tiếp nhận còn hợp lệ. Có thể giữ `group` + `[unknown]`, hoặc `single` + `[unknown]`.
- Liên kết tên chưa xác định để null/unresolved; không tạo alias hay cập nhật bank từ cặp bị lỗi.
- Nếu chính nhãn ngữ nghĩa cũng chưa xác định, dùng `unknown` và cảnh báo. Không dùng việc sửa ID để tự đổi narration thành dialogue hoặc group thành single.
- Không đưa ID hoặc tên chưa được xác nhận vào bộ nhớ như sự thật đã hoàn tất.

## Ghép tên trong mỗi batch

1. Scan phát hiện tên trước, giữ nguyên confidence và ngưỡng 0.85 hiện tại. Không có tên thì dừng nhánh tên tại đây.
2. LLM đánh vai trò từng ứng viên trong cùng lượt phân loại thoại. Ứng viên không phải tên người hoặc không rõ vai trò nhận `unknown`.
3. Tên đã có ID duy nhất dùng liên kết sẵn có; không VLM, không tự chuyển sang ID khác.
4. Tên mới được gọi trực tiếp chỉ nối với người nghe stable ID mà LLM đã kết luận nhất quán. Không suy ra ID chỉ vì tập ứng viên còn một người khác.
5. Tên mới thuộc `introduction` được xét panel chứa câu giới thiệu, dù câu là narration hay dialogue và dù MAGI có gán speaker.
6. Panel có 0 stable ID: unresolved, không VLM. Có đúng 1 stable ID và 1 tên mới: nối tự động. Nhiều tên mới nhưng chỉ 1 ID: chưa đủ căn cứ, unresolved. Có từ 2 stable ID và tên mới: VLM chọn trong các ID đó.
7. Chỉ dùng stable ID đã có trong bank. Pending không được gắn tên. Crop hay số lượng crop không tạo ngoại lệ.
8. Lưu liên kết và cập nhật snapshot bộ nhớ cho batch kế tiếp. Không đổi stable ID, embedding hoặc crop vì đổi tên.

Tên hiển thị ưu tiên nguồn giới thiệu đã ghép thành công hơn nguồn gọi trực tiếp. Tên hiển thị cũ trở thành alias khi được nâng ưu tiên. Hai tên cùng mức ưu tiên: giữ tên hiện tại, bổ sung alias có cùng-ID bằng chứng và ghi xung đột. Tên sửa thủ công được bảo vệ. Không coi hai tên là cùng người chỉ vì giống chữ hoặc có chung họ.

## Bộ nhớ và chống lan lỗi

Bộ nhớ tối đa 10 lượt lưu gọn: vị trí câu, speaker, addressee type, người nghe đã xác định, mention đã ghép và bằng chứng ngắn. Tách dữ kiện nguồn MAGI, kết luận LLM và liên kết tên đã xác nhận. Các special token không được biến thành danh tính liên tục.

Phạm vi group/audience gần đây là context gợi ý, không phải nhãn bắt buộc cho câu sau. LLM phải đọc lại ngữ nghĩa từng target; câu gọi riêng, đổi người nói hoặc đổi đối tượng có thể đổi kiểu tiếp nhận. Không dùng các ứng viên ID có mặt để tự suy ra single.

Các target trong cùng batch vẫn được suy luận cùng lượt. Tên vừa ghép chỉ trở thành dữ kiện xác nhận cho batch kế tiếp; không thêm một lượt LLM để chạy lại toàn batch hiện tại. Export bổ sung tên cuối từ bank mà không suy luận lại speaker/listener.

## Lưu kết quả và resume

Checkpoint mỗi batch gồm input/snapshot bộ nhớ, kết quả text đã chấp nhận, trạng thái từng liên kết tên/panel, cập nhật bank có khóa định danh thao tác và snapshot bộ nhớ sau batch. Mỗi target giữ cảnh báo và trạng thái đã hoàn tất, kể cả hoàn tất bằng fallback. Mỗi liên kết có trạng thái resolved, unresolved hoặc chưa hoàn tất; unresolved đã được kết luận không phải một thao tác còn chờ gọi model.

| Vị trí bị ngắt | Cách resume |
| --- | --- |
| Chưa có kết quả text hoàn tất | Chỉ xử lý target chưa hoàn tất trong batch; giữ các target đã chấp nhận |
| Text xong, chưa ghép hết tên | Dùng kết quả text đã lưu; xử lý liên kết chưa hoàn tất |
| Đã có quyết định VLM, chưa ghi bank | Dùng quyết định đã lưu để hoàn tất ghi bank; không gọi VLM lại |
| Bank đã ghi, checkpoint batch chưa xong | Kiểm tra khóa thao tác và hoàn tất snapshot/batch; không tạo alias hoặc nâng tên lặp lại |
| Batch đã hoàn tất | Bỏ qua cả batch và khôi phục bộ nhớ đã lưu |

Nếu bị ngắt trước khi nhận/lưu được kết quả model thì có thể phải gọi lại phần đó; không thể thừa nhận một kết quả chưa tồn tại trên đĩa là đã hoàn tất.

Ghi kết quả liên kết trước, áp dụng vào bank theo thao tác có thể lặp lại an toàn, rồi đánh dấu đã hoàn tất. Nếu dừng giữa các lần ghi, resume đọc journal để hoàn tất thao tác, không thêm alias trùng hoặc nâng tên lần nữa. Bộ nhớ chỉ được đưa sang batch sau khi các phần liên quan đã có checkpoint.

Mỗi run lưu phiên bản luồng và policy lúc tạo. Run cũ, kể cả đang dở, resume theo luồng sáu bước và policy đã ghi; kết quả hoàn tất vẫn được thừa nhận, không tự đổi enum hoặc gộp bước giữa chừng. Run cũ thiếu trường phiên bản được nhận diện theo manifest/artifact cũ. Adapter cho resume cũ được tách khỏi luồng analyze mới.

Áp dụng thiết kế mới bằng run mới hoặc reanalyze mới, tái sử dụng MAGI extraction đã hoàn tất như hiện tại và giữ run cũ để so sánh. CLI run mới dùng `analyze` cho lựa chọn bước; `classify`/`link` vẫn phục vụ resume run cũ. Export lấy kết quả analyze trên run mới hoặc kết quả classify/link trên run cũ và xuất cùng các trường thiết yếu.

Việc sửa tên thủ công trong bank vẫn được bảo vệ khi áp dụng liên kết. Đọc lại metadata liên quan trước khi ghi, giữ ổn định ID/crop/embedding và không coi special token là nhân vật thật.

## Review và nghiệm thu

Review mỗi câu chỉ hiển thị text, content type, speaker, addressee type/IDs, ứng viên ID và nguồn ngắn, tên/vai trò/ID ghép, bằng chứng ngắn và cảnh báo. Review liên kết thêm nguồn text/panel, tên trước/sau và lý do unresolved. Dữ liệu kỹ thuật chi tiết nằm trong JSON/cache.

Nghiệm thu cần bao phủ ngữ nghĩa gọi nhóm không biết thành viên, group chỉ một ID đã biết, caption giới thiệu, giới thiệu bằng lời nói, tự giới thiệu, gọi trực tiếp, nhắc lại tên, tên không phải người; các panel 0/1/nhiều stable ID và tên đã có ID. Không dùng riêng các tên hoặc câu trong chương mẫu làm luật xử lý.

Kiểm tra cả resume dừng sau text, sau VLM, sau ghi bank và sau checkpoint; fallback không làm mất các target hợp lệ. Probe nhỏ chỉ đánh giá tính khả thi, không chứng minh độ đúng toàn chương. Bộ kiểm tra cặp không phát hiện được mọi nhãn sai nhưng nhất quán về cấu trúc.

## Phạm vi triển khai sau xác nhận

Tách orchestration analyze, dựng input/schema, kiểm tra/sửa cặp, ghép tên và checkpoint thành các phần có trách nhiệm rõ ràng. Provider chỉ thực hiện request/cache; không sở hữu ngữ nghĩa group/narration hoặc tự sửa ID. Cập nhật CLI, review, export và tiến trình cho luồng năm bước; giữ adapter đọc/resume run cũ.

Kiểm thử cặp và resume với provider giả lập trước, sau đó chạy chẩn đoán thật trên run mới để so sánh các case đã nêu. Không ghi đè run đã hoàn tất hoặc tuyên bố cải thiện độ đúng chỉ từ kiểm thử cấu trúc. Các tùy chọn thiết bị hiện có vẫn được giữ, gồm CUDA cho thành phần hỗ trợ CUDA.
