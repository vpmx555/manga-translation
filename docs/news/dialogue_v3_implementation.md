# Triển khai dialogue v3 và kết quả chạy thử

Ngày kiểm tra: 2026-10-05. Thiết kế đã xác nhận nằm trong [dialogue_semantics_redesign.md](dialogue_semantics_redesign.md); lệnh chạy và cách đọc output nằm trong [usage.md](usage.md).

Cập nhật speaker: run v3 mới dùng [individual-v1](speaker_policy_individual_v1.md), tạm ngừng groups và yêu cầu narrator đi cùng narration. Các kết quả chapter được đo dưới đây thuộc hợp đồng đã lưu của lần chạy cũ, không được chạy lại hay sửa bằng cập nhật này.

## Những thay đổi đã triển khai

Run mới dùng `extract → normalize → scan → analyze → export`, policy `essential-v3`. Analyze thay hai bước classify/link bằng phân loại loại câu, speaker, addressee và vai trò tên trong cùng một request text cho mỗi batch 3 câu. Context tối đa 5 câu/target; bộ nhớ giữ vai trò người tham gia và tên đã xác nhận trong 10 lượt trước. Mọi request thoại dùng `think:false`, không ảnh.

Narration là chức năng kể/trình bày, độc lập với danh tính người nói và kiểu người nghe. Group không bắt buộc liệt kê đủ thành viên: một ID đã biết hoặc unknown vẫn hợp lệ. Các cặp audience/public_audience, unknown/unknown và self/speaker được chuẩn hóa có cảnh báo. Chỉ các cặp mâu thuẫn, ID ngoài ứng viên hoặc dữ liệu thiếu/sai cấu trúc mới cần repair. Tối đa một request repair cho phần lỗi; giữ các trường độc lập và những câu đã hợp lệ. Nếu vẫn lỗi, lưu fallback để chapter tiếp tục.

Nhánh đọc panel chỉ mở khi scan có tên và LLM kết luận introduction. Mọi introduction đều được xét, kể cả câu do nhân vật nói. Không có ID ổn định thì unresolved; một ID và một tên mới thì tự ghép; từ hai ID ổn định khác nhau và có tên mới thì mới cần VLM. Tên đã có mapping bỏ qua VLM. Pending không được gán tên. Direct_address ghép bằng kết luận text: có một người nghe xác định thì tên–ID phải cùng người nghe đó.

Tên từ introduction có ưu tiên cao hơn direct_address. Khi nâng tên hiển thị, tên cũ thành alias và ID/crop/embedding được giữ nguyên. Hai introduction có ưu tiên bằng nhau giữ tên hiện tại, thêm alias và cảnh báo. Tên sửa thủ công được bảo vệ. Không ghép alias chỉ vì giống họ hoặc gần giống chính tả.

Plan, từng lần trả lời model, target text, quyết định panel, thao tác bank và batch được lưu riêng. Resume tái dùng phần đã commit, kể cả fallback và unresolved. Run pipeline v2 tiếp tục sáu bước và policy đã lưu; muốn nâng cấp thì tạo run reanalyze mới, tái dùng extraction đã hoàn tất. Thanh tiến trình và review theo đúng phiên bản của run.

## Kiểm chứng

