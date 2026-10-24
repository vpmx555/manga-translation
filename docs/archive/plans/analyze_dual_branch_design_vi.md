# Thiết kế analyze: nhánh caption và dialogue

Ngày: 2026-10-14.

Trạng thái: tổng hợp quyết định từ phiên grill-me; chờ người dùng xác nhận bản thiết kế. Chưa triển khai, chưa chạy kiểm thử hoặc inference.

## 1. Mục tiêu

Tách các essential text thành hai nhánh có nhiệm vụ riêng:

- Caption: xác định người nói, ưu tiên người nói gốc và sự liên tục của đoạn kể/suy nghĩ; người nghe được gán bằng quy tắc đơn giản.
- Dialogue: xác định cả người nói và người nghe bằng neo MAGIv2, quan hệ giữa các lượt thoại và người liên quan trong cùng scene.

Tách nhánh theo bubble, không theo phân loại ngữ nghĩa. Dialogue vẫn có thể chứa suy nghĩ; caption vẫn có thể chứa lời kể hoặc suy nghĩ. `analysis_branch`, `content_type` và `speech_style` là các khái niệm riêng.

## 2. Hiện trạng đã kiểm tra

- `essential-v3` đưa text có `is_essential_text=true` vào analyze.
- Luồng legacy trong `analysis/` xử lý batch 3 câu, ngữ cảnh ±2 câu, bộ nhớ 10 essential turn trước; không phải 10 panel.
- V6 đang dùng `v6/semantic.py`: batch có thể điều chỉnh kích thước, cùng trang nguồn; context có 3 kết quả trước và 2 text sau; ứng viên bổ sung người nói từ 10 kết quả trước. V6 có hợp đồng person/cluster và ảnh context riêng, cần tích hợp thiết kế mới vào đường chạy này, không chỉ sửa `analysis/` legacy.
- Cả các đường analyze được kiểm tra đều chưa tạo hoặc sử dụng `scene_id` cho cơ chế giới hạn ứng viên đang đề xuất.
- Extraction lưu text–character, text–tail và `tail_index`. `speaker_affinity=None`; không xuất điểm người nói trong tài liệu raw hiện tại.
- `bubble_geometry.bubble_style` có thể là `caption_hint`; `style_hints` dùng nhãn `caption`.
- Các liên kết text–character và text–tail được MAGIv2 dự đoán bằng các head riêng. Tail hỗ trợ chất lượng nhận diện người nói qua quá trình học; không có phép cộng điểm người nói trực tiếp chỉ vì có tail.

Các vị trí tích hợp chính của V6: `v6/semantic.py`, `v6/semantic_contract.py`, `v6/semantic_recovery.py`, `v6/persons.py`, đường tạo utterance V6 và export. Các module legacy `analysis/context.py`, `analysis/stage.py`, `analysis/contract.py`, `analysis/pairs.py`, `analysis/binding.py`, `normalization/dialogue.py`, `extraction/document.py` cần được xét ở các điểm dùng chung hoặc tương thích. ID ổn định phải dùng đúng namespace của bank/registry tương ứng, không ép person ID V6 thành chỉ số character của raw.

## 3. Luồng xử lý đề xuất

1. Giữ thứ tự gốc của toàn bộ essential text; cấp chỉ số `essential_index` trước khi tách nhánh.
2. Định tuyến: `caption_hint` vào caption; các essential text còn lại vào dialogue. Phân loại narration/thought không được chuyển nhánh.
3. Tạo neo dialogue từ dữ liệu MAGIv2 và danh tính ổn định.
4. Xác định scene/mạch hội thoại từ sự liên quan ngữ nghĩa của các dialogue theo thứ tự đọc.
5. Gắn ranh giới scene chung vào essential text theo vị trí; lập danh sách ứng viên theo scene.
6. Analyze mỗi nhánh bằng nội dung riêng, chia đoạn khi cần và chạy hai chiều trong cùng scene.
7. Hợp nhất kết quả theo ID và thứ tự essential gốc; kiểm tra bất biến và xuất dữ liệu giải thích.

