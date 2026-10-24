# Chiến lược ghép tên nhân vật theo ngữ nghĩa và panel

Trạng thái: đã được tích hợp vào policy essential-v3 sau xác nhận của người dùng. [Thiết kế phân loại thoại mới](dialogue_semantics_redesign.md) là bản tổng hợp hiện hành, thay thế những quy tắc cũ mâu thuẫn tại đây về narration, vai trò tên, group, retry và bộ nhớ. [Hướng dẫn chạy/đọc kết quả](usage.md) mô tả luồng analyze.

## Mục tiêu

Khắc phục việc bỏ sót tên trong câu giới thiệu nhân vật và bỏ trống kết luận tên–ID khi đã xác định tên gọi trực tiếp cùng người nghe. Không hardcode theo tên, trang, mẫu trường/lớp/tuổi hoặc chỉ áp dụng cho narration.

## Giữ nguyên

- Sáu bước: extract → normalize → scan → classify → link → export.
- Chỉ essential text tham gia scan tên và suy luận speaker/addressee. Nội dung còn lại giữ cho dịch.
- NER hiện tại, ngưỡng 0.85; context 5 câu và bộ nhớ tối đa 10 lượt trước.
- Classify chỉ dùng text, không reasoning, không ảnh, không phân scene hoặc đổi thứ tự đọc.
- ID bank ổn định; không sửa embedding/crop và không gắn tên cho pending.
- Tên đã nối ID không gọi lại VLM. Cập nhật bank tự động, không hỏi duyệt trong khi chạy.
- Kết quả và target đã hoàn thành là authoritative khi resume.

## Thay đổi classify

Trong cùng lượt LLM hiện có, xác định tên có thực sự chỉ người hay không, quan hệ giữa tên và câu, và tên nào cần đối chiếu nhân vật trong panel.

Mọi câu giới thiệu nhân vật theo ngữ nghĩa đều có thể vào nhánh ghép panel, kể cả dialogue và câu có speaker. Bỏ cổng yêu cầu `content_type == narration` cùng hai vai trò `narration_introduction`/`narration_reference` ở bước link. Không cần phân biệt hai vai trò này để quyết định routing.

Người nói, người nghe và người được giới thiệu là các đối tượng riêng. Phần giới thiệu không có người nói dùng narrator và public_audience; nhân vật thực sự nói hoặc kể chuyện vẫn có thể giữ ID do LLM xác định. Không dùng ID người được giới thiệu để mặc nhiên điền speaker.

Nếu LLM xác nhận direct_address và một người nghe có ID, kết luận tên–ID phải nhất quán. Khi chưa đủ căn cứ, LLM cần điều chỉnh kết luận quan hệ/ID thay vì giữ kết luận chắc chắn nhưng bỏ trống tên–ID. Validator chỉ retry target thiếu hoặc mâu thuẫn theo ngân sách retry hiện tại.

## Cổng ghép tên và panel

1. Không có candidate tên đạt ngưỡng từ scan: không chạy panel tracking cho câu đó. Vẫn phân loại speaker/addressee.
2. Có candidate nhưng LLM bác bỏ tên người hoặc chưa kết luận cần ghép panel: không mở nhánh ghép panel.
3. Tên đã nối ID: dùng bank, không gọi lại VLM và không tự chuyển tên sang ID khác.
4. Gọi trực tiếp có kết luận ID người nghe: nối bằng kết luận text, không cần ảnh.
5. Giới thiệu nhân vật cần ghép panel: dùng các ID ổn định MAGI đã detect trong panel tương ứng.

Đếm ID phân biệt, không đếm số detection:

- Không có ID ổn định: unresolved, không VLM và không ghi tên cho pending.
- Một ID và một tên mới: tự ghép. Không loại ID chỉ vì LLM cho rằng người được giới thiệu khác speaker; quyết định Q5 B ưu tiên ID duy nhất trong panel.
- Một ID nhưng nhiều tên mới chưa được xác nhận là cùng một người: giữ quy tắc hiện tại, unresolved thay vì gắn tất cả tên vào một ID.
- Từ hai ID trở lên và có tên mới cần ghép: VLM chọn trong các detection có ID ổn định. Ưu tiên kiểm tra association gốc của MAGI, không coi association là bằng chứng chắc chắn.