- 94 test offline đều qua, gồm 19 test mới cho analyze và tên: sửa cặp, group chưa đủ thành viên, introduction trong hội thoại, điều kiện gọi VLM, tên đã biết, memory, fallback, gián đoạn trong repair, gián đoạn sau VLM/ghi bank, ưu tiên tên, bảo vệ tên thủ công và resume.
- `git diff --check` qua.
- Chạy thật với `gemma4:e4b-it-q4_K_M` trên 52 câu essential từ 11 trang; tái dùng extraction cũ. 31 text còn lại lưu để dịch, không đưa vào suy luận thoại.
- Thời gian normalize đến export: 1.730,88 giây, khoảng 28 phút 51 giây. Có 18 batch, 18 request ban đầu và 1 repair; 51 câu từ lượt đầu, 1 câu từ repair, 0 fallback. Tổng thời gian gọi LLM 1.709,10 giây, nên LLM vẫn chiếm phần lớn thời gian. Chưa đo đối chứng cùng chapter trên pipeline cũ để kết luận mức tăng tốc.
- Không có VLM request trong chapter này vì các tên đủ điều kiện được ghép theo nhánh một ID hoặc đã biết. Nhánh nhiều ID được kiểm tra bằng test; chưa đo VLM thật trong run này.
- 52/52 câu có speaker và addressee không rỗng; điều này xác nhận hợp đồng output, không chứng minh tất cả danh tính đúng. Phân bố addressee: 36 single, 10 audience, 6 unknown. Có 5 cảnh báo.
- Resume hoàn tất khoảng 0,2 giây, thêm 0 request. Hash kết quả từng bước và bank thử không đổi; manifest chỉ cập nhật `updated_at`, review được tạo lại. Hash manifest, bank gốc và các artifact completed của run nguồn không đổi.
- Cả sáu file review của run thử có liên kết ảnh/file tồn tại.

## Các case đã yêu cầu

| Case | Kết quả chạy thật |
| --- | --- |
| P01/T01 | narration; speaker 1; audience → [public_audience]. Không còn ép xác định các thành viên group. |
| P02/T23 | narration; Arase Mahoru → introduction → ID 1; audience → [public_audience]. |
| P03/T06 | narration; Madoi Ayame → introduction → ID 3; audience → [public_audience]. |
| P04/T03 | dialogue; speaker 3; single → [1]; Ayase-San → direct_address → ID 1. Câu này hoàn tất sau repair. |

Bốn kiểm tra mục tiêu đều đạt trong lần chạy này. Model vẫn chọn ID nhân vật làm speaker ở các caption giới thiệu P02/T23/P03/T06; ghép tên không phụ thuộc kết luận speaker này. Cần đọc cùng ảnh nếu muốn đánh giá riêng speaker so với narrator.

## Kết quả để kiểm tra

- [Review tổng](../../outputs/diagnostics/analyze-v3-20261005-01/run/review.md): mở đầu tiên, có link từng bước và ảnh.
- [Review analyze](../../outputs/diagnostics/analyze-v3-20261005-01/run/04_analyze/review.md): loại câu, speaker, addressee, một tập ứng viên chung, tên/vai trò/ID, bằng chứng ngắn và cảnh báo.
- [Review export](../../outputs/diagnostics/analyze-v3-20261005-01/run/05_export/review.md): kết quả cuối để đối chiếu.
- [Report kỹ thuật](../../outputs/diagnostics/analyze-v3-20261005-01/report.json): request, think/images, thời gian, bốn case và kiểm tra resume.
- [Bank thử](../../outputs/diagnostics/analyze-v3-20261005-01/bank/metadata.json): ID 1 có `display_name: Arase Mahoru`, alias Ayase-San; ID 3 có `display_name: MAD01 AYAME`, alias Madol-san và Madoi Ayame.

`MAD01 AYAME` là cách OCR đọc một câu introduction xuất hiện trước Madoi Ayame. Theo chính sách giữ tên khi hai bằng chứng cùng ưu tiên, Madoi Ayame được lưu alias kèm cảnh báo, chưa tự thay tên hiển thị. Có thể sửa `display_name` của ID 3 trong bank thử thành Madoi Ayame; lần ghép sau bảo vệ thay đổi thủ công này. Bank gốc không bị thay đổi trong thử nghiệm.

Scan vẫn nhận một số text nhiễu như goage04469, Mort và Clench. Chúng được giữ unresolved, không ghi vào bank. Chưa có cơ chế độc lập xác nhận mọi ứng viên NER thật sự là người; output essential của MAGI và OCR vẫn ảnh hưởng độ chính xác. Run thử này xác nhận các case nêu trên và khả năng resume, chưa thay thế việc kiểm tra toàn chapter bằng ảnh.