Không thay đổi điều kiện giữ essential text trong thiết kế này. Khi thiếu hình học bubble, text đi vào nhánh dialogue và giữ lý do định tuyến để truy vết.

## 4. Scene và các mạch bên trong scene

Scene trong thiết kế này là nhóm hội thoại có liên hệ ngữ nghĩa, không khẳng định đó là một cảnh vật lý được xác định từ ảnh.

- Dùng các dialogue liên tiếp có liên quan để lập mạch; không gom các đoạn ở xa chỉ vì nhắc lại cùng chủ đề.
- Không bổ sung tác vụ xác định scene bằng ảnh. Đổi panel/trang không tự tạo ranh giới.
- “Uh”, “ah” và các câu đệm tương tự không dùng để đánh giá liên tục về chủ đề. Vẫn giữ chúng để analyze người nói/người nghe, làm neo nếu đủ điều kiện, và dịch.
- Khi gặp câu không liên quan, phân biệt lời chen ngang với đổi mạch thực sự. Đây là quyết định có lý do được lưu, không phải mặc định mọi câu lệch chủ đề đều ngắt scene.
- Lời chen ngang được phân tích trong mạch phụ; không ghi đè trạng thái mạch chính. Sau lời chen ngang có thể nối lại mạch chính nếu bằng chứng cho phép.
- Đổi mạch thực sự tạo ranh giới scene mới; suy luận từ scene trước không truyền qua ranh giới.
- Giữ riêng `scene_id` và mã mạch nội bộ: cùng scene không mặc nhiên nghĩa là mọi câu cùng người nói hoặc người nghe.

Caption nhận ranh giới chung theo vị trí, không đọc nội dung dialogue. Đoạn chỉ có caption dùng khoảng cách essential text để nối mạch; đánh dấu nguồn nhóm là khoảng cách, không diễn giải thành kết luận chuyển cảnh về ngữ nghĩa.

Chi tiết quy ước cần thể hiện rõ khi triển khai: caption trước dialogue đầu tiên dùng nhóm đầu đoạn; caption sau dialogue cuối dùng nhóm cuối đoạn. Caption nằm giữa hai scene theo ranh giới đã lập, không chọn scene bằng nội dung dialogue trong nhánh caption. Các quy ước vị trí này là giả định cần người dùng xác nhận cùng bản thiết kế.

## 5. Nhánh dialogue

### 5.1. Neo khóa người nói

Một text là neo khi thỏa cả ba điều kiện:

1. Có liên kết text–tail của MAGIv2.
2. Có liên kết text–character của MAGIv2 cho chính text đó.
3. Nhân vật được liên kết có ID ổn định hợp lệ trong character bank.

Không yêu cầu thêm ngưỡng affinity, độ chênh điểm, hoặc bộ kiểm tra hướng đuôi riêng. Đây là quy tắc vận hành do người dùng chọn, không phải tuyên bố mọi liên kết MAGIv2 đều đúng tuyệt đối.

Người nói của neo bị khóa. Lượt tiến, lượt ngược, binding và hợp nhất kết quả đều phải giữ nguyên người nói của neo. Neo chỉ khóa người nói, không khóa người nghe hoặc loại nội dung.

Text có tail và character nhưng chưa có ID ổn định không được dùng làm neo khóa. Vẫn giữ các liên kết gốc để analyze như bằng chứng thông thường.

Nếu một utterance chứa nhiều text nguồn, phải tra ngược đúng từng `source_text_id` trong raw. Không lấy tail của text khác để tạo neo. Khi các nguồn có người nói neo mâu thuẫn, giữ xung đột để xử lý cấu trúc utterance, không cho LLM chọn lại người nói của các neo.

### 5.2. Suy luận người nói

- Dùng neo ở cả trước và sau trong cùng scene/mạch.
- Kết hợp người nói MAGIv2 đề xuất với quan hệ trả lời, mở rộng, tiếp diễn và nội dung của câu đang xét.
- Tiếp diễn/mở rộng là bằng chứng có thể cùng người nói, không phải quy tắc bắt buộc.
- Trả lời là bằng chứng về quan hệ giữa người hỏi và người trả lời, không phải quy tắc máy móc luân phiên hai nhân vật.
- Lưu câu làm nguồn suy luận, neo gốc, quan hệ và lý do chọn.
- Kết quả suy ra không trở thành neo khóa mới. Có thể cung cấp ngữ cảnh, nhưng không được nâng thành bằng chứng độc lập tương đương MAGIv2.
- Khi quan hệ ngữ nghĩa mâu thuẫn với neo khóa, xét lại quan hệ hoặc kết quả của câu không khóa; giữ neo.

