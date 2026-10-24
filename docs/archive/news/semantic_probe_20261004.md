# Thử nghiệm phân loại ngữ nghĩa trước khi thiết kế lại

Model: `gemma4:e4b-it-q4_K_M`. Input text-only, `think=false`, temperature 0, không ảnh. Chín câu, ba request, 207.65 giây trước cleanup; không batch lỗi.

Thử nghiệm chỉ trả content_type, addressee_type và mention_type. Không chọn speaker/listener ID hoặc liệt kê thành viên group; không dùng grammar 36 nhánh của production. Các nhãn tên thử nghiệm là introduction, direct_address, reference, unknown. Không có quy tắc runtime theo tên hoặc câu cụ thể.

Đây là kiểm tra khả năng với nhiệm vụ đơn giản hơn, không phải triển khai pipeline mới hoặc so sánh nhân quả giữa hai schema. Các kết quả production cũ là baseline khác nhiệm vụ; không thể quy toàn bộ khác biệt cho việc giảm chặn.

| Case | Content type trả về | Addressee type | Vai trò tên |
| --- | --- | --- | --- |
| P01/T01: Hello, everyone… | dialogue | group | Không có candidate tên |
| P02/T23: Arase Mahoru trong phần giới thiệu | narration | audience | introduction |
| P03/T06: Madoi Ayame trong phần giới thiệu | narration | audience | introduction |
| P02/T02: MAD01 AYAME, OF THE ENGINE SECTION | narration | audience | introduction |
| P04/T03: Ah, Ayase-San! | dialogue | single | direct_address |
| Đối chứng: This is our new club member, Hana Mori. | narration | group | introduction |
| Đối chứng: My name is Hana Mori. | dialogue | single | introduction |
| Đối chứng: I haven't heard from Hana Mori since last week. | dialogue | unknown | reference |
| Đối chứng: Hana Mori, can you hear me? | dialogue | single | direct_address |

**Điểm tích cực:** 8/8 câu có tên nhận vai trò phù hợp với mục đích thử nghiệm; lời chào tập thể được nhận là group mà không cần tìm thành viên. Các câu caption thật được nhận narration/introduction.

**Điểm chưa giải quyết:** câu spoken introduction được đặt trong context “Who's joining us today?” → “This is our new club member…” → “Nice to meet you both.” Model vẫn chọn narration/group, dù prompt định nghĩa spoken introductions có thể là dialogue. Nếu giữ narration chỉ là lời kể/caption, đây là một lỗi phân loại; nếu mở rộng narration thành lời trình bày, phải chốt định nghĩa mới trước khi đánh giá.

Có 14 tiêu chí tối thiểu đã đặt trước được đáp ứng, nhưng không đặt gold content_type cho hai câu spoken/self introduction và phần giới thiệu role ngắn vì ranh giới đang được khảo sát. Không được diễn giải thành accuracy 100%. Chưa kiểm tra khả năng chọn ID khi đưa nhiệm vụ ID trở lại, NER false positives, tên ambiguous hoặc độ tổng quát trên toàn chương.

Script: [probe_dialogue_semantics.py](../../scripts/diagnostics/probe_dialogue_semantics.py).

Kết quả, prompt và baseline từng case: [report.json](../../outputs/diagnostics/semantics-probe-20261004-01/report.json).

Các committed artifact của production run được kiểm tra hash sau thử nghiệm và giữ nguyên. Script không gọi cập nhật bank. Thiết kế runtime vẫn chưa được triển khai.