Ghép tên không tự sửa speaker/addressee của câu. Phản hồi VLM hoặc ghép panel thiếu căn cứ vẫn được phép unresolved.

## Tên hiển thị và aliases

Không thêm nhãn full_name. Chọn tên hiển thị theo nguồn bằng chứng:

- Tên từ câu giới thiệu đã ghép ID thành công có ưu tiên cao hơn tên từ lời gọi trực tiếp.
- Chưa có tên: ghi tên đã ghép.
- Tên hiện tại do pipeline gán qua lời gọi trực tiếp, tên mới qua giới thiệu: nâng cấp tên hiển thị, giữ tên cũ làm alias.
- Tên mới từ lời gọi trực tiếp: thêm alias nếu ID đã có tên từ giới thiệu.
- Hai tên khác nhau cùng mức ưu tiên từ giới thiệu: giữ tên hiển thị hiện tại, thêm tên mới làm alias và đánh dấu xung đột trong review (Q6 A).
- Tên người dùng đã sửa thủ công được bảo vệ khỏi đổi tên hiển thị tự động.
- Không suy ra alias từ độ giống chuỗi, số từ, độ dài hoặc hậu tố honorific. Alias phải có bằng chứng liên kết cùng ID.

Ghi nguồn và lịch sử thay đổi tên cùng operation receipt để cập nhật idempotent. Với dữ liệu bank cũ thiếu nguồn, khôi phục từ lịch sử/receipt có thể kiểm chứng; không đoán nguồn chỉ từ cách viết tên. Nếu không khôi phục được nguồn, giữ tên hiển thị hiện tại và lưu tên mới đã ghép làm alias.

## Export và review

Mỗi tên cần thể hiện ID đã ghép hoặc unresolved, nguồn kết luận (text gọi trực tiếp, tự ghép một ID, VLM), và bằng chứng ngắn. Tên được giới thiệu có thể nối ID ngay cả khi speaker là narrator và addressee là public_audience.

Giữ tên nguyên văn trong câu; tên hiển thị và aliases lấy từ bank. Review ghi các xung đột cần kiểm tra, không thêm quy trình duyệt bắt buộc.

## Kiểm chứng khi triển khai

- P02/T23 và P03/T06: tên đã scan được, LLM nhận chức năng giới thiệu và mở nhánh panel, không bị chặn bởi loại câu.
- P04/T03: nếu speaker 3 gọi người nghe 1, Ayase-San phải có kết luận nối ID 1.
- Regression: câu không có tên; NER false positive; giới thiệu trong thoại; người được nhắc ngoài panel; 0/1/nhiều ID; pending; nhiều tên–một ID; tên đã biết; tên sửa thủ công; xung đột tên; resume sau cập nhật bank.
- Chạy lại các target bị ảnh hưởng và các target có input phụ thuộc đã thay đổi trong run mới; dùng lại kết quả không bị ảnh hưởng, rồi link/export. Giữ run cũ nguyên vẹn.

## Giới hạn và lựa chọn đã loại bỏ

- Không hardcode các tên hoặc mẫu trường/lớp; không chỉ mở nhánh cho narration.
- Không dùng nhãn full_name chưa có cơ sở để quyết định tên hiển thị.
- Không thêm lượt LLM kiểm tra mọi câu, không mở VLM cho câu không có tên hoặc panel có 0/1 ID.
- Quy tắc một ID có thể gắn sai khi MAGI bỏ sót người được giới thiệu; đây là tradeoff đã chọn ở Q5 B.
- Tên từ câu giới thiệu là tên hiển thị ưu tiên, không phải bảo đảm tên pháp lý hoặc tên đầy đủ.
- Những kết quả cũ được giữ lại trong run sửa chọn lọc chưa được đánh giá lại toàn bộ bằng logic mới.

## Điều tra bổ sung: P01/T01 và ngữ cảnh người nghe

Run kiểm tra: `20261004T115947Z-569a1a27`.