### 5.3. Suy luận người nghe

- Kết hợp phương pháp hiện tại của pipeline với danh sách ứng viên trong cùng scene.
- Dùng gọi trực tiếp, quan hệ hỏi–đáp, phản hồi, tiếp diễn và người tham gia để xác định người nghe.
- Cho phép một người, nhóm người, độc giả/khán giả hoặc bản thân theo loại lượt lời.
- Dialogue là suy nghĩ phải có `addressee_type=self`; không chuyển sang nhánh caption chỉ vì là suy nghĩ.
- Không mặc nhiên giữ người nghe của câu trước, hoặc gán toàn bộ người trong scene làm người nghe.
- Chọn kết quả có khả năng cao nhất khi bằng chứng yếu, đồng thời lưu lý do và ứng viên thay thế. Không yêu cầu người dùng duyệt từng câu để pipeline tiếp tục.
- Không bịa ID: nếu không có danh tính hợp lệ để gán, dùng nhãn đặc biệt hiện có của hợp đồng người nghe. Việc lưu độ chắc chắn không được tự biến thành cổng phê duyệt.

Giới hạn 7 essential text không áp dụng cho các liên kết dialogue. Ranh giới scene, mạch và bằng chứng ngữ nghĩa giới hạn suy luận.

## 6. Nhánh caption

### 6.1. Người nói và sự liên tục

- Ưu tiên người nói gốc MAGIv2; có thể sửa khi bằng chứng trong mạch caption ủng hộ người nói khác.
- Analyze hai chiều trong mạch. Chuỗi A → B → A có thể sửa B thành A nếu nội dung tiếp diễn và B là liên kết nhiễu.
- Không tự quyết bằng đa số ID hoặc sao chép người nói caption trước.
- Phân biệt người nói của từng caption với người kể đang giữ mạch.
- Caption mô tả ngoài lề thường nhận `narrator`, nhưng không tự ghi đè người kể của mạch chính.
- Đổi người kể thực sự phải được ghi nhận, không tự coi mọi khác biệt là nhiễu.
- Người được giới thiệu/nhắc tới trong caption không mặc nhiên là người đang kể.

### 6.2. Khoảng cách tối đa

- Mặc định 7 essential text, cho phép cấu hình.
- Tính trên thứ tự essential gốc, gồm caption, dialogue và câu đệm; không tính text bị loại khỏi essential.
- Tính cả hai đầu mút: vị trí 1 → 7 hợp lệ; 1 → 8 vượt giới hạn.
- Áp dụng cho từng khoảng nối, không cho tổng chiều dài mạch. Caption ở vị trí 1, 6, 11 có thể cùng mạch nếu từng khoảng hợp lệ và cùng scene.
- Scene đổi hoặc khoảng nối vượt giới hạn thì xác định người nói lại cho mạch mới; vẫn giữ người nói gốc làm đề xuất.
- Caption ngoài lề không trở thành mắt xích kéo dài mạch người kể chính. Khoảng cách nối lại tính từ caption gần nhất thuộc mạch chính; đây là cách diễn giải quy tắc không cập nhật mạch chính, cần xác nhận cùng bản thiết kế.

### 6.3. Người nghe mặc định

- Caption được nhận diện là suy nghĩ: `self`.
- Caption còn lại: `audience`.
- Người nghe này được đánh dấu là gán theo quy tắc mặc định, không phải kết quả suy luận người nghe.
- Giữ `content_type` và `speech_style` riêng; không dùng người nghe mặc định để quyết định ngược người nói hoặc loại nội dung.

## 7. Danh sách người liên quan theo scene

Ứng viên gồm:

- Nhân vật có bằng chứng xuất hiện trong scene.
- Người nói MAGIv2 đề xuất trong nhánh đang xét.
- Người nói/người nghe đã suy ra trong scene của nhánh đang xét.
- Người được gọi trực tiếp trong scene của nhánh đang xét.

Người chỉ được nhắc tới không tự trở thành người tham gia. Giữ thông tin tên/alias để hiểu nội dung và binding, tách khỏi danh sách ứng viên người nói/người nghe.

Danh sách ứng viên không giới hạn theo 10 turn/panel gần nhất. Bằng chứng xuất hiện phải gắn với scene, không lấy toàn bộ nhân vật trên một trang khi trang chứa nhiều scene.

Hai nhánh không truyền nội dung hoặc kết quả suy luận hội thoại cho nhau. Dùng chung các dữ kiện cấu trúc và danh tính: ID nguồn, essential index, ranh giới scene, phát hiện nhân vật và character bank. Bộ nhớ suy luận và trạng thái mạch được tách theo nhánh/scene. Alias và danh tính đã biết trong bank vẫn tồn tại xuyên scene.

Nhãn người nói cuối: ID ổn định, `narrator` hoặc `others`. `others` không đại diện cho một người cố định xuyên các lượt lời.

## 8. Scene dài, hai lượt và checkpoint

- Chia nội dung từng nhánh thành các đoạn có ngữ cảnh chồng lấn theo giới hạn của provider.
- Việc chia đoạn không tạo ranh giới scene mới.
- Lập danh sách neo và ứng viên scene trước khi suy luận để sử dụng cả bằng chứng phía sau.
- Lượt tiến tạo kết quả tạm; lượt ngược xem xét lại câu không khóa bằng bằng chứng phía sau.
- Chỉ hợp nhất thành kết quả cuối khi đã hoàn tất các lượt cần thiết của scene. Không dùng trạng thái tạm để ghi đè kết quả cuối hoặc cập nhật bank thiếu nhất quán.
- Checkpoint cần lưu scene, nhánh, lượt, đoạn, hash đầu vào/kế hoạch và trạng thái mạch để resume không thay đổi cách định tuyến hoặc neo.
- Chỉ xử lý và commit mỗi target một lần ở kết quả cuối; vùng chồng lấn là ngữ cảnh, không tạo bản ghi trùng.
- Giữ cơ chế integrity/checkpoint hiện có. Thay đổi policy/schema phải được version hóa; không đọc checkpoint cũ như thể tương thích với thiết kế mới.

## 9. Hợp đồng dữ liệu và truy vết

Giữ nguyên dữ liệu nguồn và liên kết source box. Kết quả cần thể hiện các thông tin sau; tên trường chính xác là chi tiết triển khai:

- Nhánh, essential index, scene/mạch và lý do định tuyến/ranh giới.
- Người nói gốc và người nói cuối.
- Neo có khóa hay không, text/tail/character nguồn và ID ổn định.
- Quan hệ suy luận, các câu/neo được sử dụng và lý do chọn.
- Người nghe, phạm vi người nghe và nguồn `inferred` hay `default_rule`.
- Caption ngoài lề, người kể mạch chính và lý do sửa người nói.
- Danh sách ứng viên cùng nguồn bằng chứng; ứng viên thay thế khi lựa chọn yếu.
- Vị trí lượt tiến/lượt ngược và trạng thái cuối của target.

Schema phải tách ứng viên người tham gia khỏi tên được nhắc tới, và không coi kết quả gán mặc định của caption là bằng chứng người nghe cho dialogue.

Export giữ mỗi utterance đúng một lần và đúng thứ tự nguồn. Các review Markdown hiện có vẫn được tạo như đầu ra; không bổ sung cổng duyệt thủ công giữa các lượt.

## 10. Các phương án đã loại