- P01/T01: "Hello, everyone! We're from the kinoshima Rocketry Research Club!" → single / unknown.
- P01/T02: "This is the first test firing of our new provision engine project." → single / unknown.
- P01/T06: "Carrying our dreams, the hybrid rocket..." → speaker 3, single / ID 1.
- Cả ba target nằm trong cùng request `batch-000000`. Không thể khẳng định lỗi T01 lan qua cache đã lưu tới hai target còn lại.
- Cache cũ có phản hồi T01 group / [1, 3], rồi group / [3]; cả hai sai cấu trúc. Phản hồi cuối là single / unknown: retry sửa cấu trúc nhưng làm mất bằng chứng chào nhiều người.
- Bộ nhớ hiện tại chỉ đưa ID vào pool; không giữ addressee_type, special recipients hoặc quan hệ người nghe đã được xác nhận. Speaker và listener suy ra cùng dùng nhãn nguồn memory:LLM.
- Context T01 chỉ có ba essential turn đầu. Bằng chứng "test vlogs uploaded to the research club's channel" nằm ở essential order 7, ngoài context ±2 hiện tại.

Người dùng xác nhận tiêu chí cho lời chào nhiều người rõ ràng: group hoặc audience đều chấp nhận; single là sai. Phương pháp phải tổng quát, không hardcode câu hoặc tên. Thiết kế bổ sung dưới đây chờ xác nhận cùng bản tổng hợp:

1. LLM xác định phạm vi người nhận từ ngữ nghĩa độc lập với danh tính: một người, tập thể, tự nói hoặc chưa rõ. Cần xét đối tượng thực sự được nói tới; nhắc về một nhóm không chứng minh đang nói với nhóm đó. Có thể biểu diễn ràng buộc nội bộ từ kết luận và bằng chứng hiện có, không bắt buộc thêm nhãn vào output người dùng.
2. Giữ các kết luận ngữ nghĩa không liên quan đến lỗi qua repair. Nếu kết luận có bằng chứng là nói tới tập thể, sửa ID không được hạ thành single; group hoặc audience vẫn có thể phù hợp. Đây là cơ chế repair chung, không phải quy tắc tìm chuỗi "Hello, everyone". Khi bằng chứng ngữ nghĩa tự mâu thuẫn, đánh giá lại thay vì đóng băng kết luận sai.
3. Tách cache ứng viên ID khỏi cache kết luận người nghe. Cache kết luận giữ type, IDs/special recipient, câu nguồn và bằng chứng; kết quả kế thừa cần trỏ về nguồn, không tự trở thành bằng chứng độc lập. Không biết danh tính người nghe không có nghĩa là đang nói với một người.
4. LLM chỉ tiếp tục người nghe cũ khi ngữ nghĩa cho thấy lời nói tiếp tục cùng đối tượng. Đổi speaker không mặc nhiên làm thay đổi người nghe. Không tạo scene và không mở VLM ở nhánh này.
5. Khi có bằng chứng mới phủ định kết luận trước, xác định các target phụ thuộc để đánh giá lại trong run mới. Không sửa committed artifact của run cũ, không mặc nhiên coi chỉ ba câu gắn tên là toàn bộ phạm vi sửa.
6. Giữ context chính 5 câu và memory 10 lượt đã chốt; không mở rộng context hoặc thêm lượt LLM chỉ để quyết định group hay audience trong case đã rõ đối tượng tập thể. Tiếp tục dùng special token khi danh tính chưa rõ, không điền ID theo số lượng ứng viên.
7. Regression bằng nhiều cách diễn đạt của cùng ý định: nói với tập thể, nói với một người trong pool nhiều ID, nhắc về nhóm nhưng không nói với nhóm, đổi đối tượng giữa hai câu, đổi speaker nhưng giữ đối tượng, và retry lỗi ID. Không dùng tên/câu/trang cụ thể làm nhánh xử lý runtime.

Giới hạn: ràng buộc nhất quán và bảo vệ kết luận qua retry không tự chứng minh LLM hiểu đúng ngữ nghĩa ngay từ đầu. Cần đánh giá trên các câu đã gắn nhãn; không coi validation cấu trúc thành công là bảo đảm phân loại đúng.