- Dùng ngữ nghĩa để chuyển `caption_hint` sang dialogue.
- Cho hai nhánh đọc chéo nội dung để suy luận.
- Thêm ngưỡng affinity/margin hoặc kiểm tra hướng đuôi như điều kiện neo bắt buộc.
- Khóa neo khi nhân vật chưa có ID ổn định.
- Chỉ suy luận về phía sau neo.
- Lấy mọi người được nhắc tên làm ứng viên người tham gia.
- Giới hạn người tham gia bằng 10 câu/panel gần nhất.
- Áp ngưỡng 7 câu cho dialogue hoặc toàn bộ chiều dài mạch caption.
- Loại câu đệm khỏi analyze/dịch hoặc loại bỏ mọi câu lệch chủ đề.
- Buộc đọc toàn bộ scene dài trong một request.

## 11. Tình huống nghiệm thu cho bước triển khai

Đây là tiêu chí cần kiểm chứng sau khi được yêu cầu triển khai/kiểm thử, không phải báo cáo đã chạy:

1. Mọi `caption_hint` vào caption, kể cả khi nội dung có dạng lời nói; suy nghĩ ở nhánh dialogue vẫn giữ nhánh và người nghe self.
2. Text có tail–character và ID ổn định khóa người nói; text thiếu ID ổn định không khóa. Lượt ngược không thay đổi neo.
3. Neo phía sau giúp giải câu phía trước trong cùng mạch; không truyền neo qua scene khác.
4. Câu đệm có thể làm neo, vẫn được dịch và vẫn tính trong khoảng cách essential, nhưng không tự cắt scene vì thiếu ngữ nghĩa.
5. Lời chen ngang không ghi đè mạch chính; đổi mạch thực sự cắt liên kết suy luận.
6. Caption A → narrator ngoài lề → A giữ người kể chính A khi cùng scene và còn trong khoảng nối cho phép.
7. Caption A → B → A có thể sửa B khi có bằng chứng tiếp diễn; không sửa chỉ vì A chiếm đa số.
8. Kiểm tra chính xác khoảng nối 1→7, 1→8 và chuỗi 1,6,11.
9. Người tham gia cách xa hơn 10 turn vẫn là ứng viên trong cùng scene; người ở scene trước không tự lọt sang scene mới.
10. Tên được nhắc tới không tự thành người nghe; gọi trực tiếp và quan hệ trả lời có thể bổ sung người nghe.
11. Không suy diễn mọi người trong scene là người nghe của mọi câu.
12. Scene dài và resume giữ neo, ranh giới, thứ tự, mạch và không tạo target trùng.
13. Hai nhánh không nhận text hoặc bộ nhớ suy luận của nhau; vẫn sử dụng chung metadata scene và danh tính đã biết.
14. Caption-only và caption sát ranh giới scene tuân theo quy ước vị trí đã được xác nhận.

## 12. Rủi ro và công việc tiếp theo

- Neo MAGIv2 sai sẽ bị khóa theo chủ ý thiết kế. Giữ đầy đủ nguồn để truy vết; không để LLM âm thầm sửa neo.
- Ranh giới scene sai có thể làm mất liên kết hoặc trộn người tham gia. Cần lưu quyết định chen ngang/đổi mạch và các câu làm bằng chứng.
- Ngưỡng 7 và định tuyến trực tiếp theo bubble là quy tắc người dùng chọn, không phải ngưỡng đã được thực nghiệm hiệu chỉnh.
- Hai lượt làm tăng chi phí so với một lượt; chia đoạn phải bảo toàn bộ nhớ scene và neo gốc.
- Caption-only không có ranh giới dialogue làm bằng chứng; nhóm theo khoảng cách phải được ghi đúng bản chất.

Sau khi bản thiết kế được xác nhận: đặc tả schema/prompt và policy mới, tích hợp nguồn neo qua raw→utterance→analyze, thay bộ nhớ cửa sổ bằng bộ nhớ scene, triển khai checkpoint hai lượt và cập nhật export/review. Model Runpod dùng lựa chọn mới của người dùng ở mục 13; hạ tầng chưa được triển khai.

Phiên này chỉ chuẩn bị thiết kế. Không chạy pipeline, model pilot, kiểm thử, hoặc truy cập Runpod. Chỉ thực hiện các bước đó khi người dùng yêu cầu trong phạm vi được phép.

Nguồn đối chiếu: [mã nguồn MAGIv2 đúng revision](https://huggingface.co/ragavsachdeva/magiv2/blob/fbc890fec52977142e8ee00bfe26e9458b65517c/modelling_magiv2.py), [bài báo Tails Tell Tales](https://arxiv.org/html/2408.00298v1).

## 13. Bổ sung từ người dùng sau bản tổng hợp

### Model trên Runpod

- Người dùng sửa lựa chọn cuối thành Qwen3.8-27B quantized, thay Gemma E4B.
- Chuẩn bị config V6 với `qwen3.8:27b-q4_K_M` qua Ollama; giữ `num_ctx=16384` và cơ chế `think=false` hiện có.
- Tag đã được đối chiếu với [danh sách chính thức Ollama](https://ollama.com/library/qwen3.8/tags) và manifest registry. Có model cùng projector; chưa tải weights, chưa inference.
- Bootstrap local V6 phải truyền config frozen vào prefetch và doctor để cả hai bước dùng cùng model với batch. Đã chỉnh truyền config tại source, chưa chạy bootstrap.
- Plan/config đã freeze trước thay đổi phải được chuẩn bị lại theo cơ chế hiện có; không sửa manifest/checkpoint cũ để giả lập đã chạy model mới.

### Giảm chọn shout

- Người dùng yêu cầu giảm false positive shout vì kiểu hét xuất hiện ít.
- Ưu tiên `normal` cho lời nói thông thường khi chưa có bằng chứng rõ về việc hét/gọi lớn tiếng.
- Bubble nhọn, `shout_hint`, chữ hoa, dấu chấm than, giận dữ, bất ngờ, gấp gáp và câu đệm không tự chứng minh shout, kể cả kết hợp nhiều dấu hiệu yếu.
- Chỉ chọn shout khi có cue về cách phát âm/độ lớn rõ trong nội dung, ngữ cảnh hoặc ảnh thực sự được cung cấp; evidence phải chỉ ra cue đó.
- Không áp quota cố định hoặc đổi nhãn một tỷ lệ câu ngẫu nhiên. Thought/caption/whisper vẫn được xác định độc lập; thiếu thông tin về kiểu phát âm nói chung vẫn có thể là unknown.
- Đã bổ sung prompt vào V6 và policy legacy mới. V6 dùng policy `unified-analyze-v6.5-conservative-shout`; đường legacy mới dùng `semantic-speech-v2-conservative-shout`, giữ prompt v1 cho manifest v1.
- Chưa đo tần suất shout sau thay đổi; chưa có kết luận thực nghiệm về mức giảm.

Phần bổ sung model/prompt là thay đổi chuẩn bị source/config theo yêu cầu trực tiếp. Ghi chú chưa triển khai hai nhánh ở bản trước được thay thế bởi cập nhật bên dưới.

## Cập nhật triển khai source — 14/10/2026

Theo yêu cầu tiếp tục của người dùng, đường chạy mới đã tích hợp `dual-branch-v1` vào normalize, analyze, export, translation và verifier. Policy mới là `dual-branch-analyze-v6.1-conservative-shout`; không tái sử dụng checkpoint của unified analyze. Caption/dialogue chạy độc lập, có lượt tiến/ngược và chỉ hợp nhất kết quả cuối theo thứ tự nguồn. Neo MAGI được khóa theo đúng source text; normalization tách nhóm có neo mâu thuẫn. Scene được lập từ dialogue, caption nhận scene theo vị trí; khoảng cách caption tính bao gồm hai đầu theo essential index.

Scheduler đã có story workers, branch workers và admission GPU dùng chung. Scene có alias mới vẫn chạy theo các wave có thứ tự; các scene độc lập có thể song song. Snapshot và inference memory không chia sẻ giữa hai nhánh; chỉ alias đã chốt được truyền sang scene kế tiếp. Xem [bàn giao triển khai và giới hạn](../v6/deployment/dual-branch-parallel-handoff-vi.md).

Đây là trạng thái tích hợp source và kiểm tra tĩnh, không phải chứng nhận chất lượng hay hiệu năng. Chưa chạy test, bootstrap, model, pipeline hoặc Runpod cho thay đổi này. Các kế hoạch/config đã freeze trước đó phải chuẩn bị mới; outputs cũ giữ nguyên.
